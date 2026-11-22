"""Genera el calendario (.ics), resultados, clasificación y goleadores de PALANCAS FS.

Fuente: campeonatosmunicipalesmostoles.org (ADASUMA, Móstoles).
Sin anti-bot: los endpoints `ser/*Service.php` devuelven JSON al pasar los parámetros
de jqGrid (`page`, `rows`, `sidx`, `sord`, `_search`, `nd`).
"""
import hashlib
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

TEAM_ID = 1865573327850
TEAM_NAME = "PALANCAS FS"
DIVISION = "27850,S1,1º+sabados,,1"          # división S1 (1º sábados)
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
        out.append({"pos": n(0), "nombre": c[1], "goles": n(2), "pj": n(3)})
    return out


def parse_fecha(text: str) -> datetime | None:
    """'26-09-2026 21:00' -> datetime (hora local de Madrid)."""
    m = re.match(r"(\d{1,2})-(\d{1,2})-(\d{4})\s+(\d{1,2}):(\d{2})", (text or "").strip())
    if not m:
        return None
    d, mo, y, h, mi = m.groups()
    return datetime(int(y), int(mo), int(d), int(h), int(mi))


def esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", r"\;").replace(",", r"\,",).replace("\n", r"\n")


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
        has_score = _has_score(m)
        if has_score:
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
            f"DESCRIPTION:{esc(desc)}",
            "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{esc(summary)}",
            f"TRIGGER:{ALARM_BEFORE}", "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


def _has_score(m: dict) -> bool:
    """True si el partido ya tiene marcador real (no un 0-0 de partido aún no jugado)."""
    start = m.get("start")
    if not start:
        return False
    # Un partido futuro con 0-0 = aún no jugado. Pasado con 0-0 = empate real.
    if start > datetime.now():
        return False
    g, gc = m.get("goles"), m.get("golesc")
    return g not in (None, "", "-") and gc not in (None, "", "-")


def build_matches_json(matches: list[dict]) -> list[dict]:
    """Normaliza partidos para la web (distinguimos jugados/próximos)."""
    out = []
    for m in matches:
        start = m.get("start")
        if not start:
            continue
        jugado = start <= datetime.now()
        g, gc = m.get("goles"), m.get("golesc")
        out.append({
            "id": m["id"], "jornada": m["jornada"],
            "fecha": start.isoformat(),
            "equipo1": m["equipo1"], "equipo2": m["equipo2"],
            "goles": int(g) if jugado and g not in (None, "", "-") else None,
            "golesc": int(gc) if jugado and gc not in (None, "", "-") else None,
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

    standings = fetch_standings(session)
    scorers = fetch_scorers(session)

    DOCS.mkdir(exist_ok=True)
    ICS_FILE.write_bytes(build_ics(matches).encode("utf-8"))
    MATCHES_FILE.write_text(json.dumps(build_matches_json(matches), ensure_ascii=False, indent=1), encoding="utf-8")
    STANDINGS_FILE.write_text(json.dumps(standings, ensure_ascii=False, indent=1), encoding="utf-8")
    SCORERS_FILE.write_text(json.dumps(scorers, ensure_ascii=False, indent=1), encoding="utf-8")

    for m in sorted(matches, key=lambda x: x["start"]):
        print(m["start"].strftime("%a %d/%m %H:%M"), "| J" + m["jornada"], "|",
              m["equipo1"], m.get("goles") or "-", "-", m.get("golesc") or "-", m["equipo2"])
    print(f"{len(matches)} partidos -> {ICS_FILE.name}")
    print(f"Clasificación: {len(standings)} equipos · Goleadores: {len(scorers)}")


if __name__ == "__main__":
    main()
