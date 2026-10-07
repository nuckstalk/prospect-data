"""Nightly: read the EliteProspects 'in the system' page and refresh prospects.json.
Only updates stats for players already in the JSON (bios/draft data live in the embeds).
Exits without changing the file if the page can't be parsed or the data looks wrong."""
import json, re, sys, datetime, unicodedata, urllib.request
from bs4 import BeautifulSoup

URL = "https://www.eliteprospects.com/team/77/vancouver-canucks/in-the-system"
NOW = datetime.datetime.now(datetime.timezone.utc)
data = json.load(open("prospects.json", encoding="utf-8"))
old = json.loads(json.dumps(data))

req = urllib.request.Request(URL + "?cb=" + NOW.strftime("%Y%m%d%H%M"),
    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
             "Cache-Control": "no-cache"})
try:
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
except Exception as e:
    print("FETCH FAILED:", e); sys.exit(0)
soup = BeautifulSoup(html, "html.parser")
print("Title:", soup.title.get_text(strip=True) if soup.title else None, "| tables:", len(soup.find_all("table")))

def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", s)

def to_int(t):
    t = t.strip().replace("−", "-")
    return 0 if t in ("", "-") else int(t)

def to_float(t):
    t = t.strip()
    return 0.0 if t in ("", "-") else float(t)

known_sk = {norm(n): n for n in data["skaters"]}
known_g = {norm(n): n for n in data["goalies"]}
seen, changed = set(), []

def setv(rec, who, k, v):
    if rec.get(k) != v:
        changed.append((who, k, rec.get(k), v)); rec[k] = v

for table in soup.find_all("table"):
    heads = [h.get_text(" ", strip=True) for h in table.find_all("th")]
    is_goalie = "Goalie" in heads
    is_skater = "Skater" in heads
    if not (is_goalie or is_skater): continue
    first = next((r for r in table.find_all("tr") if r.find("td")), None)
    if first: print("first row cells:", [c.get_text(" ", strip=True) for c in first.find_all("td")])
    tail = 6 if is_goalie else 7   # columns after League: GP.. / GP,GAA,SV%,SO,W-L-T,TOI
    for tr in table.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) < tail + 3: continue
        cands = [cells[1]] + [x.get_text(" ", strip=True) for x in tr.find_all(["th", "a"])]
        key = None
        for cnd in cands:
            k = norm(re.sub(r"\([^)]*\)", "", cnd))
            if k in known_sk or k in known_g: key = k; break
        if key is None: continue
        team, lg = cells[-(tail + 2)], cells[-(tail + 1)]
        v = cells[-tail:]
        try:
            if is_goalie and key in known_g:
                who = known_g[key]; rec = data["goalies"][who]
                gp = to_int(v[0])
                setv(rec, who, "team", team); setv(rec, who, "lg", lg); setv(rec, who, "gp", gp)
                if gp > 0:
                    setv(rec, who, "gaa", to_float(v[1]))
                    sv = v[2].strip(); sv = sv[1:] if sv.startswith("0.") else sv
                    setv(rec, who, "svp", sv)
                    setv(rec, who, "so", to_int(v[3]))
                    setv(rec, who, "rec", v[4].replace(" ", ""))
                seen.add(key)
            elif is_skater and key in known_sk:
                who = known_sk[key]; rec = data["skaters"][who]
                gp, g, a, tp = (to_int(x) for x in v[:4])
                if tp != g + a: print("SKIP (TP != G+A):", who, v); continue
                setv(rec, who, "team", team); setv(rec, who, "lg", lg)
                for k, val in (("gp", gp), ("g", g), ("a", a), ("tp", tp), ("ppg", (tp / gp if gp else 0)),
                               ("pim", to_int(v[5])), ("pm", to_int(v[6]))):
                    setv(rec, who, k, val)
                seen.add(key)
        except Exception as e:
            print("PARSE ERROR on row", cells[:3], e)

missing = [n for k, n in {**known_sk, **known_g}.items() if k not in seen]
print(f"parsed {len(seen)} of {len(known_sk) + len(known_g)} known players, {len(changed)} field changes")
if missing: print("not found on page:", missing)
for c in changed: print("  ", c)

if len(seen) < 20: print("too few players parsed - no commit"); sys.exit(0)
if not changed: print("nothing changed - no commit"); sys.exit(0)
for grp in ("skaters", "goalies"):
    for n, r in data[grp].items():
        if r["gp"] < old[grp][n]["gp"]:
            print("GP DECREASED for", n, "- refusing to write (page may be stale)"); sys.exit(0)

today = NOW - datetime.timedelta(hours=8)
data["asof"] = today.strftime("%Y-%m-%d")
data["label"] = today.strftime("%B ") + str(today.day) + today.strftime(", %Y")
json.dump(data, open("prospects.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print("prospects.json updated:", data["label"])
