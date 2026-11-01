# -*- coding: utf-8 -*-
"""채널 레이더 — 유튜브 분야별 인기 영상에서 채널을 모아(pool) snap_hours 마다 총조회수·구독자를 적고,
   24시간·7일 증가량으로 순위를 매겨 radar/radar.js 에 쓴다. collect.py 가 부른다.
   유튜브는 '지금 값'만 주므로 기록이 쌓여야 순위가 생긴다:
     일간 = 12~48시간 떨어진 기록과 비교해 24시간치로 환산, 주간 = 4~10일 떨어진 기록으로 7일치 환산.
   유닛: 인기 영상(분야 15 × 지역 2) ≈ 30 + 채널 50개당 1 → 15,000채널이면 한 번에 약 330
   채널 모으기는 두 길: ① 분야별 인기 영상(키) ② 주제 낱말로 '이번 주 조회수순' 검색을 긁기(키 안 씀, ytscrape.py, 30분마다 몇 개씩).
   화면에는 지역마다 눈여겨볼 채널만 radar.js 에, 찾기·즐겨찾기용 전체 목록은 all.js 에 따로 쓴다."""
import os, re, json, time, gzip, urllib.request, urllib.parse, urllib.error
from collections import Counter
from datetime import datetime, timezone, timedelta
import ytscrape

KST = timezone(timedelta(hours=9))
API = "https://www.googleapis.com/youtube/v3/"
CATS = {"0": "전체", "1": "영화·애니", "2": "자동차", "10": "음악", "15": "동물", "17": "스포츠",
        "19": "여행", "20": "게임", "22": "일상·인물", "23": "코미디", "24": "엔터",
        "25": "뉴스·정치", "26": "노하우·스타일", "27": "교육", "28": "과학·기술"}
DAY = 1440  # 분

def yt(endpoint, **params):
    url = API + endpoint + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            reason = json.loads(e.read())["error"]["errors"][0]["reason"]
        except Exception:
            reason = str(e.code)
        raise RuntimeError(reason)

def iso_dur(s):
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return None
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se

def kst(m):
    return datetime.fromtimestamp(m * 60, KST)

# ── 제목 낱말 (주간 키워드) ──────────────────────────────
JOSA = sorted("에서는 으로는 이라고 에서 으로 까지 부터 에게 한테 처럼 보다 이랑 하고 은 는 이 가 을 를 의 에 와 과 도 로 만 랑".split(),
              key=len, reverse=True)
STOP = set("""the a an and or of to in on for with from by at is are be my your our you me we it this that vs feat ft
official video mv m/v ep part full new live shorts short trailer teaser 4k hd episode season highlights highlight
he she his her him they them their was were been has have had do does did not no all can will would just get got
out up down so what when who why how which there here than then into over about after before more most very only
one two its it's i'm don't can't won't if but as off back day vs. ever every first last best top make made
진짜 그리고 이번 오늘 결국 모든 정말 너무 하는 있는 없는 그냥 이거 그거 저거 우리 나의 내가 제가 이제 지금 대한 위한 통해
가장 다시 모두 무슨 어떤 그럼 이런 저런 그런 했다 한다 하면 하기 된다 됐다 있다 없다 같은 이유 공식 영상 채널 구독 최초
공개 직캠 풀버전 하이라이트 예고편 티저 쇼츠""".split())

def tokens(title):
    out = set()
    for w in re.findall(r"[가-힣]+|[A-Za-z][A-Za-z0-9'&-]*", title):
        if "가" <= w[0] <= "힣":
            for j in JOSA:
                if w.endswith(j) and len(w) - len(j) >= 2:
                    w = w[:-len(j)]
                    break
            if len(w) < 2:
                continue
        else:
            w = w if w.isupper() else w.lower()
            if len(w) < 2:
                continue
        if w.lower() not in STOP:
            out.add(w)
    return out

# ── 기록 계산 ─────────────────────────────────────────
def gain(h, target, lo, hi, per):
    """마지막 기록과 lo~hi 분 떨어진 기록 중 target 에 가장 가까운 것으로 per 분당 증가량"""
    last = h[-1]
    cands = [e for e in h[:-1] if lo <= last[0] - e[0] <= hi]
    if not cands:
        return None, None
    e = min(cands, key=lambda e: abs(last[0] - e[0] - target))
    span = last[0] - e[0]
    dv = round((last[1] - e[1]) * per / span)
    ds = round((last[2] - e[2]) * per / span) if last[2] >= 0 and e[2] >= 0 else None
    return dv, ds

def daily_points(h):
    """하루(한국 날짜)마다 마지막 기록 하나씩"""
    by = {}
    for e in h:
        by[kst(e[0]).date()] = e
    return [by[d] for d in sorted(by)]

def series(h, days=14):
    pts = daily_points(h)[-(days + 1):]
    return [round((b[1] - a[1]) * DAY / max(b[0] - a[0], 1)) for a, b in zip(pts, pts[1:])]

def compact(h, now):
    """48시간 안은 모두, 그 전은 하루 하나, 30일 넘은 건 버림 (채널이 많아져 기록 파일이 커지지 않게)"""
    recent = [e for e in h if now - e[0] <= 2 * DAY]
    old = [e for e in h if 2 * DAY < now - e[0] <= 30 * DAY]
    return daily_points(old) + recent

def grade(d1, subs):
    if d1 is None:
        return None
    r = d1 / max(subs if subs and subs > 0 else 0, 1000)
    for g, cut in (("S", 1), ("A", .3), ("B", .1), ("C", .03), ("D", .01)):
        if r >= cut:
            return g
    return "F"

# ── 기록 파일 (채널이 수만이라 gzip) ────────────────────────
def load_state(rdir):
    gz, old = os.path.join(rdir, "state.json.gz"), os.path.join(rdir, "state.json")
    if os.path.exists(gz):
        with gzip.open(gz, "rt", encoding="utf-8") as f:
            return json.load(f)
    return json.load(open(old, encoding="utf-8")) if os.path.exists(old) else {}

def write(path, body, gz=False):
    with (gzip.open(path + ".tmp", "wt", encoding="utf-8") if gz else open(path + ".tmp", "w", encoding="utf-8")) as f:
        f.write(body)
    os.replace(path + ".tmp", path)

def save_state(rdir, st):
    write(os.path.join(rdir, "state.json.gz"), json.dumps(st, ensure_ascii=False, separators=(",", ":")), gz=True)
    old = os.path.join(rdir, "state.json")
    if os.path.exists(old):
        os.remove(old)  # 예전 형식

# ── 키 없이 채널 찾기 ──────────────────────────────────
def weekly_words(st, reg, n):
    """최근 7일 인기 영상 제목에 2번 넘게 나온 낱말 — 정해 둔 주제 낱말 말고도 그때그때 뜨는 말로 찾게"""
    cnt = Counter()
    for d in sorted(st["kw"])[-7:]:
        for _, toks in st["kw"][d].get(reg, {}).values():
            cnt.update(toks)
    return [w for w, c in cnt.most_common(n) if c >= 2]

def discover(st, fc, regions, now, log):
    """주제 낱말마다 '이번 주 조회수순'(전체·4분 미만) 검색 → 조회수가 넘는 영상의 채널을 pool 에 넣고 영상은 hits 에 남긴다"""
    f, today = st["find"], kst(now).strftime("%Y-%m-%d")
    added = f["added"] = {d: n for d, n in f.get("added", {}).items() if d >= kst(now - 3 * DAY).strftime("%Y-%m-%d")}
    cur = f.setdefault("cur", {})
    fails = new = seen = 0
    for reg in regions:
        words = list(dict.fromkeys(fc["keywords"].get(reg, []) + weekly_words(st, reg, fc.get("auto_words", 30))))
        if not words:
            continue
        i, n = cur.get(reg, 0) % len(words), fc["per_round"].get(reg, 0)
        for q in [words[(i + j) % len(words)] for j in range(min(n, len(words)))]:
            for short in (False, True):
                time.sleep(fc.get("sleep", 1.2))
                try:
                    hits = ytscrape.week_hits(q, reg, short)
                    fails = 0
                except ytscrape.Blocked as e:
                    fails += 1
                    if fails >= 3:
                        f["blocked"] = now
                        log(f"radar 찾기 막힘({e}) — 다음 회차에 다시")
                        return
                    continue
                except Exception:
                    continue
                for h in hits:
                    if h["v"] < fc["min_views"].get(reg, 10000) or not h["pub"]:
                        continue
                    seen += 1
                    p = st["pool"].get(h["cid"])
                    if p is None:
                        if added.get(today, 0) >= fc["add_per_day"]:
                            continue
                        p = st["pool"][h["cid"]] = {"reg": reg, "add": now, "src": "검색"}
                        added[today] = added.get(today, 0) + 1
                        new += 1
                    qs, tk = p.setdefault("q", []), p.setdefault("k", [])
                    if q not in qs:
                        qs.append(q); del qs[:-5]
                    for w in sorted(tokens(h["t"])):
                        if w not in tk:
                            tk.append(w)
                    del tk[:-12]
                    old = st["hits"].get(h["id"], {})
                    st["hits"][h["id"]] = {"cid": h["cid"], "t": h["t"], "ch": h["ch"], "v": max(h["v"], old.get("v", 0)),
                                           "pub": old.get("pub") or h["pub"], "dur": h["dur"], "reg": old.get("reg", reg)}
        cur[reg] = (i + n) % len(words)
    cut = kst(now - 8 * DAY).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    hits = sorted(((k, h) for k, h in st["hits"].items() if h["pub"] >= cut), key=lambda kv: -kv[1]["v"])
    st["hits"] = dict(hits[:fc.get("hits_keep", 20000)])
    f["at"] = now
    log(f"radar 찾기: 터진 영상 {seen}개, 새 채널 {new}개 (오늘 {added.get(today, 0)}/{fc['add_per_day']})")

# ── 한 번 돌기 ────────────────────────────────────────
def collect(out, key, cfg, log, force=False):
    rdir = os.path.join(out, "radar")
    os.makedirs(rdir, exist_ok=True)
    st = load_state(rdir)
    for k in ("pool", "hist", "kw", "units", "hits", "find"):
        st.setdefault(k, {})
    now = int(time.time() // 60)
    if cfg.get("find"):
        try:
            discover(st, cfg["find"], [r for r, _ in cfg["regions"]], now, log)
        except Exception as e:
            log(f"radar 찾기 실패: {e}")
    if not force and st.get("last_snap") and now - st["last_snap"] < cfg["snap_hours"] * 60 - 15:
        save_state(rdir, st)
        return
    regions = [r for r, _ in cfg["regions"]]
    today = kst(now).strftime("%Y-%m-%d")
    used = 0

    # 1) 분야별 인기 영상 → 채널 모으기
    videos = {}
    for reg in regions:
        vids = {}
        for cat in CATS:
            p = dict(part="snippet,statistics,contentDetails", chart="mostPopular",
                     regionCode=reg, maxResults=50, key=key)
            if cat != "0":
                p["videoCategoryId"] = cat
            used += 1
            try:
                j = yt("videos", **p)
            except RuntimeError as e:
                if str(e) in ("quotaExceeded", "dailyLimitExceeded"):
                    raise
                continue  # 그 지역에 없는 분야는 404
            for v in j.get("items", []):
                sn, s = v["snippet"], v.get("statistics", {})
                if sn["channelTitle"].endswith(" - Topic"):
                    continue  # 음악 자동 채널(구독자 수십 명)이라 배수가 터무니없이 커진다
                vids[v["id"]] = {"id": v["id"], "t": sn["title"], "ch": sn["channelTitle"],
                                 "cid": sn["channelId"], "cat": sn.get("categoryId", ""),
                                 "th": sn["thumbnails"].get("medium", {}).get("url", ""),
                                 "v": int(s.get("viewCount", 0)),
                                 "lk": int(s["likeCount"]) if "likeCount" in s else None,
                                 "cm": int(s["commentCount"]) if "commentCount" in s else None,
                                 "pub": sn["publishedAt"],
                                 "dur": iso_dur(v.get("contentDetails", {}).get("duration"))}
                if sn["channelId"] not in st["pool"]:
                    st["pool"][sn["channelId"]] = {"reg": reg, "add": now, "src": "인기"}
                p = st["pool"].get(sn["channelId"])
                if p is not None and sn.get("categoryId") and sn["categoryId"] not in p.setdefault("cats", []):
                    p["cats"].append(sn["categoryId"])  # 이 채널이 인기에 오른 분야들
        videos[reg] = vids
        day = st["kw"].setdefault(today, {}).setdefault(reg, {})
        for v in vids.values():
            day[v["id"]] = [v["v"], sorted(tokens(v["t"]))]
    for cid in cfg.get("seed_channels", []):
        st["pool"].setdefault(cid, {"reg": regions[0], "add": now, "src": "직접"})

    # 2) 채널 기록 (50개씩 1유닛)
    ids, seen = list(st["pool"]), set()
    for i in range(0, len(ids), 50):
        used += 1
        j = yt("channels", part="snippet,statistics", id=",".join(ids[i:i + 50]), maxResults=50, key=key)
        for c in j.get("items", []):
            sn, s = c["snippet"], c.get("statistics", {})
            p = st["pool"][c["id"]]
            p.update(t=sn["title"], th=sn["thumbnails"].get("default", {}).get("url", ""),
                     cu=sn.get("customUrl", ""), c=sn.get("country", ""), pub=sn.get("publishedAt", "")[:10])
            subs = -1 if s.get("hiddenSubscriberCount") else int(s.get("subscriberCount", 0))
            h = st["hist"].setdefault(c["id"], [])
            h.append([now, int(s.get("viewCount", 0)), subs, int(s.get("videoCount", 0))])
            st["hist"][c["id"]] = compact(h, now)
            seen.add(c["id"])
    for cid in [c for c, p in st["pool"].items() if c not in seen or p.get("t", "").endswith(" - Topic")]:  # 지워진·자동 채널
        st["pool"].pop(cid, None); st["hist"].pop(cid, None)

    # 3) 너무 많으면 3일 넘게 지켜봐도 덜 크는 채널부터 뺀다 — 조회수 증가로도, 구독자 대비 증가로도 뒤처진 채널
    #    (작은 채널이 빨리 크는 것도 남기려고 둘 중 나은 쪽 순위로 본다)
    over = len(st["pool"]) - cfg["max_channels"]
    if over > 0:
        cands = [c for c, p in st["pool"].items() if p.get("src") != "직접" and now - p["add"] > 3 * DAY and c in st["hist"]]
        ab = {}
        for c in cands:
            h = st["hist"][c]
            ab[c] = (h[-1][1] - h[0][1]) / max(h[-1][0] - h[0][0], 1)
        ra = {c: ab[c] / max(st["hist"][c][-1][2], 1000) for c in cands}
        pct = lambda d: {c: i / max(len(d), 1) for i, c in enumerate(sorted(d, key=d.get))}
        pa, pr = pct(ab), pct(ra)
        cands.sort(key=lambda c: max(pa[c], pr[c]))
        for cid in cands[:over]:
            st["pool"].pop(cid); st["hist"].pop(cid, None)

    st["last_snap"] = now
    st.setdefault("first_snap", now)
    st["units"][today] = st["units"].get(today, 0) + used
    st["units"] = dict(sorted(st["units"].items())[-10:])
    st["kw"] = dict(sorted(st["kw"].items())[-8:])

    # 4) 화면에 줄 것
    fc = cfg.get("find") or {}
    half = fc.get("show_top", 1200) // 2
    subs_of = {c: h[-1][2] for c, h in st["hist"].items()}
    regs, every = {}, []
    for reg, label in cfg["regions"]:
        chans = []
        for cid, p in st["pool"].items():
            if p["reg"] != reg or cid not in st["hist"]:
                continue
            h = st["hist"][cid]
            d1, s1 = gain(h, DAY, DAY // 2, 2 * DAY, DAY)
            w1, sw1 = gain(h, 7 * DAY, 4 * DAY, 10 * DAY, 7 * DAY)
            chans.append({"id": cid, "t": p.get("t", ""), "th": p.get("th", ""), "cu": p.get("cu", ""),
                          "c": p.get("c", ""), "pub": p.get("pub", ""), "src": p.get("src", ""),
                          "cats": p.get("cats", []), "k": " ".join(p.get("q", []) + p.get("k", [])),
                          "subs": h[-1][2], "views": h[-1][1], "n": h[-1][3],
                          "d1": d1, "s1": s1, "w1": w1, "sw1": sw1,
                          "g": grade(d1, h[-1][2]), "ser": series(h)})
        every += [dict(c, reg=reg) for c in chans]
        # 다 보내면 무거워서, 일간·주간·구독자·구독자 대비 증가 순위마다 위쪽만 (찾기·즐겨찾기는 all.js)
        keep = {c["id"] for c in chans if c["src"] == "직접"}
        for key in (lambda c: c["d1"], lambda c: c["w1"], lambda c: c["subs"],
                    lambda c: c["d1"] / max(c["subs"], 1000) if c["d1"] is not None else None):
            keep.update(c["id"] for c in sorted((c for c in chans if key(c) is not None), key=key, reverse=True)[:half])
        n_all, chans = len(chans), [{k: v for k, v in c.items() if k != "k"} for c in chans if c["id"] in keep]
        vids = list(videos.get(reg, {}).values())
        # 검색으로 찾은 이번 주 영상 — 구독자 대비 많이 본 것 위주로
        found = [{"id": vid, "t": h["t"], "ch": h["ch"], "cid": h["cid"], "cat": "",
                  "th": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg", "v": h["v"], "lk": None, "cm": None,
                  "pub": h["pub"], "dur": h["dur"], "src": "검색"}
                 for vid, h in st["hits"].items() if h["reg"] == reg and vid not in videos.get(reg, {})]
        rx = lambda v: v["v"] / subs_of[v["cid"]] if subs_of.get(v["cid"], 0) > 0 else 0
        per = Counter()
        for v in sorted(found, key=lambda v: (-rx(v), -v["v"])):
            if per[v["cid"]] < 3 and sum(per.values()) < fc.get("hits_show", 800):  # 한 채널이 줄줄이 차지하지 않게
                per[v["cid"]] += 1
                vids.append(v)
        vids.sort(key=lambda v: -v["v"])
        for v in vids:
            sb = subs_of.get(v["cid"])
            v["subs"] = sb
            v["x"] = round(v["v"] / sb, 1) if sb and sb > 0 else None
        # 최근 7일 인기 영상 제목 낱말: 낱말마다 영상 수·조회수 합
        week = sorted(st["kw"])[-7:]
        latest = {}
        for d in week:
            latest.update(st["kw"][d].get(reg, {}))
        cnt = {}
        for vid, (views, toks) in latest.items():
            for w in toks:
                c = cnt.setdefault(w, [0, 0])
                c[0] += 1; c[1] += views
        kw = sorted(([w, n, vw] for w, (n, vw) in cnt.items() if n >= 2), key=lambda x: (-x[1], -x[2]))[:60]
        regs[reg] = {"label": label, "channels": chans, "n_all": n_all, "videos": vids, "kw": kw,
                     "kw_days": len(week), "kw_videos": len(latest)}

    fm = lambda m: kst(m).strftime("%Y-%m-%d %H:%M")
    radar = {"at": fm(now), "first": fm(st["first_snap"]), "daily_from": fm(st["first_snap"] + DAY // 2),
             "weekly_from": fm(st["first_snap"] + 4 * DAY), "units_today": st["units"][today],
             "pool": len(st["pool"]), "by_src": dict(Counter(p.get("src", "") for p in st["pool"].values())),
             "find_at": fm(st["find"]["at"]) if st["find"].get("at") else None,
             "find_blocked": fm(st["find"]["blocked"]) if st["find"].get("blocked") else None,
             "cats": CATS, "regions": regs}
    F = ["id", "t", "th", "cu", "c", "pub", "src", "cats", "k", "subs", "views", "n", "d1", "s1", "w1", "sw1", "g", "reg"]
    allc = {"f": F, "rows": [[c[k] for k in F] for c in every]}
    js = lambda name, obj: f"window.{name} = " + json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + ";\n"
    save_state(rdir, st)
    write(os.path.join(rdir, "radar.js"), js("RADAR", radar))
    write(os.path.join(rdir, "all.js"), js("RADAR_ALL", allc))
    log(f"radar 갱신 (채널 {len(st['pool'])}개, 이번 {used}유닛)")
