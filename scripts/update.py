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
    run_next()
    st = old.get("status") or {}
    if st.get("msg") != msg:
        out = json.loads(json.dumps(old))
        out["status"] = {"msg": msg, "at": NOW.strftime("%Y-%m-%dT%H:%MZ")}
        json.dump(out, open("prospects.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    sys.exit(0)

def run_next():
    global data
    # =============================================================================================
    # ---- Next game for each prospect (AHL / OHL / WHL for now) -> next.json ----
    # Runs AFTER everything else and can never stop the normal update: any problem here is just printed.
    try:
        import urllib.parse as _up
        N_FEEDS = {"AHL": ("ahl", "ccb91f29d6744675", "https://theahl.com/"),
                   "OHL": ("ohl", "2976319eb44abe94", "https://ontariohockeyleague.com/"),
                   "WHL": ("whl", ["41b145a848f4bd67", "f1aa699db3d81487", "2976319eb44abe94"], "https://chl.ca/whl/"),
                   "ECHL": ("echl", ["2c2b89ea7345cae8", "ccb91f29d6744675", "50c2cd9b5e18e390"], "https://echl.com/")}
        N_EXTRA = {"ahl": ["https://theahl.com/schedule", "https://theahl.com/stats"], "whl": ["https://whl.ca/", "https://chl.ca/whl/schedule/"]}
        PT = ZoneInfo("America/Vancouver")

        def n_try(code, key, ref, quick=False):
            last = None
            for ahead, back in (((14, 0),) if quick else ((14, 0), (7, 1), (3, 0), (0, 1), (2, 1))):
                for host in (("https://lscluster.hockeytech.com/feed/index.php",) if quick else ("https://lscluster.hockeytech.com/feed/index.php", "https://lscluster.hockeytech.com/feed/")):
                    for hd in ({"User-Agent": "Mozilla/5.0", "Referer": ref, "Origin": ref.rstrip("/")},
                               {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                                "Accept": "application/json, text/javascript, */*; q=0.01", "Referer": ref.replace("https://", "https://www.") if "www." not in ref else ref, "Origin": ref.rstrip("/")}):
                        q = {"feed": "modulekit", "view": "scorebar", "numberofdaysahead": ahead, "numberofdaysback": back,
                             "key": key, "client_code": code, "fmt": "json", "lang": "en"}
                        try:
                            req = urllib.request.Request(host + "?" + _up.urlencode(q), headers=hd)
                            with urllib.request.urlopen(req, timeout=40) as r:
                                raw = r.read().decode("utf-8", "replace").strip()
                            raw2 = re.sub(r"^[\w.$]*\(", "", raw); raw2 = re.sub(r"\)\s*;?\s*$", "", raw2)
                            return json.loads(raw2)
                        except Exception as e:
                            last = str(e)[:80]
                            try: print("next games:", code, "try ahead", ahead, "back", back, host[-6:], "->", last, "|", raw[:100].replace("\n", " ") if 'raw' in dir() else "")
                            except Exception: pass
            raise Exception("all tries failed: " + str(last))

        def n_keys(refs):
            pat = re.compile(r"""["']?(?:key|apiKey|api_key|hockeytech[a-z_]*)["']?\s*[:=]\s*["']([0-9a-f]{16})["']""", re.I)
            out = []
            for u in refs:
                try:
                    _o = "/".join(u.split("/")[:3])
                    hd = {"User-Agent": "Mozilla/5.0", "Referer": _o + "/", "Origin": _o}
                    home = urllib.request.urlopen(urllib.request.Request(u, headers=hd), timeout=30).read().decode("utf-8", "replace")
                    texts = [home]
                    for src in re.findall(r'src=["\']([^"\']+\.js[^"\']*)["\']', home)[:14]:
                        try: texts.append(urllib.request.urlopen(urllib.request.Request(_up.urljoin(u, src), headers=hd), timeout=30).read().decode("utf-8", "replace"))
                        except Exception: pass
                    for t in texts:
                        for m in pat.finditer(t):
                            if m.group(1) not in out: out.append(m.group(1))
                except Exception as e:
                    print("next games: key search failed on", u, str(e)[:60])
            return out

        def n_fetch(code, key, ref):
            last = None
            for k in (key if isinstance(key, list) else [key]):
                try:
                    r = n_try(code, k, ref, quick=True)
                    print("next games:", code, "key worked:", k)
                    return r
                except Exception as e:
                    last = str(e)[:80]
            raise Exception("no working key for " + code + " (" + str(last) + ")")

        def n_ht(code, key, ref, params):
            q = dict(params, key=key, client_code=code, fmt="json", lang="en")
            for host in ("https://lscluster.hockeytech.com/feed/index.php",):
                try:
                    req = urllib.request.Request(host + "?" + _up.urlencode(q), headers={"User-Agent": "Mozilla/5.0", "Referer": ref, "Origin": ref.rstrip("/")})
                    raw = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace").strip()
                    raw = re.sub(r"^[\w.$]*\(", "", raw); raw = re.sub(r"\)\s*;?\s*$", "", raw)
                    return json.loads(raw)
                except Exception as e:
                    print("next games:", code, params.get("view"), "failed:", str(e)[:70])
            return None

        def n_schedule(code, key, ref, teams):
            """Fallback: season -> team ids -> each team's schedule. Returns raw game rows."""
            ss = ((n_ht(code, key, ref, {"feed": "modulekit", "view": "seasons"}) or {}).get("SiteKit") or {}).get("Seasons") or []
            reg = [x for x in ss if str(x.get("playoff")) == "0" and "regular" in str(x.get("season_name", "")).lower()]
            lim = (NOW + datetime.timedelta(days=10)).strftime("%Y-%m-%d")
            st = [x for x in reg if str(x.get("start_date", "9999")) <= lim] or reg
            st.sort(key=lambda x: (str(x.get("start_date", "")), str(x.get("season_id", ""))))
            print("next games:", code, "seasons:", len(ss), "regular:", len(reg))
            if not st: return []
            sid = str(st[-1]["season_id"])
            tb = ((n_ht(code, key, ref, {"feed": "modulekit", "view": "teamsbyseason", "season_id": sid}) or {}).get("SiteKit") or {}).get("Teamsbyseason") or []
            print("next games:", code, "season", sid, "teams:", len(tb))
            rows = []
            for t in tb:
                tn = str(t.get("name", "")) + " " + str(t.get("city", "")) + " " + str(t.get("nickname", ""))
                if not any(n_match(tm, tn) or n_match(tm, str(t.get("name", ""))) for tm in teams): continue
                sc = (n_ht(code, key, ref, {"feed": "modulekit", "view": "schedule", "season_id": sid, "team_id": t.get("id")}) or {}).get("SiteKit") or {}
                got = sc.get("Schedule") or []
                print("next games:", code, "schedule for", t.get("name"), "->", len(got), "rows", ("| keys: " + str(sorted(got[0].keys())[:30])) if got else "")
                rows += got
            return rows

        def n_games(obj):
            found = []
            def walk(o):
                if isinstance(o, dict):
                    if any(k in o for k in ("HomeLongName", "HomeCity", "home_team_city")) : found.append(o)
                    else:
                        for v in o.values(): walk(v)
                elif isinstance(o, list):
                    for v in o: walk(v)
            walk(obj); return found

        def n_name(g, side):
            for k in (side + "LongName", side + "_long_name"):
                if g.get(k): return str(g[k]).strip()
            sn = "home" if side == "Home" else "visiting"
            if g.get(sn + "_team_city") or g.get(sn + "_team_name"):
                return (str(g.get(sn + "_team_city", "")) + " " + str(g.get(sn + "_team_nickname", g.get(sn + "_team_name", "")))).strip()
            return (str(g.get(side + "City", "")) + " " + str(g.get(side + "Nickname", ""))).strip()

        def n_when(g):
            iso = g.get("GameDateISO8601") or g.get("game_date_iso_8601")
            if iso:
                try:
                    dt = datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
                    if dt.tzinfo: return dt.astimezone(PT)
                except Exception: pass
            return None

        def n_match(team, full):
            def _nn(x):
                return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().lower())
            a, b = _nn(team), _nn(full)
            return bool(a and b and (a == b or a in b or b in a))

        nx_res = {}
        nx_all = {}
        nx_by = {}
        def n_add(nm, lg, when, home, away, ish, live):
            """Every game in the next 7 days (for the Schedule tab)."""
            when = when.astimezone(PT)
            if when < NOW - datetime.timedelta(hours=4) or when > NOW + datetime.timedelta(days=7): return
            rec = {"lg": lg, "iso": when.isoformat(), "date": when.strftime("%a %b ") + str(when.day),
                   "time": when.strftime("%-I:%M %p") + " PT", "opp": away if ish else home, "home": ish, "live": live}
            lst = nx_all.setdefault(nm, [])
            if not any(x["iso"] == rec["iso"] for x in lst): lst.append(rec)
        for grp in ("skaters", "goalies"):
            for nm, rec in (data.get(grp) or {}).items():
                nx_by.setdefault(rec.get("lg"), []).append((nm, rec.get("team", "")))
        for lg, plist in nx_by.items():
            if lg not in N_FEEDS: continue
            code, key, ref = N_FEEDS[lg]
            try:
                if lg == "AHL": raise Exception("AHL comes from abby.json")
                sb = n_fetch(code, key, ref)
                gl = n_games(sb)
            except Exception as e:
                print("next games:", lg, "scorebar failed:", str(e)[:100], "- trying team schedules")
                gl = []
                if lg == "AHL":
                    try:
                        _ab2 = json.load(open("abby.json", encoding="utf-8"))
                        _nx = _ab2.get("next")
                        _sc = _ab2.get("sched") or ([_nx] if _nx else [])
                        if _nx:
                            for nm, tm in plist:
                                if "abbotsford" in tm.lower():
                                    nx_res[nm] = dict(_nx, lg="AHL")
                                    for _g in _sc:
                                        if not any(x.get("iso") == _g.get("iso") for x in nx_all.setdefault(nm, [])): nx_all[nm].append(dict(_g, lg="AHL"))
                            print("next games: AHL from abby.json:", _nx.get("date"), _nx.get("opp"), "| games:", len(_sc))
                    except Exception as e3: print("next games: abby.json next unavailable:", str(e3)[:80])
                    continue
                try: gl = n_schedule(code, (key[0] if isinstance(key, list) else key), ref, [tm for _, tm in plist])
                except Exception as e2: print("next games:", lg, "schedule failed:", str(e2)[:120])
                if not gl: continue
            print("next games:", lg, "feed games:", len(gl), "| sample keys:", sorted(gl[0].keys())[:24] if gl else "none")
            sched = []
            for g in gl:
                gs = (str(g.get("GameStatusString", "")) + " " + str(g.get("GameStatusStringLong", ""))).lower()
                fin = str(g.get("Final", g.get("final", "0"))) == "1" or "final" in gs or str(g.get("game_status", "")).lower().startswith("final")
                if fin: continue
                when = n_when(g)
                if not when: continue
                if when < NOW - datetime.timedelta(hours=4): continue
                sched.append((when, n_name(g, "Home"), n_name(g, "Visitor"), str(g.get("Started", g.get("started", "0"))) == "1"))
            sched.sort(key=lambda x: x[0])
            for nm, team in plist:
                _first = True
                for when, home, away, live in sched:
                    if n_match(team, home) or n_match(team, away):
                        ish = n_match(team, home)
                        n_add(nm, lg, when, home, away, ish, live)
                        if _first:
                            nx_res[nm] = {"lg": lg, "iso": when.isoformat(), "date": when.strftime("%a %b ") + str(when.day),
                                          "time": when.strftime("%-I:%M %p") + " PT", "opp": away if ish else home, "home": ish, "live": live}
                            _first = False
        # ---- AHL: try the league feed for the next two weeks (best effort; the single next game above stays as the fallback) ----
        try:
            _pl = nx_by.get("AHL") or []
            if _pl:
                _c, _k, _r = N_FEEDS["AHL"]
                _gl = n_games(n_fetch(_c, _k, _r))
                _sc = []
                for g in _gl:
                    _gs = (str(g.get("GameStatusString", "")) + " " + str(g.get("GameStatusStringLong", ""))).lower()
                    if str(g.get("Final", g.get("final", "0"))) == "1" or "final" in _gs: continue
                    _w = n_when(g)
                    if not _w or _w < NOW - datetime.timedelta(hours=4): continue
                    _sc.append((_w, n_name(g, "Home"), n_name(g, "Visitor"), str(g.get("Started", g.get("started", "0"))) == "1"))
                _sc.sort(key=lambda x: x[0])
                print("next games: AHL feed games:", len(_sc))
                for nm, team in _pl:
                    for _w, _h, _a, _l in _sc:
                        if n_match(team, _h) or n_match(team, _a):
                            n_add(nm, "AHL", _w, _h, _a, n_match(team, _h), _l)
        except Exception as e:
            print("next games: AHL full schedule skipped:", str(e)[:100])

        def n_put(nm, lg, when, home, away, ish, live):
            when = when.astimezone(PT)
            n_add(nm, lg, when, home, away, ish, live)
            _cur = nx_res.get(nm)
            if _cur:
                try:
                    if datetime.datetime.fromisoformat(_cur["iso"]) <= when: return
                except Exception: pass
            nx_res[nm] = {"lg": lg, "iso": when.isoformat(), "date": when.strftime("%a %b ") + str(when.day),
                          "time": when.strftime("%-I:%M %p") + " PT", "opp": away if ish else home, "home": ish, "live": live}

        def n_get(url):
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            return json.loads(urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace"))

        # ---- NCAA via ESPN's public scoreboard ----
        try:
            def _cl(x):
                x = re.sub(r"\(.*?\)", "", str(x)).lower()
                x = re.sub(r"\b(univ|university|of|the)\b\.?", "", x)
                return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode())
            nc = nx_by.get("NCAA") or []
            if nc:
                ev = []
                for _i in range(0, 15):
                    _d = (NOW + datetime.timedelta(days=_i)).strftime("%Y%m%d")
                    try:
                        ev += n_get("https://site.api.espn.com/apis/site/v2/sports/hockey/mens-college-hockey/scoreboard?limit=300&dates=" + _d).get("events") or []
                    except Exception as _e:
                        print("next games: NCAA day", _d, "failed:", str(_e)[:80])
                        if _i == 0 and "400" in str(_e): break
                print("next games: NCAA events:", len(ev), "| sample:", (ev[0].get("name") if ev else "none"))
                rows = []
                for e in ev:
                    try:
                        ty = (e.get("status") or {}).get("type") or {}
                        if ty.get("completed"): continue
                        when = datetime.datetime.fromisoformat(str(e["date"]).replace("Z", "+00:00"))
                        if when < NOW - datetime.timedelta(hours=4): continue
                        cs = e["competitions"][0]["competitors"]
                        h = next(c for c in cs if c.get("homeAway") == "home")["team"]; a = next(c for c in cs if c.get("homeAway") == "away")["team"]
                        rows.append((when, h, a, ty.get("state") == "in"))
                    except Exception: continue
                rows.sort(key=lambda x: x[0])
                for nm, tm in nc:
                    k = _cl(tm)
                    for when, h, a, live in rows:
                        hl, al = _cl(h.get("location", h.get("displayName", ""))), _cl(a.get("location", a.get("displayName", "")))
                        if k and (k == hl or k == al):
                            ish = (k == hl)
                            n_put(nm, "NCAA", when, h.get("displayName", ""), a.get("displayName", ""), ish, live)
        except Exception as e:
            print("next games: NCAA failed:", str(e)[:120])

        # ---- everything else (Europe, MHL, U20, ...) via TheSportsDB, when we have no game yet ----
        try:
            for lg, plist in nx_by.items():
                if lg in ("AHL", "OHL", "NCAA"): continue
                for nm, tm in plist:
                    if nm in nx_res: continue
                    try:
                        q = n_get("https://www.thesportsdb.com/api/v1/json/123/searchteams.php?t=" + _up.quote(tm))
                        teams = q.get("teams") or []
                        print("next games:", lg, nm, "| teams found for", tm, ":", [t.get("strTeam") for t in teams][:3])
                        teams = [t for t in teams if n_match(tm, t.get("strTeam", "")) or n_match(re.sub(r"\b(U20|J20|U-20)\b", "", tm).strip(), t.get("strTeam", ""))]
                        if not teams: continue
                        tid = teams[0].get("idTeam")
                        evs = (n_get("https://www.thesportsdb.com/api/v1/json/123/eventsnext.php?id=" + str(tid)).get("events") or [])
                        for e in evs:
                            ts = e.get("strTimestamp") or ((e.get("dateEvent") or "") + "T" + (e.get("strTime") or "00:00:00"))
                            when = datetime.datetime.fromisoformat(str(ts).replace("Z", "")).replace(tzinfo=datetime.timezone.utc)
                            if when < NOW - datetime.timedelta(hours=4): continue
                            hm, aw = str(e.get("strHomeTeam", "")), str(e.get("strAwayTeam", ""))
                            n_put(nm, lg, when, hm, aw, n_match(tm, hm), False)
                    except Exception as e:
                        print("next games:", lg, nm, "lookup failed:", str(e)[:80])
        except Exception as e:
            print("next games: other leagues failed:", str(e)[:120])

        # ---- Eriksson (Färjestad U20): schedule copied from EliteProspects (times are Pacific, as shown there) ----
        try:
            EK = """2026-11-14 06:00|H|HV71 U20
2026-11-15 04:00|H|Frölunda HC U20
2026-11-21 07:45|H|Rögle BK U20
2026-11-22 04:45|H|Rögle BK U20
2026-11-26 11:00|A|Västerås IK U20
2026-11-28 04:00|A|Örebro HK U20
2026-12-05 07:00|A|Södertälje SK U20
2026-12-06 04:00|A|Linköping HC U20
2026-12-12 08:00|H|Tingsryds AIF U20
2026-12-13 04:45|H|Växjö Lakers HC U20
2027-01-06 05:00|H|BIK Karlskoga U20
2027-01-09 06:00|H|Frölunda HC U20
2027-01-10 04:00|H|HV71 U20
2027-01-13 11:00|A|BIK Karlskoga U20
2027-01-16 07:45|A|Malmö Redhawks U20
2027-01-17 04:00|A|Rögle BK U20
2027-01-22 11:15|H|Örebro HK U20
2027-01-24 05:00|H|Västerås IK U20
2027-01-30 07:00|H|Södertälje SK U20
2027-01-31 04:15|H|Linköping HC U20
2027-02-06 08:00|A|Tingsryds AIF U20
2027-02-07 04:00|A|Växjö Lakers HC U20
2027-02-20 04:00|A|BIK Karlskoga U20
2027-02-21 04:00|H|BIK Karlskoga U20
2027-02-26 11:00|A|Frölunda HC U20
2027-02-27 04:00|A|HV71 U20"""
            for nm, tm in (nx_by.get("U20") or []):
                if "rjestad" not in tm: continue
                for ln in EK.splitlines():
                    dt, ha, opp = ln.split("|")
                    when = datetime.datetime.strptime(dt, "%Y-%m-%d %H:%M").replace(tzinfo=PT)
                    if when < NOW - datetime.timedelta(hours=4): continue
                    n_put(nm, "U20", when, "Färjestad BK U20" if ha == "H" else opp, opp if ha == "H" else "Färjestad BK U20", ha == "H", False)
        except Exception as e:
            print("next games: Eriksson schedule failed:", str(e)[:100])

        # ---- Ivchenko (Omskie Yastreby, MHL): schedule copied from EliteProspects (Pacific times, as shown there) ----
        try:
            OM = """2026-11-06 07:00|A|MHK Molot Perm
2026-11-13 05:30|H|Mamonty Yugry
2026-11-18 04:30|A|Krasnoyarskie Rysi
2026-11-24 05:30|H|MHK Spartak Moskva
2026-11-26 05:30|H|Krasnaya Armiya Moskva
2026-11-29 12:00|H|Reaktor Nizhnekamsk
2026-12-02 05:30|H|Sputnik Almetievsk
2026-12-07 08:30|A|Loko-76 Yaroslavl
2026-12-08 08:30|A|Loko-76 Yaroslavl
2026-12-12 23:00|A|Sibirskie Snaipery Novosibirsk
2026-12-20 12:00|H|Krasnoyarskie Rysi
2026-12-22 05:30|H|MHK Molot Perm
2026-12-28 05:30|H|Loko Yaroslavl
2027-01-09 01:00|A|Tolpar Ufa
2027-01-10 01:00|A|Tolpar Ufa
2027-01-13 06:30|A|Stalnye Lisy Magnitogorsk
2027-01-14 06:30|A|Stalnye Lisy Magnitogorsk
2027-01-20 09:00|A|AKM-Yunior Tula Region
2027-01-26 05:30|H|Snezhnye Barsy Astana
2027-01-31 00:00|H|Loko-76 Yaroslavl
2027-02-01 05:30|H|Loko-76 Yaroslavl
2027-02-07 03:00|A|Irbis Kazan
2027-02-08 08:30|A|Irbis Kazan
2027-02-11 08:30|A|Chaika Nizhny Novgorod
2027-02-12 08:30|A|Chaika Nizhny Novgorod
2027-02-17 08:30|A|Reaktor Nizhnekamsk
2027-02-19 08:30|A|Sputnik Almetievsk
2027-02-23 00:00|H|Belye Medvedi Chelyabinsk
2027-02-24 05:30|H|Belye Medvedi Chelyabinsk
2027-02-27 00:00|H|Avto Yekaterinburg
2027-02-28 00:00|H|Avto Yekaterinburg
2027-03-06 00:00|H|Sibirskie Snaipery Novosibirsk
2027-03-10 08:30|A|MHK Spartak Moskva
2027-03-12 08:30|A|Loko Yaroslavl
2027-03-15 07:00|A|Krasnaya Armiya Moskva
2027-03-21 01:00|A|Mamonty Yugry"""
            for nm, tm in (nx_by.get("MHL") or []):
                if "mskie" not in tm and "Omsk" not in tm: continue
                for ln in OM.splitlines():
                    dt, ha, opp = ln.split("|")
                    when = datetime.datetime.strptime(dt, "%Y-%m-%d %H:%M").replace(tzinfo=PT)
                    if when < NOW - datetime.timedelta(hours=4): continue
                    n_put(nm, "MHL", when, "Omskie Yastreby" if ha == "H" else opp, opp if ha == "H" else "Omskie Yastreby", ha == "H", False)
        except Exception as e:
            print("next games: Ivchenko schedule failed:", str(e)[:100])

        # keep earlier entries (still in the future) for anyone a source missed this time
        try:
            for nm, g in (json.load(open("next.json", encoding="utf-8")).get("games") or {}).items():
                if nm not in nx_res and datetime.datetime.fromisoformat(g["iso"]) > NOW - datetime.timedelta(hours=4): nx_res[nm] = g
        except Exception: pass

        print("next games:", len(nx_res), "players with a scheduled game")
        # schedule for the Schedule tab: every game in the next 7 days, per player
        try:
            for nm, lst in (json.load(open("next.json", encoding="utf-8")).get("sched") or {}).items():
                if nm in nx_all: continue
                for g in lst:
                    if datetime.datetime.fromisoformat(g["iso"]) > NOW - datetime.timedelta(hours=4): nx_all.setdefault(nm, []).append(g)
        except Exception: pass
        for _l in nx_all.values(): _l.sort(key=lambda x: x["iso"])
        print("next games: schedule for", len(nx_all), "players,", sum(len(v) for v in nx_all.values()), "games in 7 days")
        if nx_res:
            out = {"asof": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "games": nx_res, "sched": nx_all}
            try:
                _pj = json.load(open("next.json", encoding="utf-8")); prev, prevs = _pj.get("games"), _pj.get("sched")
            except Exception: prev, prevs = None, None
            if prev != nx_res or prevs != nx_all:
                json.dump(out, open("next.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    except Exception as _e:
        print("next games skipped:", str(_e)[:200])

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
multi_rows = {}
totals_seen = set()

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
    cur_multi = None   # player on a "totals" row: the nameless rows right below it are his per-team lines
    for tr in table.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if cur_multi:
            print("  row after totals:", cells, "| player link:", bool(tr.find("a", href=re.compile(r"/player/"))), "| text:", list(tr.stripped_strings)[:4])
        _txt = [cells[1] if len(cells) > 1 else ""] + [x.get_text(" ", strip=True) for x in tr.find_all(["th", "a"])]
        _named = any(norm(re.sub(r"\([^)]*\)", "", t_)) in known_sk or norm(re.sub(r"\([^)]*\)", "", t_)) in known_g for t_ in _txt)
        _nameless = not _named and not tr.find("a", href=re.compile(r"/player/"))
        if cur_multi and len(cells) >= tail + 2 and _nameless:
            try:
                _t, _l, _v = cells[-(tail + 2)], cells[-(tail + 1)], cells[-tail:]
                if not _t.strip():   # the team name sits outside the <td>s on these rows: take it from the row's other text
                    _skip = set(cells)
                    _t = next((x for x in [y.get_text(" ", strip=True) for y in tr.find_all(["th", "a", "span"])] + list(tr.stripped_strings) if x and x not in _skip), "")
                    print("  team name found outside the cells:", repr(_t))
                if _t and _l and "totals" not in (_t.lower(), _l.lower()):
                    if is_goalie:
                        _sv = _v[2].strip(); _sv = _sv[1:] if _sv.startswith("0.") else _sv
                        _sp = {"team": _t, "lg": _l, "gp": to_int(_v[0]), "gaa": to_float(_v[1]), "svp": _sv, "so": to_int(_v[3]), "rec": _v[4].replace(" ", "")}
                        try:
                            _mm, _ss = _v[5].strip().split(":"); _sp["toi"] = round(int(_mm.replace(",", "")) + int(_ss) / 60, 2)
                        except Exception:
                            pass
                    else:
                        _sp = {"team": _t, "lg": _l, "gp": to_int(_v[0]), "g": to_int(_v[1]), "a": to_int(_v[2]), "tp": to_int(_v[3])}
                    multi_rows.setdefault(cur_multi, []).append(_sp)
            except Exception as e:
                print("SPLIT PARSE ERROR", cells[:4], e)
            continue
        if len(cells) < tail + 3: continue
        if tr.find("a", href=re.compile(r"/player/")): cur_multi = None
        cands = [cells[1]] + [x.get_text(" ", strip=True) for x in tr.find_all(["th", "a"])]
        key = None
        for cnd in cands:
            k = norm(re.sub(r"\([^)]*\)", "", cnd))
            if k in known_sk or k in known_g: key = k; break
        if key is None: continue
        team, lg = cells[-(tail + 2)], cells[-(tail + 1)]
        v = cells[-tail:]
        if "totals" in (team.strip().lower(), lg.strip().lower()):
            cur_multi = (key, "goalies" if is_goalie else "skaters"); totals_seen.add(cur_multi)
            print("totals row for", cells[:3], "- reading the team rows below it")
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

# players with two or more teams this season (traded / loaned): keep each team's line and show the CURRENT team (the last row)
for (_k, _grp), _rows in multi_rows.items():
    _rows = [x for x in _rows if x.get("gp", 0) > 0]   # a team with 0 games (e.g. a listed-only KHL club) is not a split
    _who = (known_g if _grp == "goalies" else known_sk).get(_k)
    if _who and len(_rows) >= 2:
        _rec = data[_grp][_who]
        setv(_rec, _who, "splits", _rows)
        setv(_rec, _who, "team", _rows[-1]["team"])
        setv(_rec, _who, "lg", _rows[-1]["lg"])
for _grp in ("skaters", "goalies"):
    for _who, _rec in data[_grp].items():
        _real = [x for x in multi_rows.get((norm(_who), _grp), []) if x.get("gp", 0) > 0]
        if "splits" in _rec and len(_real) < 2 and (norm(_who), _grp) not in totals_seen and not any(x.get("keep") for x in _rec["splits"]):
            changed.append((_who, "splits", "removed", None)); del _rec["splits"]

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
    C_VER = 4   # bump to make every player refresh once with new rules
    C_BUDGET_SECONDS = 600
    C_BAD = re.compile(r"U-?1\d|\bJ1\d|HS\b|High School|Prep|Midget|AAA|Bantam|Minor|Intern|WJC|WJ-?18|Hlinka|Memorial|Olympic|World|Nat\.|Five Nations|Euro Hockey|Junior Club|Jr\. ?Nat", re.I)
    C_BAD2 = re.compile(r"\b\d{2}U\b|\bB\d\b|Bantam|Midget|Mini|NTDP|Prep|\bU1\d\b", re.I)
    C_BAD_LG = {"GOJHL", "SJHL", "AJHL", "BCHL", "Latvia2", "AMBHL", "AMHL", "MDHL", "NMHL", "PHC", "WC", "OG", "OGQ", "WC-Q", "NTDP", "WJ20", "WJ18"}
    C_TEAM_BAD = re.compile(r"Projected|Olympics|National U1\d|U\.?S\.? National|Bantam|Midget|\bU1\d\b|\b1\dU\b|AAA|Prep|Academy \(", re.I)
    def c_keep(lg):
        lg = (lg or "").strip()
        if not lg or lg.endswith(" Q") or lg.lower().endswith("qualification"): return False
        if C_BAD.search(lg) or C_BAD2.search(lg): return False
        return lg not in C_BAD_LG

    def c_ti(t):
        t = str(t).replace(",", "").replace("−", "-").strip()
        try: return int(float(t))
        except Exception: return 0
    def c_tf(t):
        t = str(t).replace(",", "").strip()
        try: return float(t)
        except Exception: return 0.0

    def c_tok(t):
        t = (t or "").strip().lower()
        m = re.match(r"^[a-z0-9%+/\-]+", t)
        return m.group(0) if m else t
    def c_ctext(c):
        return c.get_text(" ", strip=True)
    def c_season(m):
        a, b = m.group(1), m.group(2)
        return a + "-" + b[-2:]
    SEASON_RE = re.compile(r"^(\d{4})\s*[-/\u2013]\s*(\d{2,4})")
    def c_parse(html):
        soup2 = BeautifulSoup(html, "html.parser")
        out = {"skater": [], "goalie": []}
        for table in soup2.find_all("table"):
            trs = table.find_all("tr")
            hr = None
            for tr in trs:
                toks = [c_tok(c_ctext(c)) for c in tr.find_all(["th", "td"])]
                if "gp" in toks and ("team" in toks or "season" in toks or "league" in toks):
                    hr = tr; break
            if hr is None: continue
            heads = [c_tok(c_ctext(c)) for c in hr.find_all(["th", "td"])]
            def first(*names):
                for n in names:
                    if n in heads: return heads.index(n)
                return None
            iS, iT, iL, iGP = first("season", "year", "s"), first("team", "club"), first("league", "lg"), first("gp")
            goalie = "gaa" in heads or "sv%" in heads
            idx = {k: first(k) for k in (("gaa", "sv%", "ga", "sv", "w-l-t", "toi") if goalie else ("g", "a", "tp", "pts"))}
            if iT is None or iL is None or iGP is None: continue
            prev = ""
            for tr in trs:
                if tr is hr: continue
                cells = [c_ctext(c) for c in tr.find_all(["td", "th"])]
                if len(cells) < 4: continue
                if len(cells) == len(heads) - 1 and iS is not None: cells.insert(iS, "")
                if len(cells) != len(heads): continue
                season = cells[iS].strip() if iS is not None else ""
                m = SEASON_RE.match(season)
                if m: prev = season = c_season(m)
                else: season = prev if not season else ""
                if not season: continue
                team, lg = cells[iT].strip(), cells[iL].strip()
                gp = c_ti(cells[iGP])
                if not gp or not c_keep(lg) or C_TEAM_BAD.search(team) or re.match(r"^[A-Za-z ]+ U-?\d\d( \(all\))?$", team) and re.match(r"^(Russia|Finland|Sweden|Canada|USA|Czech|Slovakia|Switzerland|Germany|Latvia|Norway|Denmark|Austria|Belarus|Kazakhstan)", team): continue
                if goalie:
                    w = l = t = 0
                    m2 = re.match(r"^(\d+)-(\d+)-(\d+)", cells[idx["w-l-t"]].replace(" ", "")) if idx.get("w-l-t") is not None else None
                    if m2: w, l, t = (int(x) for x in m2.groups())
                    ga = c_ti(cells[idx["ga"]]) if idx.get("ga") is not None else 0
                    sv = c_ti(cells[idx["sv"]]) if idx.get("sv") is not None else 0
                    mins = c_ti(cells[idx["toi"]]) if idx.get("toi") is not None else 0
                    svp = cells[idx["sv%"]].strip() if idx.get("sv%") is not None else ""
                    svp = svp[1:] if svp.startswith("0.") else ("" if svp in ("-", "\u2013") else svp)
                    if svp == "" and sv + ga > 0: svp = ("%.3f" % (sv / (sv + ga))).lstrip("0")
                    gaa = c_tf(cells[idx["gaa"]]) if idx.get("gaa") is not None else 0.0
                    if not gaa and ga and mins: gaa = round(ga * 60.0 / mins, 2)
                    out["goalie"].append([season, team, lg, gp, w, l, t, gaa, svp, ga, sv + ga if (sv or ga) else 0, mins])
                else:
                    ip = idx.get("tp") if idx.get("tp") is not None else idx.get("pts")
                    if idx.get("g") is None or idx.get("a") is None or ip is None: continue
                    out["skater"].append([season, team, lg, gp, c_ti(cells[idx["g"]]), c_ti(cells[idx["a"]]), c_ti(cells[ip])])
            if out["goalie"] or out["skater"]: break
        return out
    def c_diag(html):
        try:
            s2 = BeautifulSoup(html, "html.parser")
            tabs = []
            for t in s2.find_all("table")[:5]:
                trs = t.find_all("tr")
                tabs.append({"rows": len(trs), "first": [[c.get_text(" ", strip=True)[:22] for c in tr.find_all(["th", "td"])][:14] for tr in trs[:3]]})
            return {"len": len(html), "title": (s2.title.get_text(strip=True) if s2.title else "")[:90], "tables": tabs, "text": s2.get_text(" ", strip=True)[:240]}
        except Exception as ex:
            return {"diag_error": str(ex)[:100]}

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
    c_dbg = []
    for c_name, c_isg in [(n, False) for n in data["skaters"]] + [(n, True) for n in data["goalies"]]:
        if _t.time() - c_start > C_BUDGET_SECONDS: print("career: time budget used, the rest waits for the next run"); break
        e = c_old.get(c_name)
        if e and e.get("at") and e.get("rows") is not None and e.get("v") == C_VER and (NOW.replace(tzinfo=None) - datetime.datetime.fromisoformat(e["at"])).total_seconds() < C_STALE_HOURS * 3600: continue
        path = c_links.get(norm(c_name))
        if not path: print("career: no EP link for", c_name); continue
        c_tried += 1
        rows = None; last_html = None
        for suffix in ("", "/print"):
            try:
                last_html = fetch(C_BASE + path + suffix)
                p = c_parse(last_html)
                rows = p["goalie" if c_isg else "skater"]
                if rows: break
            except Exception as ex:
                print("career: fetch failed:", c_name, suffix, str(ex)[:80])
                _t.sleep(8)
            _t.sleep(3)
        if not rows:
            print("career: no rows for", c_name)
            if last_html and len(c_dbg) < 3: c_dbg.append(dict(c_diag(last_html), name=c_name))
            continue
        rows = [r for r in rows if r[0] != C_CUR]
        c_res[c_name] = {"at": NOW.strftime("%Y-%m-%dT%H:%M:%S"), "rows": rows, "v": C_VER}; c_done += 1
        if c_done == 1: print("career: sample", c_name, rows[:2])
        _t.sleep(4)
    print("career: updated", c_done, "of", c_tried, "tried;", len(c_res), "players stored")
    if c_dbg: c_res["_debug"] = c_dbg
    else: c_res.pop("_debug", None)
    if c_res != c_old:
        json.dump(c_res, open("career.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
except Exception as _e:
    print("career skipped:", str(_e)[:200])

run_next()
