"""Genera el calendario (.ics), resultados, clasificación, goleadores y ficha del rival de PALANCAS FS.

Fuente: campeonatosmunicipalesmostoles.org (ADASUMA, Móstoles).
Sin anti-bot: los endpoints `ser/*Service.php` devuelven JSON al pasar los parámetros
de jqGrid (`page`, `rows`, `sidx`, `sord`, `_search`, `nd`).

El calendario por equipo (calendarTeamService) NO trae el pabellón; el calendario
completo de la división (calendarService) sí lo trae en el campo 8. Se cruzan por ID.
"""
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

TEAM_ID = 1865573327850
TEAM_NAME = "PALANCAS FS"
DIVISION = "27850,S1,1º+sabados,,1"          # división S1 (1º sábados)
DIV_NAME = "Fútbol Sala Móstoles · S1 (1º Sábados)"
BASE = "http://www.campeonatosmunicipalesmostoles.org"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
DURATION = timedelta(hours=1)
ALARM_BEFORE = "-PT1H"

ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
ICS_FILE = DOCS / "palancas.ics"
MATCHES_FILE = DOCS / "matches.json"
STANDINGS_FILE = DOCS / "standings.json"
SCORERS_FILE = DOCS / "scorers.json"
STATS_FILE = DOCS / "stats.json"


def fetch_json(session: requests.Session, path: str, dato: str, rows: int = 500, sidx: str = "jornada") -> dict:
    """GET a un endpoint jqGrid y devuelve el JSON parseado."""
    params = {
        "dato": dato, "page": 1, "rows": rows,
        "sidx": sidx, "sord": "asc", "_search": "false", "nd": 1,
    }
    r = session.get(f"{BASE}/{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_calendar(session: requests.Session) -> list[dict]:
    """Partidos de PALANCAS FS: [{jornada, fecha, equipo1, goles, golesc, equipo2}, ...].

    La celda jqGrid es: [jornada, fecha, equipo1, escudo1, goles, golesc, escudo2, equipo2].
    """
    data = fetch_json(session, "ser/calendarTeamService.php", str(TEAM_ID))
    out = []
    for row in data.get("rows", []):
        c = row.get("cell", [])
        if len(c) < 8:
            continue
        out.append({
            "id": row.get("id", ""),
            "jornada": c[0],
            "fecha": c[1],           # "26-09-2026 21:00"
            "equipo1": c[2],
            "goles": c[4],
            "golesc": c[5],
            "equipo2": c[7],
        })
    return out


def fetch_division_calendar(session: requests.Session) -> list[dict]:
    """Todos los partidos de la división, CON pabellón.

    La celda es: [jornada, fecha, equipo1, escudo1, goles, golesc, escudo2, equipo2, PABELLON, grupo].
    """
    data = fetch_json(session, "ser/calendarService.php", DIVISION, rows=500)
    out = []
    for row in data.get("rows", []):
        c = row.get("cell", [])
        if len(c) < 9:
            continue
        out.append({
            "id": row.get("id", ""),
            "jornada": c[0],
            "start": parse_fecha(c[1]),
            "equipo1": c[2],
            "goles": c[4],
            "golesc": c[5],
            "equipo2": c[7],
            "pabellon": (c[8] or "").strip(),
        })
    return out


def fetch_standings(session: requests.Session) -> list[dict]:
    """Clasificación de la división S1.

    Celda: [POS, escudo, equipo, PTS, PJ, PG, PE, PP, GF, GC, DG, PS, INC, idequipo, grupo_idcomp].
    """
    data = fetch_json(session, "ser/clasifOptService.php", DIVISION, rows=50, sidx="POS")
    out = []
    for row in data.get("rows", []):
        c = row.get("cell", [])
        if len(c) < 13:
            continue
        def n(i):
            try:
                return int(c[i])
            except (ValueError, TypeError):
                return 0
        out.append({
            "pos": n(0), "equipo": c[2], "pts": n(3), "pj": n(4),
            "pg": n(5), "pe": n(6), "pp": n(7), "gf": n(8), "gc": n(9),
            "dg": n(10), "idequipo": c[13] if len(c) > 13 else "",
        })
    return out


def fetch_scorers(session: requests.Session) -> list[dict]:
    """Goleadores de PALANCAS FS.

    Celda: [pos, nombre, goles, partidos, media, ?].
    """
    data = fetch_json(session, "ser/goleadorTeamService.php", str(TEAM_ID), rows=100)
    out = []
    for row in data.get("rows", []):
        c = row.get("cell", [])
        if len(c) < 4:
            continue
        def n(i):
            try:
                return int(c[i])
            except (ValueError, TypeError):
                return 0
        out.append({"pos": n(0), "nombre": c[1].strip(), "goles": n(2), "pj": n(3)})
    return out


def fetch_team_scorers(session: requests.Session, team_id) -> list[dict]:
    """Goleadores de un equipo cualquiera (misma estructura que fetch_scorers)."""
    try:
        data = fetch_json(session, "ser/goleadorTeamService.php", str(team_id), rows=100)
    except Exception:
        return []
    out = []
    for row in data.get("rows", []):
        c = row.get("cell", [])
        if len(c) < 3:
            continue
        try:
            out.append({"pos": int(c[0]), "nombre": c[1].strip(), "goles": int(c[2]),
                        "pj": int(c[3]) if len(c) > 3 and str(c[3]).isdigit() else None})
        except (ValueError, TypeError):
            continue
    return out


def fetch_all_team_scorers(standings: list[dict], previous: list | None = None) -> list[dict]:
    """Goleadores de TODOS los equipos de la división, en paralelo (4 hilos).

    El navegador no puede llamarlas en vivo (la web es HTTPS y ADASUMA HTTP), así que
    se precalculan aquí. Si la petición de un equipo falla o vuelve vacía, se conserva
    la lista anterior de `stats.json` en vez de dejar al equipo sin goleadores.
    """
    prev = {}
    for t in (previous or []):
        if isinstance(t, dict) and t.get("idequipo"):
            prev[str(t["idequipo"])] = t.get("scorers") or []

    def one(st: dict):
        tid = str(st.get("idequipo") or "")
        if not tid:
            return st.get("equipo", ""), tid, []
        ses = requests.Session()
        ses.headers["User-Agent"] = UA
        try:
            rows = fetch_team_scorers(ses, tid)
        except Exception:
            rows = []
        finally:
            ses.close()
        return st.get("equipo", ""), tid, rows

    targets = [st for st in standings if st.get("idequipo")]
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(one, targets))

    by_id = {st["idequipo"]: st for st in targets}
    out = []
    for equipo, tid, rows in results:
        if not rows:
            rows = prev.get(str(tid), [])
        st = by_id.get(tid, {})
        out.append({"idequipo": tid, "equipo": equipo or st.get("equipo", ""),
                    "pos": st.get("pos"), "scorers": rows})
    out.sort(key=lambda t: t.get("pos") or 99)
    return out


def parse_fecha(text: str) -> datetime | None:
    """'26-09-2026 21:00' -> datetime (hora local de Madrid)."""
    m = re.match(r"(\d{1,2})-(\d{1,2})-(\d{4})\s+(\d{1,2}):(\d{2})", (text or "").strip())
    if not m:
        return None
    d, mo, y, h, mi = m.groups()
    return datetime(int(y), int(mo), int(d), int(h), int(mi))


def _has_score(m: dict) -> bool:
    """True si el partido ya tiene marcador real (no un 0-0 de partido aún no jugado)."""
    start = m.get("start")
    if not start or start > datetime.now():
        return False
    g, gc = m.get("goles"), m.get("golesc")
    return str(g) not in ("", "-", "None") and str(gc) not in ("", "-", "None")


def _result_for(team: str, m: dict) -> str | None:
    """V/E/D del equipo `team` en el partido m (o None si aún no jugado)."""
    if not _has_score(m):
        return None
    g, gc = int(m["goles"]), int(m["golesc"])
    own, riv = (g, gc) if m["equipo1"] == team else (gc, g)
    return "V" if own > riv else ("D" if own < riv else "E")


def build_next_rival(palancas: list[dict], division: list[dict], standings: list[dict],
                     session: requests.Session, scorers_map: dict | None = None) -> dict | None:
    """Ficha completa del próximo rival: pabellón, clasificación, forma, goleadores, H2H."""
    now = datetime.now()
    future = sorted([m for m in palancas if m["start"] and m["start"] > now], key=lambda m: m["start"])
    if not future:
        return None
    nm = future[0]
    rival = nm["equipo2"] if nm["equipo1"] == TEAM_NAME else nm["equipo1"]
    home = nm["equipo1"] == TEAM_NAME

    st = next((s for s in standings if s["equipo"] == rival), None)
    rival_id = st.get("idequipo") if st else None
    top_scorers = []
    if scorers_map is not None and rival_id:
        top_scorers = (scorers_map.get(str(rival_id)) or {}).get("scorers") or []
    if not top_scorers and rival_id:
        top_scorers = fetch_team_scorers(session, rival_id)

    # últimos resultados del rival (5 más recientes jugados)
    riv_matches = [m for m in division if rival in (m["equipo1"], m["equipo2"]) and _has_score(m)]
    riv_matches.sort(key=lambda m: m["start"], reverse=True)
    last_results = [{
        "date": m["start"].isoformat(), "home": m["equipo1"], "away": m["equipo2"],
        "score": [int(m["goles"]), int(m["golesc"])], "result": _result_for(rival, m),
    } for m in riv_matches[:5]]

    # cara a cara Palancas vs rival
    h2h = []
    for m in division:
        if TEAM_NAME in (m["equipo1"], m["equipo2"]) and rival in (m["equipo1"], m["equipo2"]) and TEAM_NAME != rival and _has_score(m):
            h2h.append({
                "date": m["start"].isoformat(), "home": m["equipo1"], "away": m["equipo2"],
                "score": [int(m["goles"]), int(m["golesc"])], "result": _result_for(TEAM_NAME, m),
            })
    h2h.sort(key=lambda x: x["date"])

    return {
        "name": rival, "match_id": nm["id"], "competition": DIV_NAME,
        "date": nm["start"].isoformat(), "pabellon": nm.get("pabellon", ""),
        "home": home, "jornada": nm["jornada"], "rival_id": rival_id,
        "standing": ({"pos": st["pos"], "pts": st["pts"], "pj": st["pj"], "pg": st["pg"],
                      "pe": st["pe"], "pp": st["pp"], "gf": st["gf"], "gc": st["gc"],
                      "dg": st["dg"]} if st else None),
        "top_scorers": top_scorers,
        "last_results": last_results,
        "head_to_head": h2h,
    }


def esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", r"\;").replace(",", r"\,").replace("\n", r"\n")


def fold(line: str) -> str:
    """Pliega líneas a 75 octetos (RFC 5545)."""
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode()) > 74:
            out.append(cur)
            cur = " " + ch
        else:
            cur += ch
    out.append(cur)
    return "\r\n".join(out)


VTIMEZONE = """BEGIN:VTIMEZONE
TZID:Europe/Madrid
BEGIN:DAYLIGHT
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
TZNAME:CEST
DTSTART:19700329T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
TZNAME:CET
DTSTART:19701025T030000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE""".splitlines()


def build_ics(matches: list[dict]) -> str:
    fmt = "%Y%m%dT%H%M%S"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//palancasfs-calendario//ES",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:Palancas FS",
             "X-WR-TIMEZONE:Europe/Madrid", "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
             "X-PUBLISHED-TTL:PT1H", *VTIMEZONE]
    for m in matches:
        start = m["start"]
        if not start:
            continue
        goles, golesc = m.get("goles"), m.get("golesc")
        if _has_score(m):
            summary = f"⚽ {m['equipo1']} {goles}-{golesc} {m['equipo2']}"
        else:
            summary = f"⚽ {m['equipo1']} vs {m['equipo2']}"
        desc = f"Fútbol Sala Móstoles · Jornada {m['jornada']}"
        stamp = datetime(2026, 1, 1) + timedelta(
            seconds=int(hashlib.sha1((summary + desc + start.isoformat()).encode()).hexdigest()[:6], 16))
        lines += [
            "BEGIN:VEVENT",
            f"UID:palancas-match-{m['id']}@palancasfs",
            f"DTSTAMP:{stamp.strftime(fmt)}Z",
            f"DTSTART;TZID=Europe/Madrid:{start.strftime(fmt)}",
            f"DTEND;TZID=Europe/Madrid:{(start + DURATION).strftime(fmt)}",
            f"SUMMARY:{esc(summary)}",
        ]
        if m.get("pabellon"):
            lines.append(f"LOCATION:{esc(m['pabellon'])}")
        lines += [
            f"DESCRIPTION:{esc(desc)}",
            "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{esc(summary)}",
            f"TRIGGER:{ALARM_BEFORE}", "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


def build_matches_json(matches: list[dict]) -> list[dict]:
    """Normaliza partidos para la web (jugados/próximos) + pabellón."""
    out = []
    for m in matches:
        start = m.get("start")
        if not start:
            continue
        jugado = _has_score(m)
        g, gc = m.get("goles"), m.get("golesc")
        out.append({
            "id": m["id"], "jornada": m["jornada"],
            "fecha": start.isoformat(),
            "equipo1": m["equipo1"], "equipo2": m["equipo2"],
            "goles": int(g) if jugado else None,
            "golesc": int(gc) if jugado else None,
            "pabellon": m.get("pabellon", ""),
        })
    return out


def main():
    session = requests.Session()
    session.headers["User-Agent"] = UA

    matches = fetch_calendar(session)
    for m in matches:
        m["start"] = parse_fecha(m["fecha"])
    matches = [m for m in matches if m["start"]]

    if not matches:
        # Fail-safe: no sobrescribir si no se extraen partidos.
        print("AVISO: 0 partidos. Se mantiene la web anterior.")
        return

    division = fetch_division_calendar(session)
    pab_by_id = {m["id"]: m["pabellon"] for m in division}
    for m in matches:
        m["pabellon"] = pab_by_id.get(m["id"], "")

    standings = fetch_standings(session)
    scorers = fetch_scorers(session)

    previous_teams = None
    try:
        previous_teams = json.loads(STATS_FILE.read_text(encoding="utf-8")).get("teams")
    except Exception:
        previous_teams = None

    teams = fetch_all_team_scorers(standings, previous_teams)
    scorers_map = {t["idequipo"]: t for t in teams}
    next_rival = build_next_rival(matches, division, standings, session, scorers_map=scorers_map)

    liga_matches = [{
        "id": m["id"], "jornada": m["jornada"], "fecha": m["start"].isoformat(),
        "equipo1": m["equipo1"], "equipo2": m["equipo2"],
        "goles": int(m["goles"]) if _has_score(m) else None,
        "golesc": int(m["golesc"]) if _has_score(m) else None,
        "pabellon": m["pabellon"],
    } for m in division if m["start"]]

    stats = {
        "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "next_rival": next_rival,
        "players": [{"pos": s["pos"], "nombre": s["nombre"], "goles": s["goles"], "pj": s["pj"]} for s in scorers],
        "liga": {"division": DIV_NAME, "standings": standings, "matches": liga_matches},
        "teams": teams,
    }

    DOCS.mkdir(exist_ok=True)
    ICS_FILE.write_bytes(build_ics(matches).encode("utf-8"))
    MATCHES_FILE.write_text(json.dumps(build_matches_json(matches), ensure_ascii=False, indent=1), encoding="utf-8")
    STANDINGS_FILE.write_text(json.dumps(standings, ensure_ascii=False, indent=1), encoding="utf-8")
    SCORERS_FILE.write_text(json.dumps(scorers, ensure_ascii=False, indent=1), encoding="utf-8")
    STATS_FILE.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")

    for m in sorted(matches, key=lambda x: x["start"]):
        print(m["start"].strftime("%a %d/%m %H:%M"), "| J" + m["jornada"], "|",
              m["equipo1"], m.get("goles") or "-", "-", m.get("golesc") or "-", m["equipo2"],
              "|", m.get("pabellon") or "")
    print(f"{len(matches)} partidos -> {ICS_FILE.name}")
    print(f"Clasificación: {len(standings)} equipos · Goleadores: {len(scorers)} · "
          f"Liga: {len(liga_matches)} partidos · Próximo rival: {(next_rival or {}).get('name', '-')}")
    print(f"Goleadores por equipo: {sum(1 for t in teams if t['scorers'])}/{len(teams)} con datos")


if __name__ == "__main__":
    main()
