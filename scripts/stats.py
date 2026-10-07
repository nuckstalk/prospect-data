"""Canucks Stats Hub: pull skater, goalie, standings and team stats from the NHL's public API
and write stats.json. Leaves the file alone if anything looks wrong."""
import json, sys, datetime, urllib.request

H = {"User-Agent": "Mozilla/5.0"}
NOW = datetime.datetime.now(datetime.timezone.utc)

def get(url):
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=30))
    except Exception as e:
        print("FETCH FAILED:", url, "->", e); return None

def nm(x):
    return (x.get("default") if isinstance(x, dict) else x) or ""

def num(v, d=0):
    try: return float(v) if v is not None else d
    except Exception: return d

def toi(sec):
    s = int(num(sec)); return "%d:%02d" % (s // 60, s % 60)

def pct(v):
    v = num(v)
    return round(v * 100 if v <= 1 else v, 1)

# ---- skaters + goalies
cs = get("https://api-web.nhle.com/v1/club-stats/VAN/now")
if not cs or not cs.get("skaters"):
    print("no club stats - leaving stats.json alone"); sys.exit(0)
season = str(cs.get("season", ""))
print("season:", season, "| skaters:", len(cs["skaters"]), "| goalies:", len(cs.get("goalies", [])))
print("first skater keys:", sorted(cs["skaters"][0].keys()))

skaters = []
for p in cs["skaters"]:
    gp = int(num(p.get("gamesPlayed")))
    skaters.append({
        "name": (nm(p.get("firstName")) + " " + nm(p.get("lastName"))).strip(), "id": p.get("playerId"),
        "pos": p.get("positionCode", ""), "gp": gp,
        "g": int(num(p.get("goals"))), "a": int(num(p.get("assists"))), "pts": int(num(p.get("points"))),
        "pm": int(num(p.get("plusMinus"))), "pim": int(num(p.get("penaltyMinutes"))),
        "ppg": int(num(p.get("powerPlayGoals"))), "sog": int(num(p.get("shots"))),
        "shg": int(num(p.get("shorthandedGoals"))), "gwg": int(num(p.get("gameWinningGoals"))),
        "otg": int(num(p.get("overtimeGoals"))), "shp": pct(p.get("shootingPctg")),
        "toi": toi(p.get("avgTimeOnIcePerGame")), "fo": pct(p.get("faceoffWinPctg")),
        "img": p.get("headshot", "")})
skaters.sort(key=lambda s: (-s["pts"], -s["g"], s["name"]))

goalies = []
for p in cs.get("goalies", []):
    goalies.append({
        "name": (nm(p.get("firstName")) + " " + nm(p.get("lastName"))).strip(), "id": p.get("playerId"),
        "gp": int(num(p.get("gamesPlayed"))), "gs": int(num(p.get("gamesStarted"))),
        "w": int(num(p.get("wins"))), "l": int(num(p.get("losses"))), "otl": int(num(p.get("overtimeLosses"))),
        "gaa": round(num(p.get("goalsAgainstAverage")), 2),
        "svp": ("%.3f" % num(p.get("savePercentage"))).lstrip("0"),
        "so": int(num(p.get("shutouts"))), "sa": int(num(p.get("shotsAgainst"))),
        "sv": int(num(p.get("saves"))), "ga": int(num(p.get("goalsAgainst"))), "img": p.get("headshot", "")})
goalies.sort(key=lambda g: (-g["gp"], g["name"]))


# ---- game-by-game logs (newest first) for the Trends tab
try:
    prev = json.load(open("stats.json", encoding="utf-8"))
except Exception:
    prev = {}
prev_logs = {p.get("id"): p.get("log") for p in prev.get("skaters", []) + prev.get("goalies", []) if p.get("log")}

def tsec(t):
    try:
        m, sec = str(t).split(":"); return int(m) * 60 + int(sec)
    except Exception:
        return 0

def get_log(pid):
    d = get("https://api-web.nhle.com/v1/player/%s/game-log/now" % pid)
    gl = (d or {}).get("gameLog")
    if gl is None: return None
    return sorted(gl, key=lambda g: g.get("gameDate", ""), reverse=True)  # newest first, whatever order the NHL sends

logged = 0
for p in skaters:
    gl = get_log(p["id"]) if p.get("id") else None
    if gl is None:
        p["log"] = prev_logs.get(p.get("id"), []); continue
    p["log"] = [[g.get("gameDate", ""), int(num(g.get("goals"))), int(num(g.get("assists"))), int(num(g.get("points"))),
                 int(num(g.get("plusMinus"))), int(num(g.get("pim"))), int(num(g.get("shots"))),
                 int(num(g.get("powerPlayPoints"))), tsec(g.get("toi"))] for g in gl[:90]]
    logged += 1
for p in goalies:
    gl = get_log(p["id"]) if p.get("id") else None
    if gl is None:
        p["log"] = prev_logs.get(p.get("id"), []); continue
    p["log"] = [[g.get("gameDate", ""), str(g.get("decision", "")), int(num(g.get("shotsAgainst"))), int(num(g.get("goalsAgainst"))),
                 tsec(g.get("toi")), int(num(g.get("shutouts"))), int(num(g.get("gamesStarted")))] for g in gl[:90]]
    logged += 1
print("game logs fetched:", logged, "of", len(skaters) + len(goalies))
if skaters and skaters[0].get("log"): print("sample log row:", skaters[0]["log"][0])

# ---- standings (all 32 teams, compact)
st = get("https://api-web.nhle.com/v1/standings/now")
standings = []
if st and st.get("standings"):
    for t in st["standings"]:
        standings.append({
            "abbr": nm(t.get("teamAbbrev")), "name": nm(t.get("teamName")), "nick": nm(t.get("teamCommonName")),
            "div": t.get("divisionName", t.get("divisionAbbrev", "")), "conf": t.get("conferenceName", t.get("conferenceAbbrev", "")),
            "gp": int(num(t.get("gamesPlayed"))), "w": int(num(t.get("wins"))), "l": int(num(t.get("losses"))),
            "otl": int(num(t.get("otLosses"))), "pts": int(num(t.get("points"))),
            "gf": int(num(t.get("goalFor"))), "ga": int(num(t.get("goalAgainst"))),
            "diff": int(num(t.get("goalDifferential"))),
            "l10": "%d-%d-%d" % (num(t.get("l10Wins")), num(t.get("l10Losses")), num(t.get("l10OtLosses"))),
            "strk": "%s%s" % (t.get("streakCode", ""), int(num(t.get("streakCount")))) if t.get("streakCode") else "",
            "dseq": int(num(t.get("divisionSequence"))), "cseq": int(num(t.get("conferenceSequence"))),
            "lseq": int(num(t.get("leagueSequence")))})
    print("standings teams:", len(standings))
else:
    print("no standings")

# ---- team stats with league rank
team = {}
if season:
    url = ("https://api.nhle.com/stats/rest/en/team/summary?cayenneExp=seasonId=%s%%20and%%20gameTypeId=2" % season)
    ts = get(url)
    rows = (ts or {}).get("data", [])
    if rows:
        print("team rows:", len(rows), "| keys:", sorted(rows[0].keys()))
        METRICS = [("gf", "goalsForPerGame", True), ("ga", "goalsAgainstPerGame", False),
                   ("pp", "powerPlayPct", True), ("pk", "penaltyKillPct", True),
                   ("sf", "shotsForPerGame", True), ("sa", "shotsAgainstPerGame", False),
                   ("fo", "faceoffWinPct", True), ("pt", "pointPct", True)]
        van = next((r for r in rows if "Vancouver" in str(r.get("teamFullName", ""))), None)
        if van:
            for key, field, high in METRICS:
                vals = [num(r.get(field)) for r in rows if r.get(field) is not None]
                if van.get(field) is None or not vals: continue
                v = num(van[field])
                rank = 1 + sum(1 for x in vals if (x > v if high else x < v))
                team[key] = {"v": (pct(v) if key in ("pp", "pk", "fo", "pt") else round(v, 2)), "rank": rank}
    else:
        print("no team rows")

data = {"season": season, "skaters": skaters, "goalies": goalies, "standings": standings, "team": team}

try:
    old = json.load(open("stats.json", encoding="utf-8"))
except Exception:
    old = {}
if {k: v for k, v in old.items() if k != "updated"} == data:
    print("no changes"); sys.exit(0)
data["updated"] = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
json.dump(data, open("stats.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print("stats.json updated:", len(skaters), "skaters,", len(goalies), "goalies,", len(standings), "teams,", len(team), "team stats")
