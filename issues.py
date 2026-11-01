# -*- coding: utf-8 -*-
"""오늘의 이슈 덧붙이기 — 네이트 실시간 이슈 키워드, 많이 본 뉴스(네이트 전체 순위·네이버 언론사별),
   커뮤니티 인기글(네이트판·더쿠·디시 실베·보배드림·루리웹·오유) 제목과 링크만 받는다. collect.py 가 부른다.
   사이트 모양이 바뀌거나 막히면 그 칸만 비고 나머지는 그대로 받는다(칸마다 error 에 까닭)."""
import re, json, html, time, urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0 Safari/537.36", "Accept-Language": "ko-KR,ko;q=0.9"}

def page(url, enc="utf-8"):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode(enc, "replace")

def text(s):
    """태그 빼고 &quot; 같은 것 풀고 띄어쓰기 하나로"""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()

def num(s):
    s = re.sub(r"[^\d]", "", s or "")
    return int(s) if s else None

def find(pat, s, default=""):
    m = re.search(pat, s, re.S)
    return m.group(1) if m else default

# ── 네이트 실시간 이슈 키워드 (10개) ─────────────────────
def nate_keywords():
    raw = page(f"https://www.nate.com/js/data/jsonLiveKeywordDataV1.js?v={int(time.time())}", "cp949")
    out = []
    for r, kw, st, ch, *_ in json.loads(raw):
        out.append({"r": int(r), "kw": kw.strip(), "st": {"+": "up", "-": "down", "n": "new"}.get(st, "same"),
                    "ch": int(ch or 0)})
    if not out:
        raise RuntimeError("키워드가 비어 있어요")
    return out

# ── 많이 본 뉴스 ──────────────────────────────────────
def nate_news():
    """네이트 많이 본 뉴스 — 모든 언론사를 합친 오늘 순위 1~50위"""
    t = page("https://news.nate.com/rank/interest?sc=all&p=day", "cp949")
    out = []
    for ch in t.split('class="mduRank rank')[1:]:
        r = num(find(r"^(\d+)", ch))
        u = find(r'href="(//news\.nate\.com/view/[^"?]+)', ch)
        tt = text(find(r"<h2[^>]*>(.*?)</h2>", ch))
        if not (r and u and tt):
            continue
        mv = "up" if 'class="up"' in ch[:400] else "down" if 'class="down"' in ch[:400] else \
             "new" if 'class="new"' in ch[:400] else "same"
        out.append({"r": r, "t": tt, "u": "https:" + u,
                    "src": text(find(r'<span class="medium">(.*?)(?:<em>|</span>)', ch)), "mv": mv})
    if not out:
        raise RuntimeError("순위를 못 찾았어요 (사이트 모양이 바뀐 듯)")
    return sorted(out, key=lambda x: x["r"])[:50]

def naver_news():
    """네이버 랭킹 — 네이버는 언론사마다 많이 본 순서만 준다(전체 순위 없음). 언론사마다 1~3위"""
    t = page("https://news.naver.com/main/ranking/popularDay.naver", "cp949")
    out = []
    for box in t.split('class="rankingnews_box"')[1:]:
        press = text(find(r'<strong class="rankingnews_name">(.*?)</strong>', box))
        items = []
        for li in box.split("<li")[1:4]:
            u = find(r'href="(https://n\.news\.naver\.com/article/[^"?]+)', li)
            tt = text(find(r'class="list_title[^>]*>(.*?)</a>', li))
            if u and tt:
                items.append({"t": tt, "u": u, "tm": text(find(r'class="list_time[^>]*>(.*?)<', li))})
        if press and items:
            out.append({"press": press, "items": items})
    if not out:
        raise RuntimeError("랭킹을 못 찾았어요 (사이트 모양이 바뀐 듯)")
    return out

# ── 커뮤니티 인기글 ────────────────────────────────────
def pann():
    t = page("https://pann.nate.com/talk/ranking")
    out = []
    for ch in t.split('<div class="rankNum">')[1:]:
        m = re.search(r'<h2><a href="(/talk/\d+)"[^>]*title="([^"]*)"', ch)
        if not m:
            continue
        out.append({"t": text(m.group(2)), "u": "https://pann.nate.com" + m.group(1),
                    "c": num(find(r'class="reple-num">\((\d+)\)', ch)),
                    "v": num(find(r'조회 ([\d,]+)', ch)), "rc": num(find(r'추천 ([\d,]+)', ch))})
    return out

def theqoo():
    t = page("https://theqoo.net/hot")
    out = []
    for cls, row in re.findall(r'<tr(?: class="([^"]*)")?[^>]*>(.*?)</tr>', t, re.S):
        if "notice" in (cls or ""):
            continue
        m = re.search(r'<a href="(/hot/\d+)">(.*?)</a>', row, re.S)
        if not m:
            continue
        out.append({"t": text(m.group(2)), "u": "https://theqoo.net" + m.group(1),
                    "cat": text(find(r'<td class="cate"[^>]*>(.*?)</td>', row)),
                    "c": num(find(r'class="replyNum">([\d,]+)', row)),
                    "v": num(find(r'<td class="m_no">([\d,]+)', row)),
                    "tm": text(find(r'<td class="time">(.*?)</td>', row))})
    return out

def dcbest():
    t = page("https://gall.dcinside.com/board/lists/?id=dcbest")
    out = []
    for row in t.split('<tr class="ub-content us-post')[1:]:
        row = row.split("</tr>")[0]
        no = find(r'data-no="(\d+)"', row)
        a = find(r'<td class="gall_tit[^"]*">\s*<a [^>]*>(.*?)</a>', row)
        tt = text(re.sub(r'<div class="thumimg">.*?</div>', "", a, flags=re.S))
        if not (no and tt):
            continue
        gal = find(r"^\[([^\]]+)\]\s*", tt)
        out.append({"t": re.sub(r"^\[[^\]]+\]\s*", "", tt), "cat": gal,
                    "u": f"https://gall.dcinside.com/board/view/?id=dcbest&no={no}",
                    "c": num(find(r'class="reply_num">\[(\d+)', row)),
                    "v": num(find(r'<td class="gall_count">([\d,]+)', row)),
                    "rc": num(find(r'<td class="gall_recommend">([\d,]+)', row)),
                    "tm": text(find(r'<td class="gall_date"[^>]*>(.*?)</td>', row))})
    return out

# 에펨코리아는 깃허브에서 받으면 늘 HTTP 430 으로 막아서 뺐다 (11-01)
def bobae():
    t = page("https://www.bobaedream.co.kr/list?code=best")
    out = []
    for row in t.split('<tr itemscope itemtype="http://schema.org/Article">')[1:]:
        row = row.split("</tr>")[0]
        m = re.search(r'<a class="bsubject"[^>]*href="(/view\?code=best&No=\d+)[^"]*"[^>]*title="([^"]*)"', row)
        if not m:
            continue
        out.append({"t": text(m.group(2)), "u": "https://www.bobaedream.co.kr" + m.group(1),
                    "cat": text(find(r'<td class="category" title="([^"]*)"', row)),
                    "c": num(find(r'class="totreply">(\d+)', row)),
                    "rc": num(find(r'<td class="recomm">.*?>(\d+)<', row)),  # 앞의 글자색 #ff7234 를 피해서
                    "v": num(find(r'<td class="count"[^>]*>([\d,]+)', row)),
                    "tm": text(find(r'<td class="date"[^>]*>(.*?)</td>', row))})
    return out

def todayhumor():
    t = page("https://www.todayhumor.co.kr/board/list.php?table=bestofbest")
    out = []
    for row in t.split('<tr class="view list_tr_')[1:]:
        row = row.split("</tr>")[0]
        m = re.search(r'<td class="subject"><a href="(/board/view\.php\?table=bestofbest&no=\d+)[^"]*"[^>]*>(.*?)</a>', row)
        if not m:
            continue
        out.append({"t": text(m.group(2)), "u": "https://www.todayhumor.co.kr" + m.group(1),
                    "c": num(find(r"list_memo_count_span'>\s*\[(\d+)\]", row)),
                    "rc": num(find(r'<td class="oknok">([\d,]+)', row)),
                    "v": num(find(r'<td class="hits">([\d,]+)', row)),
                    "tm": text(find(r'<td class="date">(.*?)</td>', row))[-5:]})
    return out

def ruliweb():
    t = page("https://bbs.ruliweb.com/best/humor_only?orderby=recommend&range=24h")
    out = []
    for row in t.split('<tr class="table_body blocktarget')[1:]:
        row = row.split("</tr>")[0]
        u = find(r'href="(https://bbs\.ruliweb\.com/best/board/\d+/read/\d+|/best/board/\d+/read/\d+)', row)
        tt = text(find(r'<(?:strong|span) class="text_over">(.*?)</(?:strong|span)>', row))
        if not (u and tt):
            continue
        out.append({"t": tt, "u": u if u.startswith("http") else "https://bbs.ruliweb.com" + u,
                    "c": num(find(r'class="num_reply[^"]*">\s*\((\d+)\)', row)),
                    "rc": num(find(r'<td class="recomd">\s*([\d,]+)', row)),
                    "v": num(find(r'<td class="hit">\s*([\d,]+)', row)),
                    "tm": text(find(r'<td class="time">(?:\s*<input[^>]*>)?(.*?)</td>', row))})
    return out

COMMUNITIES = [  # (id, 이름, 사이트 주소, 받는 함수)
    ("pann", "네이트판", "https://pann.nate.com/talk/ranking", pann),
    ("theqoo", "더쿠 HOT", "https://theqoo.net/hot", theqoo),
    ("dc", "디시 실베", "https://gall.dcinside.com/board/lists/?id=dcbest", dcbest),
    ("bobae", "보배드림 베스트", "https://www.bobaedream.co.kr/list?code=best", bobae),
    ("ruli", "루리웹 유머", "https://bbs.ruliweb.com/best/humor_only?orderby=recommend&range=24h", ruliweb),
    ("oyu", "오유 베오베", "https://www.todayhumor.co.kr/board/list.php?table=bestofbest", todayhumor),
]
