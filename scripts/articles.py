"""Every 30 min: read the NucksTalk blog RSS feed and write articles.json
(title, link, date, category, excerpt, image). Leaves the file alone if anything looks wrong."""
import json, sys, os, re, datetime, html
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

URL = "https://www.nuckstalk.com/blog-feed.xml"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

def fetch():
    local = os.environ.get("ARTICLES_FEED_FILE")
    if local:
        return open(local, "rb").read()
    import urllib.request
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": UA, "Accept": "application/rss+xml,application/xml,text/xml,*/*"})
        return urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:
        print("urllib failed:", e)
    try:
        from curl_cffi import requests as cr
        r = cr.get(URL, impersonate="chrome", timeout=30)
        if r.status_code == 200:
            return r.content
        print("curl_cffi status", r.status_code)
    except Exception as e:
        print("curl_cffi failed:", e)
    return None

def clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()

raw = fetch()
if not raw:
    print("FETCH FAILED - no change"); sys.exit(0)
try:
    root = ET.fromstring(raw)
except Exception as e:
    print("PARSE FAILED:", e); sys.exit(0)

items = []
for it in root.iter("item"):
    title = clean(it.findtext("title"))
    link = (it.findtext("link") or "").strip()
    if not title or not link:
        continue
    try:
        dt = parsedate_to_datetime(it.findtext("pubDate")).astimezone(datetime.timezone.utc)
        date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        date = ""
    cats = [clean(c.text) for c in it.findall("category") if clean(c.text)]
    enc = it.find("enclosure")
    img = (enc.get("url") if enc is not None else "") or ""
    items.append({
        "title": title, "url": link, "date": date,
        "cat": cats[0] if cats else "",
        "desc": clean(it.findtext("description")),
        "img": img,
    })

if not items:
    print("no items found - no change"); sys.exit(0)

items.sort(key=lambda x: x["date"], reverse=True)
out = {"updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "items": items[:20]}
try:
    old = json.load(open("articles.json", encoding="utf-8"))
except Exception:
    old = {}
if old.get("items") == out["items"]:
    print("no change"); sys.exit(0)
json.dump(out, open("articles.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print("articles.json updated:", len(out["items"]), "articles")
