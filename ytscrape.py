# -*- coding: utf-8 -*-
"""키 안 쓰고 유튜브 검색 화면을 긁는다 — 채널 레이더가 '이번 주 조회수순' 검색으로 채널을 모을 때 쓴다.
   (영드레이더 yd-radar/yt.py 에서 필요한 것만 옮김. 깃허브 서버에서도 막히지 않는 것 확인 2026-11-01)"""
import re, json, time, base64, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


class Blocked(Exception):
    """유튜브가 로봇 확인·동의 화면을 내밀었다(서버 IP 차단)"""


def fetch(url, gl="KR", tries=3):
    head = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9", "Cookie": f"PREF=hl=ko&gl={gl}; CONSENT=YES+1"}
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=head), timeout=30) as r:
                body = r.read().decode("utf-8", "replace")
            if "consent.youtube.com" in body[:3000] or "/sorry/" in body[:3000]:
                raise Blocked("consent/sorry")
            return body
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise Blocked("429")
            if e.code >= 500 and i < tries - 1:
                time.sleep(3 * (i + 1)); continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if i < tries - 1:
                time.sleep(3 * (i + 1)); continue
            raise


def walk(obj, key):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            else:
                yield from walk(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v, key)


def text(node):
    if not node: return ""
    if isinstance(node, str): return node
    if "simpleText" in node: return node["simpleText"]
    if "runs" in node: return "".join(r.get("text", "") for r in node["runs"])
    return ""


def num(s):
    """'조회수 782,485회' '12만' → 정수"""
    s = (s or "").replace(",", "")
    m = re.search(r"([\d.]+)\s*(억|만|천)?", s)
    if not m: return None
    return int(float(m.group(1)) * {"억": 1e8, "만": 1e4, "천": 1e3, None: 1}[m.group(2)])


AGO = {"초": 1 / 60, "분": 1, "시간": 60, "일": 1440, "주": 10080, "개월": 43200, "년": 525600}

def ago_iso(s, now):
    """'3일 전' '스트리밍 시간: 5시간 전' → 대략의 올린 때(ISO)"""
    m = re.search(r"(\d+)\s*(초|분|시간|일|주|개월|년)\s*전", s or "")
    if not m: return None
    t = now - timedelta(minutes=int(m.group(1)) * AGO[m.group(2)])
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def secs(s):
    p = [int(x) for x in re.findall(r"\d+", s or "")][-3:]
    return sum(v * 60 ** i for i, v in enumerate(reversed(p))) if p else None


def _sp(b):
    return urllib.parse.quote(base64.b64encode(bytes(b)).decode())

WEEK_TOP = _sp([0x08, 0x03, 0x12, 0x04, 0x08, 0x03, 0x10, 0x01])                # 이번 주 · 동영상 · 조회수순
WEEK_SHORT = _sp([0x08, 0x03, 0x12, 0x06, 0x08, 0x03, 0x10, 0x01, 0x18, 0x01])  # + 4분 미만


def week_hits(query, gl="KR", short=False):
    """이번 주에 올라온 영상 20개 (조회수순) — 영상마다 채널 id·조회수·대략 올린 때가 따라온다"""
    url = ("https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
           + "&sp=" + (WEEK_SHORT if short else WEEK_TOP) + f"&hl=ko&gl={gl}")
    for i in range(2):  # 가끔 자료 없는 화면이 온다 → 한 번 더
        m = re.search(r"ytInitialData\s*=\s*(\{.*?\});\s*</script>", fetch(url, gl=gl), re.S)
        if m:
            break
        time.sleep(2)
    else:
        raise Blocked("no ytInitialData")
    now, out = datetime.now(timezone.utc), []
    for v in walk(json.loads(m.group(1)), "videoRenderer"):
        run = ((v.get("ownerText") or {}).get("runs") or [{}])[0]
        cid = run.get("navigationEndpoint", {}).get("browseEndpoint", {}).get("browseId")
        if not v.get("videoId") or not cid:
            continue
        when = text(v.get("publishedTimeText"))
        if not v.get("lengthText") or "스트리밍" in when or "LIVE" in json.dumps(v.get("badges", [])):
            continue  # 라이브 방송(조회수가 부풀어 있고 우리가 만들 영상도 아님)
        out.append({"id": v["videoId"], "t": text(v.get("title")), "ch": run.get("text", ""), "cid": cid,
                    "v": num(text(v.get("viewCountText"))) or 0,
                    "pub": ago_iso(when, now),
                    "dur": secs(text(v.get("lengthText")))})
    return out
