"""Nightly: read the EliteProspects 'in the system' page and refresh prospects.json.
Only updates stats for players already in the JSON (bios/draft data live in the embeds).
Exits without changing the file if the page can't be parsed or the data looks wrong."""
import json, re, sys, datetime, unicodedata, urllib.request
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup

URL = "https://www.eliteprospects.com/team/77/vancouver-canucks/in-the-system"
NOW = datetime.datetime.now(datetime.timezone.utc)
data = json.load(open("prospects.json", encoding="utf-8"))
old = json.loads(json.dumps(data))


def bail(msg):
    """Stop without touching stats, but leave the reason in prospects.json ("status") so it can be read without logging in."""
    print(msg)
    st = old.get("status") or {}
    if st.get("msg") != msg:
        out = json.loads(json.dumps(old))
        out["status"] = {"msg": msg, "at": NOW.strftime("%Y-%m-%dT%H:%MZ")}
        json.dump(out, open("prospects.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    sys.exit(0)

def fetch(url):
    """Plain request first; if the site answers 403 (it blocks GitHub's servers), retry as a real browser (curl_cffi)."""
    errs = []
    hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9", "Cache-Control": "no-cache", "Upgrade-Insecure-Requests": "1"}
    try:
        return urllib.request.urlopen(urllib.request.Request(url, headers=hdrs), timeout=30).read().decode("utf-8", "replace")
    except Exception as e:
        errs.append("urllib: " + str(e)[:60]); print("plain request failed:", e)
    try:
        from curl_cffi import requests as cr
    except Exception as e:
        errs.append("curl_cffi not installed"); cr = None
    if cr:
        for imp in ("chrome", "safari", "edge"):
            try:
                r = cr.get(url, impersonate=imp, timeout=30, headers={"Accept-Language": "en-US,en;q=0.9"})
                if r.status_code == 200 and r.text:
                    print("fetched OK as", imp); return r.text
                errs.append(imp + ": HTTP " + str(r.status_code)); print(imp, "HTTP", r.status_code)
            except Exception as e:
                errs.append(imp + ": " + str(e)[:50]); print(imp, "failed:", e)
    raise Exception("; ".join(errs))

try:
    html = fetch(URL + "?cb=" + NOW.strftime("%Y%m%d%H%M"))
except Exception as e:
    bail("FETCH FAILED: " + str(e)[:220])
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
                setv(rec, who, "gp", gp)
                if gp > 0:
                    setv(rec, who, "gaa", to_float(v[1]))
                    sv = v[2].strip(); sv = sv[1:] if sv.startswith("0.") else sv
                    setv(rec, who, "svp", sv)
                    setv(rec, who, "so", to_int(v[3]))
                    setv(rec, who, "rec", v[4].replace(" ", ""))
                    try:
                        mm, ss = v[5].strip().split(":"); setv(rec, who, "toi", round(int(mm.replace(",", "")) + int(ss) / 60, 2))
                    except Exception:
                        pass
                seen.add(key)
            elif is_skater and key in known_sk:
                who = known_sk[key]; rec = data["skaters"][who]
                gp, g, a, tp = (to_int(x) for x in v[:4])
                if tp != g + a: print("SKIP (TP != G+A):", who, v); continue
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

if len(seen) < 20: bail("TOO FEW PLAYERS PARSED: " + str(len(seen)) + " (title: " + str(soup.title.get_text(strip=True) if soup.title else None)[:80] + ")")
if not changed: print("no stat changes - refreshing the date only")
dropped = [n for grp in ("skaters", "goalies") for n, r in data[grp].items() if r["gp"] < old[grp][n]["gp"] and r.get("lg") != "AHL"]   # AHL numbers come from abby.json below
if len(dropped) > 2:
    bail("GP DECREASED for " + ", ".join(sorted(dropped)) + " - refusing to write (page looks stale)")
for n in dropped:
    print("GP decreased for", n, "- treating it as a correction from EliteProspects and writing it")


# ---- AHL players: the AHL's own stats (abby.json, from theahl.com's feed) replace the EliteProspects numbers ----
AHLS, AHLG = {}, {}
try:
    _ab = json.load(open("abby.json", encoding="utf-8"))
    AHLS = {norm(p["name"]): p for p in _ab.get("skaters", [])}
    AHLG = {norm(p["name"]): p for p in _ab.get("goalies", [])}
except Exception as e:
    print("abby.json not available, keeping EliteProspects numbers for AHL players:", e)
for n, r in data["skaters"].items():
    a = AHLS.get(norm(n))
    if r.get("lg") == "AHL" and a and a.get("gp") is not None:
        r["gp"], r["g"], r["a"], r["tp"] = a["gp"], a["g"], a["a"], a["pts"]
        r["pim"], r["pm"] = a.get("pim", r.get("pim", 0)), a.get("pm", r.get("pm", 0))
        r["ppg"] = (a["pts"] / a["gp"]) if a["gp"] else 0
        if r != old["skaters"][n]: print("AHL stats used for", n, ":", a["gp"], "GP", a["g"], "G", a["a"], "A")
for n, r in data["goalies"].items():
    a = AHLG.get(norm(n))
    if r.get("lg") == "AHL" and a and a.get("gp") is not None:
        r["gp"], r["gaa"], r["svp"], r["so"] = a["gp"], a["gaa"], a["svp"], a.get("so") or 0
        r["rec"] = "%d-%d-%d" % (a.get("w") or 0, a.get("l") or 0, a.get("otl") or 0)
        if a.get("log"): r["toi"] = round(sum(x[4] for x in a["log"]) / 60.0, 2)
        if r != old["goalies"][n]: print("AHL stats used for", n, ":", a["gp"], "GP", r["rec"], r["gaa"], r["svp"])


# ---- an Abbotsford game is being played right now: AHL players go back to their last FINAL numbers and tonight's AHL watch entries come out until the game ends ----
try:
    if _ab.get("live"):
        _gd = (NOW.astimezone(ZoneInfo("America/Vancouver")) - datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
        _day = next((d for d in data.get("watch", []) if d["date"] == _gd), None)
        for grp in ("skaters", "goalies"):
            for n in list(data[grp]):
                if data[grp][n].get("lg") == "AHL" and n in old[grp]:
                    rec = dict(old[grp][n])
                    for it in (_day or {}).get("items", []):
                        if it.get("name") == n and it.get("lg") == "AHL":
                            rec["gp"] = rec["gp"] - it.get("dgp", 0)
                            if it.get("k") == "S":
                                rec["g"] -= it.get("dg", 0); rec["a"] -= it.get("da", 0); rec["tp"] -= it.get("dtp", 0)
                                rec["ppg"] = (rec["tp"] / rec["gp"]) if rec["gp"] else 0
                            elif it.get("rec0"):
                                rec["rec"] = it["rec0"]
                    data[grp][n] = rec
        if _day:
            _day["items"] = [it for it in _day["items"] if it.get("lg") != "AHL"]
            if not _day["items"]: data["watch"] = [d for d in data["watch"] if d is not _day]
        print("Abbotsford game in progress - AHL players held at their last final numbers, tonight's AHL entries removed")
except NameError:
    pass

# ---- Prospect Watch: log who played since the last run, filed under the Pacific game date ----
def parse_rec(r):
    try:
        a = [int(x) for x in str(r).split("-")]
        return a + [0] * (3 - len(a))
    except Exception:
        return [0, 0, 0]

PT = ZoneInfo("America/Vancouver")
game_date = (NOW.astimezone(PT) - datetime.timedelta(hours=8)).strftime("%Y-%m-%d")  # before 8am PT counts as last night
watch = data.get("watch", [])
day = next((d for d in watch if d["date"] == game_date), None)
new_items = []
for n, r in data["skaters"].items():
    o = old["skaters"][n]
    dgp = r["gp"] - o["gp"]
    if dgp > 0:
        new_items.append({"k": "S", "name": n, "lg": r["lg"], "dgp": dgp, "dg": r["g"] - o["g"], "da": r["a"] - o["a"],
                          "dtp": r["tp"] - o["tp"], "sgp": r["gp"], "sg": r["g"], "sa": r["a"], "stp": r["tp"]})
for n, r in data["goalies"].items():
    o = old["goalies"][n]
    dgp = r["gp"] - o["gp"]
    if dgp > 0:
        a, b = parse_rec(o["rec"]), parse_rec(r["rec"])
        res = "W" if b[0] > a[0] else "L" if b[1] > a[1] else "OTL" if b[2] > a[2] else ""
        item = {"k": "G", "name": n, "lg": r["lg"], "dgp": dgp, "res": res, "gaa": r["gaa"],
                "svp": r["svp"], "sgp": r["gp"], "rec0": o["rec"]}
        try:   # goals against this game = season GA now minus season GA before (GAA x minutes / 60)
            # only trust it once EliteProspects has also added this game's minutes (otherwise the old minutes are reused and GA comes out as 0)
            if r.get("toi") and r.get("gaa") is not None and (o["gp"] == 0 or (o.get("toi") and o.get("gaa") is not None and r["toi"] > o["toi"])):
                ga_new = round(r["gaa"] * r["toi"] / 60)
                ga_old = round(o["gaa"] * o["toi"] / 60) if o["gp"] > 0 else 0
                if ga_new - ga_old >= 0: item["ga"] = ga_new - ga_old
            if "ga" not in item and o["gp"] > 0 and o.get("toi") and o.get("gaa") is not None:
                item["gaa0"], item["toi0"] = o["gaa"], o["toi"]     # GA can be worked out later, once EliteProspects posts this game's minutes
        except Exception as e:
            print("GA calc skipped for", n, e)
        ag = AHLG.get(norm(n)) if r.get("lg") == "AHL" else None
        if ag and ag.get("log"):   # AHL game log has the decision and goals against for every game
            lastg = ag["log"][:dgp]
            item["ga"] = sum(x[3] for x in lastg)
            if dgp == 1 and lastg[0][1]: item["res"] = lastg[0][1]; item.pop("rec0", None)
        if r.get("so", 0) > o.get("so", 0):
            item["so"] = r["so"] - o.get("so", 0)
            if item["so"] == dgp: item["ga"] = 0
        new_items.append(item)
if new_items:
    if day is None:
        day = {"date": game_date, "items": []}; watch.append(day)
    for it in new_items:
        ex = next((x for x in day["items"] if x["name"] == it["name"]), None)
        if ex is None:
            day["items"].append(it)
        else:  # same player again today: add the new deltas, keep latest season totals
            for k in ("dgp", "dg", "da", "dtp", "ga", "so"):
                if k in it: ex[k] = ex.get(k, 0) + it[k]
            ex.update({k: v for k, v in it.items() if k not in ("dgp", "dg", "da", "dtp", "ga", "so", "rec0") and not (k == "res" and not v)})
    print("watch:", [(i["name"], i["dgp"]) for i in new_items])
# late stat corrections: EliteProspects sometimes adds a goal/assist AFTER the game was logged (GP unchanged) - fold it into that game's watch entry
for n, r in data["skaters"].items():
    o = old["skaters"][n]
    if r["gp"] == o["gp"] and (r["g"], r["a"], r["tp"]) != (o["g"], o["a"], o["tp"]):
        for d in sorted(watch, key=lambda x: x["date"], reverse=True):
            it = next((x for x in d["items"] if x["name"] == n and x.get("k") == "S" and x.get("sgp") == r["gp"]), None)
            if it:
                it["dg"] = it.get("dg", 0) + r["g"] - o["g"]; it["da"] = it.get("da", 0) + r["a"] - o["a"]; it["dtp"] = it.get("dtp", 0) + r["tp"] - o["tp"]
                it["sg"], it["sa"], it["stp"] = r["g"], r["a"], r["tp"]
                print("watch corrected:", n, "now", it["dg"], "G", it["da"], "A")
                break
# a goalie's W/L/OTL can post later than his GP: fill in a blank result once the record catches up.
# Only for his MOST RECENT game: if he has played since, a changed record could belong to any of those games, so leave older blanks alone.
_last_g = {}
for d in sorted(watch, key=lambda x: x["date"]):
    for it in d["items"]:
        if it.get("k") == "G": _last_g[it["name"]] = it
for d in watch:
    for it in d["items"]:
        if it.get("k") == "G" and not it.get("res") and it.get("rec0") and it["name"] in data["goalies"] and _last_g.get(it["name"]) is it:
            a, b = parse_rec(it["rec0"]), parse_rec(data["goalies"][it["name"]]["rec"])
            res = "W" if b[0] > a[0] else "L" if b[1] > a[1] else "OTL" if b[2] > a[2] else ""
            if res:
                gg = data["goalies"][it["name"]]
                it["res"] = res; it["svp"] = gg["svp"]; it["gaa"] = gg["gaa"]; it.pop("rec0", None)
                print("result filled in:", it["name"], res)
# AHL goalies: make every logged game match the AHL game log (result + goals against) for that date
for d in watch:
    for it in d["items"]:
        ag = AHLG.get(norm(it["name"])) if it.get("k") == "G" and it.get("lg") == "AHL" else None
        if ag and ag.get("log") and it.get("dgp") == 1:
            hit = [x for x in ag["log"] if x[0] == d["date"]]
            if len(hit) == 1:
                if it.get("ga") != hit[0][3] or (hit[0][1] and it.get("res") != hit[0][1]):
                    it["ga"] = hit[0][3]
                    if hit[0][1]: it["res"] = hit[0][1]
                    it.pop("rec0", None)
                    print("AHL game log applied:", it["name"], d["date"], it["res"], it["ga"], "GA")
            if _last_g.get(it["name"]) is it:   # his latest game: keep the season SV% / GAA on the card current
                gg = data["goalies"][it["name"]]
                if (it.get("svp"), it.get("gaa")) != (gg["svp"], gg["gaa"]):
                    it["svp"], it["gaa"] = gg["svp"], gg["gaa"]; print("AHL season line refreshed:", it["name"], gg["svp"], gg["gaa"])
# goals against that couldn't be worked out when the game was logged (minutes not posted yet): fill in once they are
for d in watch:
    for it in d["items"]:
        if it.get("k") == "G" and "ga" not in it and it.get("gaa0") is not None and it.get("toi0") and it["name"] in data["goalies"] and _last_g.get(it["name"]) is it:
            gg = data["goalies"][it["name"]]
            if gg.get("toi") and gg.get("gaa") is not None and gg["toi"] > it["toi0"]:
                ga = round(gg["gaa"] * gg["toi"] / 60) - round(it["gaa0"] * it["toi0"] / 60)
                if ga >= 0:
                    it["ga"] = ga; it["svp"] = gg["svp"]; it["gaa"] = gg["gaa"]; it.pop("gaa0", None); it.pop("toi0", None)
                    print("GA filled in:", it["name"], ga)
# one-off hand corrections to a goalie's logged result (confirmed from his game log): {(name, watch date): result}
RESULT_FIXES = {("Dmitri Ivchenko", "2026-10-21"): "OTL"}   # Oct 21 was a shootout loss, wrongly stamped W
for d in watch:
    for it in d["items"]:
        fx = RESULT_FIXES.get((it["name"], d["date"]))
        if it.get("k") == "G" and fx and it.get("res") != fx:
            it["res"] = fx; it.pop("rec0", None); print("result corrected:", it["name"], d["date"], fx)
# one-off hand corrections to a goalie's goals against: {(name, watch date): GA}
GA_FIXES = {("Alexei Medvedev", "2026-10-30"): 4}   # logged before the late-GA fill-in existed
for d in watch:
    for it in d["items"]:
        ga = GA_FIXES.get((it["name"], d["date"]))
        if it.get("k") == "G" and ga is not None and it.get("ga") != ga:
            it["ga"] = ga; it.pop("gaa0", None); it.pop("toi0", None); print("GA corrected:", it["name"], d["date"], ga)
watch.sort(key=lambda d: d["date"], reverse=True)
data["watch"] = watch[:7]
# ---------------------------------------------------------------------------------------------

data.pop("status", None)
today = NOW - datetime.timedelta(hours=8)
data["asof"] = today.strftime("%Y-%m-%d")
data["label"] = today.strftime("%B ") + str(today.day) + today.strftime(", %Y")
json.dump(data, open("prospects.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print("prospects.json updated:", data["label"])

# =============================================================================================
# ---- Career history (once a day): season-by-season stats for the Prospect Portal -> career.json ----
# Runs AFTER prospects.json is saved, and can never stop the normal update: any problem here is just printed.
try:
    import time as _t
    C_BASE = "https://www.eliteprospects.com"
    C_CUR = "%d-%02d" % ((NOW.year if NOW.month >= 7 else NOW.year - 1), ((NOW.year if NOW.month >= 7 else NOW.year - 1) + 1) % 100)
    C_STALE_HOURS = 20
    C_BUDGET_SECONDS = 600
    C_BAD = re.compile(r"U-?1\d|\bJ1\d|HS\b|High School|Prep|Midget|AAA|Bantam|Minor|Intern|WJC|WJ-?18|Hlinka|Memorial|Olympic|World|Nat\.|Five Nations|Euro Hockey|Junior Club|Jr\. ?Nat", re.I)
    def c_keep(lg):
        lg = (lg or "").strip()
        if not lg or lg.endswith(" Q") or lg.lower().endswith("qualification"): return False
        return not C_BAD.search(lg)

    def c_ti(t):
        t = str(t).replace(",", "").replace("−", "-").strip()
        try: return int(float(t))
        except Exception: return 0
    def c_tf(t):
        t = str(t).replace(",", "").strip()
        try: return float(t)
        except Exception: return 0.0

    def c_parse(html):
        soup = BeautifulSoup(html, "html.parser")
        out = {"skater": [], "goalie": []}
        for table in soup.find_all("table"):
            hr = next((r for r in table.find_all("tr") if r.find("th")), None)
            heads = [h.get_text(" ", strip=True) for h in (hr.find_all(["th", "td"]) if hr else [])]
            if "Season" not in heads or "GP" not in heads: continue
            first = lambda name: heads.index(name) if name in heads else None
            iS, iT, iL, iGP = first("Season"), first("Team"), first("League"), first("GP")
            goalie = "GAA" in heads or "SV%" in heads
            idx = {k: first(k) for k in (("GAA", "SV%", "GA", "SV", "W-L-T", "TOI") if goalie else ("G", "A", "TP"))}
            if iT is None or iL is None: continue
            prev = ""
            for tr in table.find_all("tr"):
                if tr is hr: continue
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) == len(heads) - 1 and iS is not None:
                    cells.insert(iS, "")
                if len(cells) < len(heads): continue
                season = cells[iS].strip() if iS is not None else ""
                if re.match(r"^\d{4}-\d{2}", season): prev = season = re.match(r"^\d{4}-\d{2}", season).group(0)
                else: season = prev if not season else ""
                if not season: continue
                team, lg = cells[iT].strip(), cells[iL].strip()
                gp = c_ti(cells[iGP])
                if not gp or not c_keep(lg) or re.match(r"^[A-Za-z ]+ U-?\d\d( \(all\))?$", team) and re.match(r"^(Russia|Finland|Sweden|Canada|USA|Czech|Slovakia|Switzerland|Germany|Latvia|Norway|Denmark|Austria|Belarus|Kazakhstan)", team): continue
                if goalie:
                    w = l = t = 0
                    m = re.match(r"^(\d+)-(\d+)-(\d+)", cells[idx["W-L-T"]].replace(" ", "")) if idx.get("W-L-T") is not None else None
                    if m: w, l, t = (int(x) for x in m.groups())
                    ga = c_ti(cells[idx["GA"]]) if idx.get("GA") is not None else 0
                    sv = c_ti(cells[idx["SV"]]) if idx.get("SV") is not None else 0
                    mins = c_ti(cells[idx["TOI"]]) if idx.get("TOI") is not None else 0
                    svp = cells[idx["SV%"]].strip() if idx.get("SV%") is not None else ""
                    svp = svp[1:] if svp.startswith("0.") else ("" if svp in ("-", "–") else svp)
                    if svp == "" and sv + ga > 0: svp = ("%.3f" % (sv / (sv + ga))).lstrip("0")
                    gaa = c_tf(cells[idx["GAA"]]) if idx.get("GAA") is not None else 0.0
                    if not gaa and ga and mins: gaa = round(ga * 60.0 / mins, 2)
                    out["goalie"].append([season, team, lg, gp, w, l, t, gaa, svp, ga, sv + ga if (sv or ga) else 0, mins])
                else:
                    g, a, tp = (c_ti(cells[idx[k]]) for k in ("G", "A", "TP"))
                    out["skater"].append([season, team, lg, gp, g, a, tp])
            if out["goalie"] or out["skater"]: break
        return out

    c_old = {}
    try:
        c_old = json.load(open("career.json", encoding="utf-8"))
    except Exception:
        c_old = {}
    c_links = {}
    for a in soup.find_all("a", href=True):
        m = re.search(r"/player/(\d+)/([^/?#]+)", a["href"])
        if m:
            k = norm(re.sub(r"\([^)]*\)", "", a.get_text(" ", strip=True)))
            if k: c_links.setdefault(k, "/player/%s/%s" % (m.group(1), m.group(2)))
    print("career: player links found:", len(c_links))
    c_res, c_done, c_tried, c_start = dict(c_old), 0, 0, _t.time()
    for c_name, c_isg in [(n, False) for n in data["skaters"]] + [(n, True) for n in data["goalies"]]:
        if _t.time() - c_start > C_BUDGET_SECONDS: print("career: time budget used, the rest waits for the next run"); break
        e = c_old.get(c_name)
        if e and e.get("at") and e.get("rows") is not None and (NOW.replace(tzinfo=None) - datetime.datetime.fromisoformat(e["at"])).total_seconds() < C_STALE_HOURS * 3600: continue
        path = c_links.get(norm(c_name))
        if not path: print("career: no EP link for", c_name); continue
        c_tried += 1
        rows = None
        for suffix in ("", "/print"):
            try:
                p = c_parse(fetch(C_BASE + path + suffix))
                rows = p["goalie" if c_isg else "skater"]
                if rows: break
            except Exception as ex:
                print("career: fetch failed:", c_name, suffix, str(ex)[:80])
            _t.sleep(1.5)
        if not rows: print("career: no rows for", c_name); continue
        rows = [r for r in rows if r[0] != C_CUR]
        c_res[c_name] = {"at": NOW.strftime("%Y-%m-%dT%H:%M:%S"), "rows": rows}; c_done += 1
        if c_done == 1: print("career: sample", c_name, rows[:2])
        _t.sleep(2)
    print("career: updated", c_done, "of", c_tried, "tried;", len(c_res), "players stored")
    if c_res != c_old:
        json.dump(c_res, open("career.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
except Exception as _e:
    print("career skipped:", str(_e)[:200])
