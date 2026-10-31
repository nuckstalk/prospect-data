"""Daily: pull each prospect's season-by-season history from EliteProspects into career.json.
Only keeps: pro leagues, CHL, NCAA, European leagues and U20 junior. Drops U18 and younger, international tournaments, qualifiers.
Leaves the current season out (the page builds that row from the live stats). Never touches prospects.json."""
import json, re, sys, time, datetime, unicodedata, urllib.request
from bs4 import BeautifulSoup

BASE = "https://www.eliteprospects.com"
URL = BASE + "/team/77/vancouver-canucks/in-the-system"
NOW = datetime.datetime.now(datetime.timezone.utc)
STALE_HOURS = 20
CUR = "%d-%02d" % ((NOW.year if NOW.month >= 7 else NOW.year - 1), ((NOW.year if NOW.month >= 7 else NOW.year - 1) + 1) % 100)

def fetch(url):
    hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"}
    try:
        return urllib.request.urlopen(urllib.request.Request(url, headers=hdrs), timeout=30).read().decode("utf-8", "replace")
    except Exception as e:
        err = "urllib: " + str(e)[:50]
    try:
        from curl_cffi import requests as cr
    except Exception:
        raise Exception(err + "; curl_cffi missing")
    for imp in ("chrome", "safari", "edge"):
        try:
            r = cr.get(url, impersonate=imp, timeout=30, headers={"Accept-Language": "en-US,en;q=0.9"})
            if r.status_code == 200 and r.text: return r.text
            err += "; " + imp + " " + str(r.status_code)
        except Exception as e:
            err += "; " + imp + " " + str(e)[:40]
    raise Exception(err)

def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", s)

BAD = re.compile(r"U-?1\d|\bJ1\d|HS\b|High School|Prep|Midget|AAA|Bantam|Minor|Intern|WJC|WJ-?18|Hlinka|Memorial|Olympic|World|Nat\.|Five Nations|Euro Hockey|Junior Club|Jr\. ?Nat", re.I)
def keep(lg):
    lg = (lg or "").strip()
    if not lg or lg.endswith(" Q") or lg.lower().endswith("qualification"): return False
    return not BAD.search(lg)

def ti(t):
    t = str(t).replace(",", "").replace("−", "-").strip()
    try: return int(float(t))
    except Exception: return 0
def tf(t):
    t = str(t).replace(",", "").strip()
    try: return float(t)
    except Exception: return 0.0

def parse(html):
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
            gp = ti(cells[iGP])
            if not gp or not keep(lg) or re.match(r"^[A-Za-z ]+ U-?\d\d( \(all\))?$", team) and re.match(r"^(Russia|Finland|Sweden|Canada|USA|Czech|Slovakia|Switzerland|Germany|Latvia|Norway|Denmark|Austria|Belarus|Kazakhstan)", team): continue
            if goalie:
                w = l = t = 0
                m = re.match(r"^(\d+)-(\d+)-(\d+)", cells[idx["W-L-T"]].replace(" ", "")) if idx.get("W-L-T") is not None else None
                if m: w, l, t = (int(x) for x in m.groups())
                ga = ti(cells[idx["GA"]]) if idx.get("GA") is not None else 0
                sv = ti(cells[idx["SV"]]) if idx.get("SV") is not None else 0
                mins = ti(cells[idx["TOI"]]) if idx.get("TOI") is not None else 0
                svp = cells[idx["SV%"]].strip() if idx.get("SV%") is not None else ""
                svp = svp[1:] if svp.startswith("0.") else ("" if svp in ("-", "–") else svp)
                if svp == "" and sv + ga > 0: svp = ("%.3f" % (sv / (sv + ga))).lstrip("0")
                gaa = tf(cells[idx["GAA"]]) if idx.get("GAA") is not None else 0.0
                if not gaa and ga and mins: gaa = round(ga * 60.0 / mins, 2)
                out["goalie"].append([season, team, lg, gp, w, l, t, gaa, svp, ga, sv + ga if (sv or ga) else 0, mins])
            else:
                g, a, tp = (ti(cells[idx[k]]) for k in ("G", "A", "TP"))
                out["skater"].append([season, team, lg, gp, g, a, tp])
        if out["goalie"] or out["skater"]: break
    return out

def main():
    try:
        old = json.load(open("career.json", encoding="utf-8"))
    except Exception:
        old = {}
    pros = json.load(open("prospects.json", encoding="utf-8"))
    names = [(n, False) for n in pros["skaters"]] + [(n, True) for n in pros["goalies"]]
    try:
        home = fetch(URL + "?cb=" + NOW.strftime("%Y%m%d"))
    except Exception as e:
        print("could not load the in-the-system page:", e); return
    links = {}
    for a in BeautifulSoup(home, "html.parser").find_all("a", href=True):
        m = re.search(r"/player/(\d+)/([^/?#]+)", a["href"])
        if m:
            k = norm(re.sub(r"\([^)]*\)", "", a.get_text(" ", strip=True)))
            if k: links.setdefault(k, "/player/%s/%s" % (m.group(1), m.group(2)))
    print("player links found:", len(links))
    res, done, tried = dict(old), 0, 0
    for n, isg in names:
        e = old.get(n)
        if e and e.get("at") and (NOW.replace(tzinfo=None) - datetime.datetime.fromisoformat(e["at"])).total_seconds() < STALE_HOURS * 3600 and e.get("rows") is not None: continue
        path = links.get(norm(n))
        if not path: print("no EP link for", n); continue
        tried += 1
        rows = None
        for suffix in ("", "/print"):
            try:
                p = parse(fetch(BASE + path + suffix))
                rows = p["goalie" if isg else "skater"]
                if rows: break
            except Exception as ex:
                print("fetch failed:", n, suffix, str(ex)[:80])
            time.sleep(1.5)
        if not rows:
            print("no rows for", n); continue
        rows = [r for r in rows if r[0] != CUR]
        res[n] = {"at": NOW.strftime("%Y-%m-%dT%H:%M:%S"), "rows": rows}; done += 1
        if done == 1: print("sample:", n, rows[:2])
        time.sleep(2)
    print("career updated for", done, "of", tried, "tried;", len(res), "players stored")
    if res != old:
        json.dump(res, open("career.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

main()
