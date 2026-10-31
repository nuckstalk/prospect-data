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


def ht(params, quick=False):
    global KEY
    for attempt in range(1 if quick else 2):
        q = dict(params, key=KEY, client_code="ahl", fmt="json", lang="en")
        for host in (HOSTS[:1] if quick else HOSTS):
            url = host + "?" + urllib.parse.urlencode(q)
            for hi, hd in enumerate(HEADERS[:1] if quick else HEADERS):
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
RAW = {}


def players(position):
    r = ht({"feed": "statviewfeed", "view": "players", "season": sid, "season_id": sid, "team": tid, "team_id": tid,
            "position": position, "rookie": "no", "statsType": "standard", "rosterstatus": "undefined",
            "site_id": "3", "first": "0", "limit": "200", "sort": "points" if position == "skaters" else "games_played",
            "league_id": "4", "division": "-1", "conference": "-1", "qualified": "all"})
    rows = [x for x in rows_of(r) if "properties" not in x or len(x) > 1]
    mine_rows = [x for x in rows if str(x.get("team_code", "")).upper() == abb_code]
    if mine_rows: rows = mine_rows
    for x in rows: RAW[str(g(x, "player_id", "id"))] = x
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
skaters = [s for s in skaters if s["name"] and s["pos"].upper() != "G"]
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

# ---- game-by-game logs (newest first) for the Trends tab
try:
    prev = json.load(open("abby.json", encoding="utf-8"))
except Exception:
    prev = {}
prev_logs = {str(p.get("id")): p.get("log") for p in prev.get("skaters", []) + prev.get("goalies", []) if p.get("log")}

# ---- birth dates (for the Age column). Never lets a failure break the update.
def _bd(v):
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", str(v or ""))
    return m.group(1) if m else None


def find_bd(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if str(k).lower() in ("birthdate", "birth_date", "dob", "birthday", "date_of_birth") and _bd(v): return _bd(v)
        for v in o.values():
            r = find_bd(v)
            if r: return r
    elif isinstance(o, list):
        for v in o:
            r = find_bd(v)
            if r: return r
    return None


try:
    bds = {str(p.get("id")): p.get("bd") for p in prev.get("skaters", []) + prev.get("goalies", []) if p.get("bd")}
    for p in skaters + goalies:
        k = str(p.get("id"))
        if k not in bds and find_bd(RAW.get(k)): bds[k] = find_bd(RAW.get(k))
    for p in skaters + goalies:
        k = str(p.get("id"))
        if k not in bds and p.get("id"):
            for at in ({"feed": "modulekit", "view": "player"}, {"feed": "statviewfeed", "view": "player"}):
                b = find_bd(ht(dict(at, player_id=k), quick=True))
                if b: bds[k] = b; break
    for p in skaters + goalies:
        if bds.get(str(p.get("id"))): p["bd"] = bds[str(p.get("id"))]
    print("birth dates:", sum(1 for p in skaters + goalies if p.get("bd")), "of", len(skaters) + len(goalies))
except Exception as e:
    print("birth dates skipped:", e)

# ---- career season-by-season (AHL player profile). Never lets a failure break the update.
CSHOWN = []

def season_rows(o):
    """Find the list of per-season rows inside the player profile, whatever the feed calls it."""
    best = None
    def walk(v, key=""):
        nonlocal best
        if isinstance(v, list) and v and all(isinstance(i, dict) for i in v) and any(("season_name" in i or "season" in i) and ("games_played" in i or "gp" in i) for i in v):
            score = (0 if "regular" in key.lower() else 1 if "career" not in key.lower() else 2)
            if best is None or score < best[0]: best = (score, v)
        if isinstance(v, dict):
            for k, c in v.items(): walk(c, str(k))
        elif isinstance(v, list):
            for c in v: walk(c, key)
    walk(o)
    return best[1] if best else None

def career_of(resp, goalie):
    rows = season_rows(resp)
    if not rows: return None
    out = []
    for r in rows:
        if str(g(r, "playoff", default="0")) in ("1", "True", "true"): continue
        lg = str(g(r, "league_code", "league_name", "league", default="")).lower()
        if lg and "ahl" not in lg and "american hockey" not in lg: continue   # AHL seasons only
        season = str(g(r, "season_name", "season", default=""))
        team = str(g(r, "team_name", "team_city", "team", "team_code", default=""))
        gp = ival(r, "games_played", "gp")
        if not season or not gp: continue
        if goalie:
            out.append([season, team, gp, ival(r, "wins"), ival(r, "losses"), ival(r, "ot_losses", "overtime_losses") + ival(r, "shootout_losses"),
                        round(num(g(r, "goals_against_average", "gaa")), 2), svp(g(r, "save_percentage", "sv_pct"))])
        else:
            out.append([season, team, gp, ival(r, "goals"), ival(r, "assists"), ival(r, "points")])
    if len(CSHOWN) < 1 and rows:
        CSHOWN.append(1); print("career row keys:", sorted(rows[0].keys()))
    return out or None

try:
    prev_c = {str(p.get("id")): p.get("career") for p in prev.get("skaters", []) + prev.get("goalies", []) if p.get("career")}
    got = 0
    for grp, isg in ((skaters, False), (goalies, True)):
        for p in grp:
            k = str(p.get("id")); rows = None
            for at in ({"feed": "modulekit", "view": "player"}, {"feed": "statviewfeed", "view": "player"}):
                resp = ht(dict(at, player_id=k, season_id=sid), quick=True)
                rows = career_of(resp, isg) if resp else None
                if rows: break
            if rows: p["career"] = rows; got += 1
            elif prev_c.get(k): p["career"] = prev_c[k]
    print("career rows fetched for", got, "of", len(skaters) + len(goalies))
except Exception as e:
    print("career skipped:", e)


def find_games(obj):
    """The longest list of per-game dicts anywhere in a response (rows with a date and a stat)."""
    best = []
    def walk(o):
        nonlocal best
        if isinstance(o, list):
            flat = [(x["row"] if isinstance(x.get("row"), dict) else x) for x in o if isinstance(x, dict)]
            if flat and any(any("date" in k for k in r) for r in flat) and \
               any(("goals" in r or "saves" in r or "shots_against" in r or "goals_against" in r or "points" in r) for r in flat):
                if len(flat) > len(best): best = flat
            for x in o: walk(x)
        elif isinstance(o, dict):
            for v in o.values(): walk(v)
    walk(obj)
    return best


def gdate(r):
    for k in ("date_played", "game_date", "date", "game_date_iso_8601"):
        if r.get(k): v = str(r[k]); break
    else:
        v = next((str(val) for key, val in r.items() if "date" in key and val), "")
    m = re.search(r"\d{4}-\d{2}-\d{2}", v)
    return m.group(0) if m else v


def mins(v):
    v = str(v or "").strip()
    try:
        if ":" in v:
            a, b = v.split(":")[:2]; return int(num(a)) * 60 + int(num(b))
        return int(num(v) * 60)
    except Exception:
        return 0


def decision(r):
    low = {str(k).lower(): v for k, v in r.items()}
    for k in ("decision", "result", "game_result", "win_loss", "outcome", "record"):
        d = str(low.get(k, "")).strip().upper()
        if d:
            if d[:1] == "W": return "W"
            if d[:2] in ("OT", "SO") or "OT" in d: return "OTL"
            if d[:1] == "L": return "L"
    def on(*names): return any(num(low.get(n)) > 0 for n in names)
    if on("win", "wins", "w"): return "W"
    if on("ot_loss", "ot_losses", "otl", "overtime_loss", "overtime_losses", "so_loss", "shootout_loss", "shootout_losses", "sol"): return "OTL"
    if on("loss", "losses", "l"): return "L"
    return ""


LOGGED = {"shown": 0}
ATTEMPTS = [
    {"feed": "modulekit", "view": "player", "category": "gamebygame"},
    {"feed": "statviewfeed", "view": "player", "statsType": "standard", "site_id": "3", "league_id": "4"},
    {"feed": "modulekit", "view": "player", "category": "lastgames"},
]


def get_log(pid, goalie):
    for at in ATTEMPTS:
        resp = ht(dict(at, player_id=str(pid), season_id=sid, season=sid), quick=True)
        games = [x for x in find_games(resp) if gdate(x)]
        if not games:
            if LOGGED.get("miss", 0) < 3 and resp is not None:
                LOGGED["miss"] = LOGGED.get("miss", 0) + 1
                print("no games found via", at.get("category") or at.get("view"), "| response starts:", json.dumps(resp)[:300])
            continue
        games.sort(key=lambda x: (gdate(x), str(x.get("game_id", ""))), reverse=True)
        if LOGGED["shown"] < 2:
            LOGGED["shown"] += 1
            print("log source:", at.get("category") or at.get("view"), "| games:", len(games), "| game row keys:", sorted(games[0].keys()))
        out = []
        for x in games[:90]:
            if goalie:
                ga = ival(x, "goals_against"); sa = ival(x, "shots_against", "shots") or (ival(x, "saves") + ga)
                toi = mins(g(x, "minutes_played", "time_on_ice", "toi", "minutes", "ice_time_minutes_seconds")) or 3600
                if not LOGGED.get("g"):
                    LOGGED["g"] = 1; print("goalie game row:", json.dumps(x)[:700])
                out.append([gdate(x), decision(x), sa, ga, toi, ival(x, "shutouts"), ival(x, "games_started") or 1])
            else:
                gg, aa = ival(x, "goals"), ival(x, "assists")
                out.append([gdate(x), gg, aa, ival(x, "points") or gg + aa, ival(x, "plus_minus"), ival(x, "penalty_minutes"),
                            ival(x, "shots"), ival(x, "power_play_goals") + ival(x, "power_play_assists"),
                            mins(g(x, "ice_time_minutes_seconds", "time_on_ice", "toi"))])
        return out
    return None


logged = 0
for p in skaters + goalies:
    gl = get_log(p["id"], p in goalies) if p.get("id") else None
    if gl is None:
        p["log"] = prev_logs.get(str(p.get("id")), []); continue
    p["log"] = gl; logged += 1
print("game logs fetched:", logged, "of", len(skaters) + len(goalies))
if skaters and skaters[0].get("log"): print("sample log row:", skaters[0]["log"][0])

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

# ---- safety net: the AHL standings/season-total feeds can lag a game behind the game-by-game logs.
# If the logs show more Abbotsford games than the standings row, add the missing newest game(s) on top.
def catch_up():
    me = next((t for t in standings if t["abbr"] == "ABB"), None)
    if not me: return
    res = {}
    for gl in goalies:
        for row in gl.get("log") or []:
            d, dec = row[0], row[1]
            if d not in res or (dec and not res[d]): res[d] = dec
    dates = sorted(res)
    miss = len(dates) - me["gp"]
    if miss <= 0 or miss > 3: return
    add = dates[-miss:]
    if any(not res[d] for d in add): return
    print("standings lag: adding", add, "to ABB row")
    for d in add:
        dec = res[d]
        me["gp"] += 1
        idx = {"W": 0, "L": 1, "OTL": 2}[dec]
        if dec == "W": me["w"] += 1; me["pts"] += 2
        elif dec == "L": me["l"] += 1
        else: me["otl"] += 1; me["pts"] += 1
        for f in ("l10", "strk"):
            parts = [int(x) for x in re.findall(r"\d+", me.get(f) or "")]
            if len(parts) < 3: parts = [0, 0, 0, 0]
            parts = (parts + [0, 0, 0, 0])[:4]
            if f == "strk":
                only = [i for i in range(4) if parts[i] > 0]
                cur = parts[idx] if only == [idx] else 0
                parts = [0, 0, 0, 0]; parts[idx] = cur + 1
            else:
                parts[idx] += 1
            me[f] = "-".join(str(x) for x in parts)
    for gl in goalies:
        for row in sorted([r for r in (gl.get("log") or []) if r[0] in add], key=lambda r: r[0]):
            dec, sa, ga, toi = row[1], row[2], row[3], row[4] or 3600
            old_min = (gl["ga"] * 3600.0 / gl["gaa"]) if gl.get("gaa") and gl.get("ga") else toi * gl["gp"]
            gl["gp"] += 1; gl["sa"] += sa; gl["ga"] += ga; gl["sv"] += sa - ga
            if dec == "W": gl["w"] += 1
            elif dec == "L": gl["l"] += 1
            elif dec == "OTL": gl["otl"] += 1
            if len(row) > 5 and row[5]: gl["so"] = (gl.get("so") or 0) + 1
            gl["gaa"] = round(gl["ga"] * 3600.0 / (old_min + toi), 2)
            gl["svp"] = ("%.3f" % (gl["sv"] / gl["sa"])).lstrip("0") if gl["sa"] else gl.get("svp")


try:
    catch_up()
except Exception as e:
    print("catch_up skipped:", e)


# ---- streak: the feed's streak string can be ambiguous (e.g. "1-0-0-1"), so work it out from the game logs
def fix_streak():
    me = next((t for t in standings if t["abbr"] == "ABB"), None)
    if not me: return
    res = {}
    for gl in goalies:
        for row in gl.get("log") or []:
            if row[0] not in res or (row[1] and not res[row[0]]): res[row[0]] = row[1]
    seq_ = [res[d] for d in sorted(res, reverse=True)]
    if not seq_ or not seq_[0] or len(seq_) < me["gp"]: return
    n = 0
    for r in seq_:
        if r != seq_[0]: break
        n += 1
    idx = {"W": 0, "L": 1, "OTL": 2}[seq_[0]]
    parts = [0, 0, 0, 0]; parts[idx] = n
    me["strk"] = "-".join(str(x) for x in parts)


try:
    fix_streak()
except Exception as e:
    print("fix_streak skipped:", e)


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
