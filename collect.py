# -*- coding: utf-8 -*-
"""오늘의 흐름 — 시세(야후 파이낸스)·구글 트렌드·유튜브 인기 영상을 한 번 받아
   data.json(보관용)과 data.js(화면이 읽는 것)에 쓰고, 때가 되면 채널 레이더(radar.py)도 돌린다.
   설정은 config.json. 깃허브 Actions 가 30분마다 부른다(.github/workflows/collect.yml).
   유튜브 키: 환경변수 YOUTUBE_API_KEY → 없으면 youtube_key.txt (저장소에 안 올림)
   사용: python collect.py [--out 폴더] [--all]
         --out  자료를 쓸 폴더 (기본: 이 폴더)   --all  이슈·레이더도 지금 받기"""
import os, sys, json, warnings, urllib.request, urllib.parse
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

# ── 구글 트렌드 ────────────────────────────────────────
NS = {"ht": "https://trends.google.com/trending/rss"}

def fetch_trends():
    out = {}
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
            items.append({"title": it.findtext("title", ""),
                          "traffic": it.findtext("ht:approx_traffic", "", NS),
                          "picture": it.findtext("ht:picture", "", NS),
                          "pub": pub, "news": news[:3]})
        out[geo] = {"label": label, "items": items}
    DATA["trends"] = out
    DATA["trends_at"] = now_str()

# ── 유튜브 인기 급상승 ──────────────────────────────────
def fetch_youtube():
    key = youtube_key()
    if not key:
        DATA["youtube"] = {"error": "유튜브 키가 없어요 (깃허브 Secrets 의 YOUTUBE_API_KEY)"}
        return
    q = urllib.parse.urlencode({"part": "snippet,statistics", "chart": "mostPopular",
                                "regionCode": "KR", "maxResults": 20, "key": key})
    j = json.loads(get("https://www.googleapis.com/youtube/v3/videos?" + q))
    vids = [{"id": v["id"], "title": v["snippet"]["title"],
             "channel": v["snippet"]["channelTitle"],
             "thumb": v["snippet"]["thumbnails"].get("medium", {}).get("url", ""),
             "views": int(v.get("statistics", {}).get("viewCount", 0)),
             "published": v["snippet"]["publishedAt"]} for v in j.get("items", [])]
    DATA["youtube"] = {"items": vids, "at": now_str()}

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
    if "--all" in sys.argv or trends_due():
        run("trends", fetch_trends)
        run("youtube", fetch_youtube)
    save()
    if youtube_key():
        import radar
        try:
            radar.collect(OUT, youtube_key(), CFG["radar"], log, force="--all" in sys.argv)
        except Exception as e:
            log(f"radar 실패: {e}")
