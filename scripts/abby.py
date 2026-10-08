"""Abbotsford Canucks stats for the Stats Portal: pulls skaters, goalies, AHL standings and team
stats from the AHL's public stats feed (HockeyTech) and writes abby.json.
Leaves the file alone if anything looks wrong, and prints what it saw so problems are easy to spot."""
import json, re, sys, datetime, urllib.request, urllib.parse

BASE = "https://lscluster.hockeytech.com/feed/index.php"
KEY = "ccb91f29d6744675"          # the public key theahl.com itself uses
H = {"User-Agent": "Mozilla/5.0", "Referer": "https://theahl.com/", "Origin": "https://theahl.com"}
NOW = datetime.datetime.now(datetime.timezone.utc)
EAST = ("atlantic", "north")        # AHL divisions in the Eastern Conference; the rest are Western


HOSTS = ["https://lscluster.hockeytech.com/feed/index.php", "https://lscluster.hockeytech.com/feed/"]
HEADERS = [{"User-Agent": "Mozilla/5.0"}, H,
           {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01", "Referer": "https://www.theahl.com/", "Origin": "https://www.theahl.com"},
           ]
SHOWN = set()


def raw_get(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read().decode("utf-8", "replace").strip()


def discover_key():
    """If the built-in key is rejected, look for the one theahl.com itself uses in its page scripts."""
    pat = re.compile(r"""["']?(?:key|apiKey|api_key)["']?\s*[:=]\s*["']([0-9a-f]{16})["']""")
    try:
        _, _, home = raw_get("https://theahl.com/", H)
        srcs = re.findall(r'src=["\']([^"\']+\.js[^"\']*)["\']', home)[:12]
        for text in [home] + [raw_get(u if u.startswith("http") else urllib.parse.urljoin("https://theahl.com/", u), H)[2] for u in srcs if "theahl" in u or u.startswith("/")]:
            m = pat.search(text)
            if m: return m.group(1)
    except Exception as e:
        print("key discovery failed:", e)
    return None


def ht(params):
    global KEY
    for attempt in range(2):
        q = dict(params, key=KEY, client_code="ahl", fmt="json", lang="en")
        for host in HOSTS:
            url = host + "?" + urllib.parse.urlencode(q)
            for hi, hd in enumerate(HEADERS):
                try:
                    status, ctype, raw = raw_get(url, hd)
                except Exception as e:
                    sig = (params.get("view"), host, hi, str(e)[:60])
                    if sig not in SHOWN: SHOWN.add(sig); print("FETCH FAILED:", params.get("view"), "| host", host[-12:], "| headers", hi, "->", e)
                    continue
                body = re.sub(r"^[\w.$]*\(", "", raw)
                body = re.sub(r"\)\s*;?\s*$", "", body)
                try:
                    return json.loads(body)
                except Exception:
                    sig = (params.get("view"), host, hi, "notjson")
                    if sig not in SHOWN:
                        SHOWN.add(sig)
                        print("NOT JSON:", params.get("view"), "| host", host[-12:], "| headers", hi, "| status", status, "|", ctype, "| length", len(raw), "|", raw[:150].replace("\n", " "))
        if attempt == 0:
            k = discover_key()
            print("built-in key failed; key found on theahl.com:", k)
            if not k or k == KEY: break
            KEY = k
    return None


def rows_of(obj):
    """Every {"row": {...}} dict found anywhere in a statviewfeed response."""
    out = []
    if isinstance(obj, dict):
        if isinstance(obj.get("row"), dict):
            r = dict(obj["row"])
            for k, v in obj.items():
                if k != "row" and not isinstance(v, (dict, list)): r.setdefault("_" + k, v)
            out.append(r)
        else:
            for v in obj.values(): out += rows_of(v)
    elif isinstance(obj, list):
        for v in obj: out += rows_of(v)
    return out


def g(r, *names, default=None):
    for n in names:
        if n in r and r[n] not in (None, "", "-"): return r[n]
    return default


def num(v, d=0.0):
    try: return float(str(v).replace("%", "").replace(",", "")) if v is not None else d
    except Exception: return d


def ival(r, *names): return int(num(g(r, *names), 0))


# ---- season + team ids
seasons = (ht({"feed": "modulekit", "view": "seasons"}) or {}).get("SiteKit", {}).get("Seasons", [])
reg = [s for s in seasons if str(s.get("playoff")) == "0" and "regular" in str(s.get("season_name", "")).lower()]
today = NOW.strftime("%Y-%m-%d")
started = [s for s in reg if str(s.get("start_date", "9999")) <= (NOW + datetime.timedelta(days=10)).strftime("%Y-%m-%d")]
pick = sorted(started or reg, key=lambda s: (str(s.get("start_date", "")), int(num(s.get("season_id")))))
if not pick:
    print("could not find a regular season - leaving abby.json alone"); sys.exit(0)
season = pick[-1]
sid = str(season["season_id"])
print("season:", season.get("season_name"), "id", sid)

teams = (ht({"feed": "modulekit", "view": "teamsbyseason", "season_id": sid}) or {}).get("SiteKit", {}).get("Teamsbyseason", [])
print("teams in season:", len(teams))
if teams: print("team keys:", sorted(teams[0].keys()))
abb = next((t for t in teams if "abbotsford" in json.dumps(t).lower()), None)
tid = str(abb["id"]) if abb else "440"
abb_code = str((abb or {}).get("code", (abb or {}).get("team_code", "ABB"))).upper()
print("Abbotsford team id:", tid)
by_id = {str(t.get("id")): t for t in teams}
by_code = {str(t.get("team_code", t.get("code", ""))).upper(): t for t in teams}


def divname(t):
    d = str(g(t or {}, "division_long_name", "division_name", "division", "division_short_name", default="")).replace("Division", "").strip()
    return d


# ---- skaters + goalies
def players(position):
    r = ht({"feed": "statviewfeed", "view": "players", "season": sid, "season_id": sid, "team": tid, "team_id": tid,
            "position": position, "rookie": "no", "statsType": "standard", "rosterstatus": "undefined",
            "site_id": "3", "first": "0", "limit": "200", "sort": "points" if position == "skaters" else "games_played",
            "league_id": "4", "division": "-1", "conference": "-1", "qualified": "all"})
    rows = [x for x in rows_of(r) if "properties" not in x or len(x) > 1]
    mine_rows = [x for x in rows if str(x.get("team_code", "")).upper() == abb_code]
    if mine_rows: rows = mine_rows
    print(position, "rows:", len(rows), "| first row keys:", sorted(rows[0].keys()) if rows else "none")
    return rows


def pname(r):
    n = g(r, "name", "player_name")
    if not n: n = (str(g(r, "first_name", default="")) + " " + str(g(r, "last_name", default=""))).strip()
    return re.sub(r"\s+", " ", str(n)).strip()


def img(r):
    pid = g(r, "player_id", "id")
    return "https://assets.leaguestat.com/ahl/240x240/%s.jpg" % pid if pid else ""


skaters = []
for r in players("skaters"):
    skaters.append({
        "name": pname(r), "id": g(r, "player_id", "id"), "pos": str(g(r, "position", "pos", default="")),
        "gp": ival(r, "games_played"), "g": ival(r, "goals"), "a": ival(r, "assists"), "pts": ival(r, "points"),
        "pm": ival(r, "plus_minus"), "pim": ival(r, "penalty_minutes"),
        "ppg": ival(r, "power_play_goals"), "shg": ival(r, "short_handed_goals"),
        "gwg": ival(r, "game_winning_goals") if g(r, "game_winning_goals") is not None else None,
        "otg": None, "sog": ival(r, "shots"),
        "shp": round(num(g(r, "shooting_percentage", "shooting_pct"), None) if g(r, "shooting_percentage", "shooting_pct") is not None else (100.0 * num(r.get("goals")) / num(r.get("shots")) if num(r.get("shots")) else 0.0), 1),
        "toi": None, "fo": None, "img": img(r)})
skaters = [s for s in skaters if s["name"]]
skaters.sort(key=lambda s: (-s["pts"], -s["g"], s["name"]))


def svp(v):
    v = num(v)
    return ("%.3f" % v).lstrip("0") if v <= 1 else ("%.3f" % (v / 100)).lstrip("0")


goalies = []
for r in players("goalies"):
    goalies.append({
        "name": pname(r), "id": g(r, "player_id", "id"), "gp": ival(r, "games_played"), "gs": None,
        "w": ival(r, "wins"), "l": ival(r, "losses"), "otl": ival(r, "ot_losses", "overtime_losses") + ival(r, "shootout_losses"),
        "gaa": round(num(g(r, "goals_against_average", "gaa")), 2), "svp": svp(g(r, "save_percentage", "sv_pct")),
        "so": ival(r, "shutouts"), "sa": ival(r, "shots_against", "shots"), "sv": ival(r, "saves"), "ga": ival(r, "goals_against"),
        "img": img(r)})
goalies = [x for x in goalies if x["name"]]
goalies.sort(key=lambda x: (-x["gp"], x["name"]))
for k in ("gs", "sa", "sv", "ga"):          # drop a column the feed doesn't carry rather than showing zeros
    if all(not x.get(k) for x in goalies):
        for x in goalies: x[k] = None

# ---- standings
sr = ht({"feed": "statviewfeed", "view": "teams", "groupTeamsBy": "league", "context": "overall", "site_id": "3",
         "season": sid, "season_id": sid, "special": "false", "league_id": "4"})
srows = [x for x in rows_of(sr) if (x.get("team_code") or x.get("code") or x.get("name")) and "games_played" in x]
print("standings rows:", len(srows), "| first row keys:", sorted(srows[0].keys()) if srows else "none")
standings = []
for r in srows:
    code = str(g(r, "team_code", "code", default="")).upper()
    t = by_code.get(code) or by_id.get(str(g(r, "team_id", "id", default="")))
    gp = ival(r, "games_played")
    w, l = ival(r, "wins"), ival(r, "losses")
    otl = ival(r, "ot_losses", "overtime_losses") + ival(r, "shootout_losses")
    pts = ival(r, "points")
    gf, ga = ival(r, "goals_for"), ival(r, "goals_against")
    div = divname(t) or str(g(r, "division_long_name", "division", default="")).replace("Division", "").strip()
    conf = "Eastern" if div.lower() in EAST else "Western"
    p10 = g(r, "past_10", "last_10", "l10", default="")
    strk = g(r, "streak", default="")
    standings.append({
        "abbr": code, "name": str(g(r, "name", "team_name", default=(t or {}).get("name", code))),
        "nick": str((t or {}).get("nickname") or g(r, "nickname", default="") or g(r, "name", default=code)),
        "div": div, "conf": conf, "gp": gp, "w": w, "l": l, "otl": otl, "pts": pts, "gf": gf, "ga": ga, "diff": gf - ga,
        "l10": re.sub(r"-0$", "", str(p10)) if p10 else "", "strk": str(strk) if strk else "", "_pp": r})


def key(t): return (-(t["pts"] / (2 * t["gp"]) if t["gp"] else 0), -t["pts"], -t["w"], t["name"])


def seq(group, field):
    for i, t in enumerate(sorted(group, key=key)): t[field] = i + 1


seq(standings, "lseq")
for c in set(t["conf"] for t in standings): seq([t for t in standings if t["conf"] == c], "cseq")
for d in set(t["div"] for t in standings): seq([t for t in standings if t["div"] == d], "dseq")

# ---- team stats with league rank (from the standings rows)
team = {}
mine = next((t for t in standings if t["abbr"] == "ABB" or "abbotsford" in t["name"].lower()), None)
if mine and mine["gp"]:
    def per(t, f): return t[f] / t["gp"] if t["gp"] else 0
    pool = [t for t in standings if t["gp"]]
    METRICS = [("gf", lambda t: per(t, "gf"), True, 2), ("ga", lambda t: per(t, "ga"), False, 2),
               ("pt", lambda t: 100 * t["pts"] / (2 * t["gp"]), True, 1)]
    for k, fn, high, nd in METRICS:
        v = fn(mine)
        team[k] = {"v": round(v, nd), "rank": 1 + sum(1 for t in pool if (fn(t) > v if high else fn(t) < v))}
    for k, names, high in (("pp", ("power_play_pct", "power_play_percentage"), True), ("pk", ("penalty_kill_pct", "penalty_kill_percentage"), True)):
        vals = [(t, num(g(t["_pp"], *names), None) if g(t["_pp"], *names) is not None else None) for t in pool]
        vals = [(t, v) for t, v in vals if v is not None]
        me = next((v for t, v in vals if t is mine), None)
        if me is not None and vals:
            team[k] = {"v": round(me * 100 if me <= 1 else me, 1), "rank": 1 + sum(1 for t, v in vals if v > me)}
for t in standings: t.pop("_pp", None)
print("standings teams:", len(standings), "| team metrics:", sorted(team.keys()))

if not skaters:
    print("no skater rows - leaving abby.json alone"); sys.exit(0)

data = {"season": str(season.get("season_name", sid)), "skaters": skaters, "goalies": goalies, "standings": standings, "team": team}
try:
    old = json.load(open("abby.json", encoding="utf-8"))
except Exception:
    old = {}
if {k: v for k, v in old.items() if k != "updated"} == data:
    print("no changes"); sys.exit(0)
data["updated"] = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
json.dump(data, open("abby.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print("abby.json updated:", len(skaters), "skaters,", len(goalies), "goalies,", len(standings), "teams,", len(team), "team stats")
