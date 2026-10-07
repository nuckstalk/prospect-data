"""Nightly: scrape the EliteProspects 'in the system' page and refresh prospects.json.
Only updates stats for players already in the JSON (bio/draft data lives in the embeds).
Exits without changing the file if the page can't be parsed or looks stale/blocked."""
import json, re, sys, datetime, urllib.request
from bs4 import BeautifulSoup

URL = "https://www.eliteprospects.com/team/77/vancouver-canucks/in-the-system"
data = json.load(open("prospects.json", encoding="utf-8"))

req = urllib.request.Request(URL + "?cb=" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M"),
    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
             "Cache-Control": "no-cache"})
try:
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
except Exception as e:
    print("FETCH FAILED:", e); sys.exit(0)

soup = BeautifulSoup(html, "html.parser")

# ---- DIAGNOSTICS (printed to the Actions log) ----
print("HTML length:", len(html))
print("Title:", soup.title.get_text(strip=True) if soup.title else None)
print("Tables:", len(soup.find_all("table")))
for i, t in enumerate(soup.find_all("table")[:4]):
    print(" table", i, "headers:", [h.get_text(" ", strip=True) for h in t.find_all("th")][:14])
    r = t.find_all("tr")
    print("   rows:", len(r), "| sample:", r[1].get_text(" | ", strip=True)[:160] if len(r) > 1 else None)
print("has __NEXT_DATA__:", "__NEXT_DATA__" in html, "| has 'Riley Patterson':", "Riley Patterson" in html,
      "| cloudflare/challenge:", any(w in html.lower() for w in ("just a moment", "cf-chl", "captcha", "access denied")))
print("Body text sample:", re.sub(r"\s+", " ", soup.get_text(" "))[:600])
# ---------------------------------------------------

def norm(s):
    import unicodedata
    return re.sub(r"[^a-z ]", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()).strip()

def num(t, f=int):
    t = t.strip().replace("−", "-")
    try: return f(t)
    except: return None

known_sk = {norm(n): n for n in data["skaters"]}
known_g = {norm(n): n for n in data["goalies"]}
changed, seen = [], set()

for table in soup.find_all("table"):
    heads = [h.get_text(" ", strip=True).upper() for h in table.find_all("th")]
    if "GP" not in heads: continue
    is_goalie = "GAA" in heads
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if not tds: continue
        name = None
        for a in tr.find_all("a"):
            k = norm(a.get_text(" ", strip=True))
            if k in known_sk or k in known_g: name = k; break
        if not name: continue
        cells = [td.get_text(" ", strip=True) for td in tds]
        # map header -> cell (headers and cells may be offset by leading #/name cols)
        off = len(cells) - len(heads)
        col = lambda h: cells[heads.index(h) + off] if h in heads and 0 <= heads.index(h) + off < len(cells) else None
        if is_goalie and name in known_g:
            rec = data["goalies"][known_g[name]]
            new = dict(gp=num(col("GP") or ""), gaa=num(col("GAA") or "", float), svp=(col("SV%") or "").lstrip("0") or None)
            if new["gp"] is None: continue
            for k, v in new.items():
                if v is not None and rec.get(k) != v: rec[k] = v; changed.append((known_g[name], k, v))
            seen.add(name)
        elif not is_goalie and name in known_sk:
            rec = data["skaters"][known_sk[name]]
            gp, g, a, tp = (num(col(h) or "") for h in ("GP", "G", "A", "TP"))
            if None in (gp, g, a, tp) or tp != g + a: continue
            new = dict(gp=gp, g=g, a=a, tp=tp, ppg=(tp / gp if gp else 0),
                       pim=num(col("PIM") or ""), pm=num(col("+/-") or ""))
            for k, v in new.items():
                if v is None: continue
                if rec.get(k) != v: rec[k] = v; changed.append((known_sk[name], k, v))
            seen.add(name)

print(f"parsed {len(seen)} known players, {len(changed)} field changes")
for c in changed: print("  ", c)
# safety: don't commit if the parse looks broken (too few players) or nothing changed
if len(seen) < 10 or not changed:
    print("no commit"); sys.exit(0)
# safety: GP should never go down
old = json.load(open("prospects.json", encoding="utf-8"))
for grp in ("skaters", "goalies"):
    for n, r in data[grp].items():
        if r["gp"] < old[grp][n]["gp"]:
            print("GP DECREASED for", n, "- refusing to write"); sys.exit(0)
today = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=8)  # Pacific-ish
data["asof"] = today.strftime("%Y-%m-%d")
data["label"] = today.strftime("%B ") + str(today.day) + today.strftime(", %Y")
json.dump(data, open("prospects.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
