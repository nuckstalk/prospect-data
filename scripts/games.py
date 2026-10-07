"""Nightly: write the Canucks' last 3 results and next 3 games to games.json
from the NHL's public schedule. Leaves the file alone if anything looks wrong."""
import json, sys, datetime, urllib.request
from zoneinfo import ZoneInfo

URL = "https://api-web.nhle.com/v1/club-schedule-season/VAN/now"
PT = ZoneInfo("America/Vancouver")
try:
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    sched = json.load(urllib.request.urlopen(req, timeout=30))
except Exception as e:
    print("FETCH FAILED:", e); sys.exit(0)

games = [g for g in sched.get("games", []) if g.get("startTimeUTC") and g.get("gameType", 2) >= 2]  # skip preseason

def team_name(t):
    place = (t.get("placeName") or {}).get("default", "")
    common = (t.get("commonName") or {}).get("default", "")
    return (place + " " + common).strip() or t.get("abbrev", "?")

def local(g):
    return datetime.datetime.fromisoformat(g["startTimeUTC"].replace("Z", "+00:00")).astimezone(PT)

results, upcoming = [], []
for g in sorted(games, key=lambda x: x["startTimeUTC"]):
    home, away = g["homeTeam"], g["awayTeam"]
    van_home = home.get("abbrev") == "VAN"
    opp = away if van_home else home
    d = local(g)
    base = {"opp": team_name(opp), "abbr": opp.get("abbrev", ""), "home": van_home}
    if g.get("gameState") in ("FINAL", "OFF"):
        try:
            us, them = (int(home["score"]), int(away["score"])) if van_home else (int(away["score"]), int(home["score"]))
        except Exception:
            continue
        period = (g.get("gameOutcome") or {}).get("lastPeriodType", "REG")
        res = "W" if us > them else ("L" if period == "REG" else ("OTL" if period == "OT" else "SOL"))
        results.append(dict(base, date=d.strftime("%b ") + str(d.day), us=us, them=them, res=res))
    elif g.get("gameState") in ("FUT", "PRE"):
        upcoming.append(dict(base, date=d.strftime("%a %b ") + str(d.day),
                             time=d.strftime("%I:%M %p").lstrip("0") + " PT"))

out = {"updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
       "results": results[-3:][::-1], "upcoming": upcoming[:3]}
if not out["results"] and not out["upcoming"]:
    print("nothing found - no change"); sys.exit(0)
try:
    old = json.load(open("games.json", encoding="utf-8"))
except Exception:
    old = {}
if old.get("results") == out["results"] and old.get("upcoming") == out["upcoming"]:
    print("no change"); sys.exit(0)
json.dump(out, open("games.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print("games.json updated:", len(out["results"]), "results,", len(out["upcoming"]), "upcoming")
