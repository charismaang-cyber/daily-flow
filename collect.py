# -*- coding: utf-8 -*-
"""오늘의 흐름 — 시세(야후 파이낸스)·구글 트렌드·유튜브 인기 영상·이슈(issues.py: 네이트 키워드·많이 본 뉴스·
   커뮤니티 인기글)를 한 번 받아 data.json(보관용)과 data.js(화면이 읽는 것)에 쓰고, 때가 되면 채널 레이더(radar.py)도 돌린다.
   검색어는 받을 때마다 '24시간 모음'에 보태서, 한 번에 10개만 주는 구글·네이트도 하루치를 볼 수 있다.
   설정은 config.json. 깃허브 Actions 가 30분마다 부른다(.github/workflows/collect.yml).
   유튜브 키: 환경변수 YOUTUBE_API_KEY → 없으면 youtube_key.txt (저장소에 안 올림)
   사용: python collect.py [--out 폴더] [--all]
         --out  자료를 쓸 폴더 (기본: 이 폴더)   --all  이슈·레이더도 지금 받기"""
import os, sys, json, time, warnings, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

warnings.filterwarnings("ignore")
if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
KST = timezone(timedelta(hours=9))
CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
OUT = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else HERE
os.makedirs(OUT, exist_ok=True)
DATA_PATH = os.path.join(OUT, "data.json")
JS_PATH = os.path.join(OUT, "data.js")
LOG_PATH = None if os.environ.get("GITHUB_ACTIONS") else os.path.join(HERE, "collect.log")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

def youtube_key():
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key and os.path.exists(os.path.join(HERE, "youtube_key.txt")):
        key = open(os.path.join(HERE, "youtube_key.txt"), encoding="utf-8").read().strip()
    return key

def load_data():
    try:
        return json.load(open(DATA_PATH, encoding="utf-8"))
    except Exception:
        return {}

DATA = load_data()

def save():
    text = json.dumps(DATA, ensure_ascii=False)
    for path, body in ((DATA_PATH, text), (JS_PATH, "window.DASH_DATA = " + text + ";\n")):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp, path)

def now_str():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

def log(msg):
    line = f"[{now_str()}] {msg}"
    if sys.stdout:
        print(line)
    if not LOG_PATH:
        return
    try:  # pythonw 는 창이 없으니 파일에 남긴다 (최근 300줄만)
        old = open(LOG_PATH, encoding="utf-8").read().splitlines()[-299:] if os.path.exists(LOG_PATH) else []
        open(LOG_PATH, "w", encoding="utf-8").write("\n".join(old + [line]) + "\n")
    except Exception:
        pass

def get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

# ── 시세 ──────────────────────────────────────────────
def exch_tz(t):
    """일봉 날짜가 어느 시간대 기준인지 (장중 값이 '새 날'인지 가를 때 씀)"""
    if t.endswith((".KS", ".KQ")) or t in ("^KS11", "^KQ11"):
        return "Asia/Seoul"
    if t.endswith("=X") or t.endswith("-USD"):
        return "UTC"
    return "America/New_York"

def fetch_markets():
    import yfinance as yf
    warnings.simplefilter("ignore")  # yfinance 가 내는 pandas 경고 숨김
    tickers = [it["t"] for g in CFG["groups"] for it in g["items"]]
    df = yf.download(tickers, period="1mo", interval="1d", progress=False,
                     auto_adjust=False, threads=True)["Close"]
    # 야후 일봉은 하루쯤 늦게 채워질 때가 있어(코스피 등) 30분봉 마지막 값으로 보탠다
    try:
        intra = yf.download(tickers, period="5d", interval="30m", progress=False,
                            auto_adjust=False, threads=True)["Close"]
    except Exception:
        intra = None
    groups = []
    for g in CFG["groups"]:
        items = []
        for it in g["items"]:
            row = {k: v for k, v in it.items()}
            try:
                s = df[it["t"]].dropna()
                vals = [float(x) for x in s.tolist()]
                last_day = s.index[-1].date()
                if intra is not None and it["t"] in intra:
                    si = intra[it["t"]].dropna()
                    if len(si):
                        iday = si.index[-1].tz_convert(exch_tz(it["t"])).date()
                        if iday > last_day:
                            vals.append(float(si.iloc[-1])); last_day = iday
                        elif iday == last_day:
                            vals[-1] = float(si.iloc[-1])
                if it.get("inv"):  # KRWCNY 처럼 거꾸로 된 쌍은 뒤집어 '원/외화' 로
                    vals = [1 / v for v in vals]
                closes = [round(v * it.get("mul", 1), 4) for v in vals]
                row["price"] = closes[-1]
                row["prev"] = closes[-2] if len(closes) > 1 else None
                row["date"] = last_day.strftime("%m-%d")
                row["spark"] = closes[-22:]
            except Exception as e:
                row["error"] = str(e)[:80]
            items.append(row)
        groups.append({"id": g["id"], "title": g["title"], "en": g["en"], "items": items})
    DATA["markets"] = groups
    DATA["markets_at"] = now_str()

# ── 24시간 모음 ───────────────────────────────────────
def roll(store, items, key, now, top=()):
    """지금 목록을 모음에 보탠다 — f 처음 본 때, l 마지막 본 때(분), n 본 횟수, best 가장 높은 순위,
       top 에 적은 칸은 가장 큰 값을 남긴다. 24시간 동안 다시 안 보이면 뺀다."""
    for i, it in enumerate(items):
        old = store.get(it[key], {})
        e = dict(old, **it)
        e.update(f=old.get("f", now), l=now, n=old.get("n", 0) + 1, best=min(old.get("best", 999), i + 1))
        for k in top:
            e[k] = max(old.get(k) or 0, it.get(k) or 0)
        store[it[key]] = e
    return {k: e for k, e in store.items() if now - e["l"] <= 1440}

# ── 구글 트렌드 ────────────────────────────────────────
NS = {"ht": "https://trends.google.com/trending/rss"}

def fetch_trends():
    out, day = {}, DATA.setdefault("trend_day", {})
    now = int(time.time() // 60)
    for geo, label in CFG["trend_geos"]:
        root = ET.fromstring(get(f"https://trends.google.com/trending/rss?geo={geo}"))
        items = []
        for it in root.iter("item"):
            news = [{"title": n.findtext("ht:news_item_title", "", NS),
                     "url": n.findtext("ht:news_item_url", "", NS),
                     "source": n.findtext("ht:news_item_source", "", NS)}
                    for n in it.findall("ht:news_item", NS)]
            pub = it.findtext("pubDate", "")
            try:
                pub = parsedate_to_datetime(pub).astimezone(KST).strftime("%m-%d %H:%M")
            except Exception:
                pass
            traffic = it.findtext("ht:approx_traffic", "", NS)
            items.append({"title": it.findtext("title", ""), "traffic": traffic,
                          "tn": int("".join(ch for ch in traffic if ch.isdigit()) or 0),
                          "picture": it.findtext("ht:picture", "", NS),
                          "pub": pub, "news": news[:3]})
        out[geo] = {"label": label, "items": items}
        # 모음에는 기사 하나만 (자료가 너무 커지지 않게)
        slim = [dict(it, news=it["news"][:1], nn=len(it["news"])) for it in items]
        day[geo] = roll(day.get(geo, {}), slim, "title", now, top=("tn",))
    DATA["trends"] = out
    DATA["trends_at"] = now_str()

# ── 유튜브 인기 급상승 ──────────────────────────────────
def fetch_youtube():
    """한국 인기 동영상 — 전체와 분야별로 50위씩 (한 번에 15유닛쯤, 하루 약 720)"""
    key = youtube_key()
    if not key:
        DATA["youtube"] = {"error": "유튜브 키가 없어요 (깃허브 Secrets 의 YOUTUBE_API_KEY)"}
        return
    import radar
    items, lists = {}, {}
    for cat in radar.CATS:
        p = dict(part="snippet,statistics,contentDetails", chart="mostPopular", regionCode="KR",
                 maxResults=50, key=key)
        if cat != "0":
            p["videoCategoryId"] = cat
        try:
            j = radar.yt("videos", **p)
        except RuntimeError as e:
            if cat == "0" or str(e) in ("quotaExceeded", "dailyLimitExceeded"):
                raise
            continue  # 한국에 없는 분야
        vids = [v for v in j.get("items", []) if not v["snippet"]["channelTitle"].endswith(" - Topic")]  # 음악 자동 채널
        for v in vids:
            sn, s = v["snippet"], v.get("statistics", {})
            items[v["id"]] = {"t": sn["title"], "ch": sn["channelTitle"], "v": int(s.get("viewCount", 0)),
                              "pub": sn["publishedAt"],
                              "dur": radar.iso_dur(v.get("contentDetails", {}).get("duration"))}
        if vids:
            lists[cat] = [v["id"] for v in vids]
    DATA["youtube"] = {"items": items, "lists": lists, "cats": {c: radar.CATS[c] for c in lists}, "at": now_str()}

# ── 네이트 키워드·많이 본 뉴스·커뮤니티 ─────────────────────
def fetch_issues():
    import issues
    now = int(time.time() // 60)
    iss = DATA.setdefault("issues", {})
    errs = iss["errors"] = {}
    def grab(name, fn):
        try:
            v = fn()
            if not v:
                raise RuntimeError("글을 못 찾았어요 (사이트 모양이 바뀐 듯)")
            return v
        except Exception as e:
            errs[name] = str(e)[:120]
            log(f"issues {name} 실패: {e}")
    kw = grab("nate_kw", issues.nate_keywords)
    if kw:
        iss["nate_kw"] = kw
        iss["nate_day"] = roll(iss.get("nate_day", {}), kw, "kw", now)
    for name, fn in (("nate_news", issues.nate_news), ("naver_news", issues.naver_news)):
        v = grab(name, fn)
        if v:
            iss[name] = v
    comm = iss.setdefault("comm", {})
    for cid, label, url, fn in issues.COMMUNITIES:
        v = grab(cid, fn)
        c = comm.setdefault(cid, {"items": []})
        c.update(label=label, url=url)
        if v:  # 막히면 지난번 것을 그대로 둔다
            c.update(items=v[:50], at=now_str())
    iss["at"] = now_str()

# ── 한 번 받기 ────────────────────────────────────────
def run(name, fn):
    try:
        fn()
        DATA.setdefault("errors", {}).pop(name, None)
        log(f"{name} 갱신")
    except Exception as e:
        DATA.setdefault("errors", {})[name] = str(e)[:200]
        log(f"{name} 실패: {e}")

def trends_due():
    try:
        last = datetime.strptime(DATA["trends_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
        return datetime.now(KST) - last >= timedelta(minutes=CFG["trend_minutes"] - 1)
    except Exception:
        return True

if __name__ == "__main__":
    run("markets", fetch_markets)
    if "--all" in sys.argv or trends_due() or "issues" not in DATA:  # 새 칸이 아직 없으면 바로 받기
        run("trends", fetch_trends)
        run("youtube", fetch_youtube)
        run("issues", fetch_issues)
    save()
    if youtube_key():
        import radar
        try:
            radar.collect(OUT, youtube_key(), CFG["radar"], log, force="--all" in sys.argv)
        except Exception as e:
            log(f"radar 실패: {e}")
