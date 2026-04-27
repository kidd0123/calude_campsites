"""Plan multiple weekends at once. Designed to be called from a scheduled agent.

Input: JSON list of windows on stdin OR --weekends auto (next 8 from today).
Output: JSON to stdout with ranked available campsites per window.

Usage:
    python scripts/weekend_planner.py --weekends auto --max-hours 5 --nights 2
    echo '[{"start":"2026-05-24","nights":2}]' | python scripts/weekend_planner.py --stdin
"""
import argparse
import datetime as dt
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from db import connect, init_schema, load_config
from find import check_availability


def upcoming_weekends(n=8, days_out=60):
    """Return up to n upcoming Fri-Sat-Sun windows starting in the next `days_out` days."""
    today = dt.date.today()
    out = []
    d = today + dt.timedelta(days=(4 - today.weekday()) % 7 or 7)  # next Friday
    while len(out) < n and (d - today).days <= days_out:
        out.append(d)
        d += dt.timedelta(days=7)
    return out


def plan_window(start, nights, max_hours, theme=None, hidden_gems=False, candidates=40, limit=10):
    cfg = load_config()
    conn = connect()
    init_schema(conn)
    sql = """
    SELECT f.id, f.name, f.url, f.miles_from_home, f.drive_hours_est,
           q.composite, q.themes, q.uniqueness, q.reddit_mention_count
    FROM facilities f
    LEFT JOIN quality_score q ON q.facility_id = f.id
    WHERE f.car_accessible = 1 AND f.drive_hours_est <= ?
    """
    params = [max_hours]
    if theme:
        sql += " AND q.themes LIKE ?"
        params.append(f"%{theme}%")
    if hidden_gems:
        sql += " AND q.uniqueness >= 1"
    sql += " ORDER BY COALESCE(q.composite, 0) DESC LIMIT ?"
    params.append(candidates)
    cands = conn.execute(sql, params).fetchall()

    quotes = {}
    for c in cands:
        row = conn.execute(
            "SELECT snippet, permalink FROM reddit_mentions WHERE facility_id=? ORDER BY score DESC LIMIT 1",
            (c["id"],),
        ).fetchone()
        if row:
            quotes[c["id"]] = {"snippet": row["snippet"][:200], "permalink": row["permalink"]}

    available = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(check_availability, c["id"], start, nights): c for c in cands}
        for f in as_completed(futs):
            c = futs[f]
            try:
                avail_dates, _ = f.result()
            except Exception:
                avail_dates = None
            if avail_dates is not None and avail_dates >= nights:
                available.append((c, avail_dates))

    blend = cfg["scoring"]["final_blend"]
    max_q = max((c["composite"] or 0 for c, _ in available), default=1) or 1
    max_h = max((c["drive_hours_est"] or 0 for c, _ in available), default=1) or 1
    def score(c):
        q = (c["composite"] or 0) / max_q
        p = 1 - ((c["drive_hours_est"] or 0) / max_h)
        return blend["quality"] * q + blend["proximity"] * p
    available.sort(key=lambda x: -score(x[0]))

    out = []
    for c, avail in available[:limit]:
        out.append({
            "name": c["name"],
            "url": c["url"],
            "drive_hours": round(c["drive_hours_est"] or 0, 1),
            "miles": round(c["miles_from_home"] or 0, 0),
            "themes": c["themes"] or "",
            "hidden_gem": bool(c["uniqueness"] and c["uniqueness"] >= 1),
            "reddit_mentions": c["reddit_mention_count"] or 0,
            "available_nights": avail,
            "quote": quotes.get(c["id"], {}).get("snippet", ""),
            "quote_link": quotes.get(c["id"], {}).get("permalink", ""),
            "quality_score": round(c["composite"] or 0, 3),
        })
    conn.close()
    return out


def render_html(report):
    parts = ['<html><body style="font-family:-apple-system,sans-serif;max-width:760px">',
             '<h2>🏕️ Camp Finder — Weekend Plan</h2>',
             f'<p style="color:#666">Generated {dt.datetime.now().strftime("%A %b %-d, %Y at %-I:%M %p")} '
             f'• Home: {report["home"]} • Max drive: {report["max_hours"]}h</p>']
    if report.get("conflicts"):
        parts.append(f'<p style="color:#c33"><b>Skipped {len(report["conflicts"])} weekends</b> '
                     f'(calendar conflicts): {", ".join(report["conflicts"])}</p>')
    for w in report["weekends"]:
        date_str = dt.date.fromisoformat(w["start"]).strftime("%a %b %-d")
        parts.append(f'<h3 style="margin-top:24px">{date_str} → +{w["nights"]} nights</h3>')
        if not w["results"]:
            parts.append('<p style="color:#999"><i>No availability found within the criteria.</i></p>')
            continue
        parts.append('<table style="border-collapse:collapse;width:100%">')
        parts.append('<tr style="background:#f3f3f3"><th align="left">#</th><th align="left">Site</th>'
                     '<th align="left">Drive</th><th align="left">Avail</th><th align="left">Themes</th></tr>')
        for i, r in enumerate(w["results"][:8], 1):
            gem = " ★" if r["hidden_gem"] else ""
            parts.append(
                f'<tr style="border-top:1px solid #eee"><td>{i}</td>'
                f'<td><a href="{r["url"]}">{r["name"]}</a>{gem}</td>'
                f'<td>{r["drive_hours"]}h ({r["miles"]:.0f} mi)</td>'
                f'<td>{r["available_nights"]}/{w["nights"]} nights</td>'
                f'<td style="color:#666;font-size:0.9em">{r["themes"] or "—"}</td></tr>')
            if r["quote"]:
                parts.append(
                    f'<tr><td></td><td colspan="4" style="color:#555;font-size:0.85em;font-style:italic;'
                    f'padding-bottom:8px">"{r["quote"]}" '
                    f'<a href="{r["quote_link"]}" style="color:#888">[reddit]</a></td></tr>')
        parts.append('</table>')
    parts.append('<p style="color:#999;font-size:0.8em;margin-top:32px">'
                 'Auto-generated by camp-finder • '
                 '<a href="https://github.com/kidd0123/calude_campsites">repo</a></p>')
    parts.append('</body></html>')
    return "\n".join(parts)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--weekends", default="auto", help="'auto' for next N weekends, or comma-separated YYYY-MM-DD")
    p.add_argument("--n", type=int, default=8)
    p.add_argument("--nights", type=int, default=2)
    p.add_argument("--max-hours", type=float, default=5.0)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--candidates", type=int, default=40)
    p.add_argument("--theme", default=None)
    p.add_argument("--hidden-gems", action="store_true")
    p.add_argument("--stdin", action="store_true")
    p.add_argument("--exclude", default="", help="comma-separated YYYY-MM-DD weekends to skip (calendar conflicts)")
    p.add_argument("--html", action="store_true")
    args = p.parse_args()

    cfg = load_config()
    excluded = {s.strip() for s in args.exclude.split(",") if s.strip()}

    if args.stdin:
        windows = [(dt.date.fromisoformat(w["start"]), w.get("nights", args.nights))
                   for w in json.load(sys.stdin)]
    elif args.weekends == "auto":
        windows = [(d, args.nights) for d in upcoming_weekends(args.n)]
    else:
        windows = [(dt.date.fromisoformat(s.strip()), args.nights)
                   for s in args.weekends.split(",") if s.strip()]

    weekends_out = []
    skipped = []
    for start, nights in windows:
        if start.isoformat() in excluded:
            skipped.append(start.isoformat())
            continue
        results = plan_window(start, nights, args.max_hours, args.theme, args.hidden_gems,
                              args.candidates, args.limit)
        weekends_out.append({
            "start": start.isoformat(),
            "nights": nights,
            "results": results,
        })

    report = {
        "generated_at": dt.datetime.now().isoformat(),
        "home": cfg["home"]["name"],
        "max_hours": args.max_hours,
        "conflicts": skipped,
        "weekends": weekends_out,
    }
    if args.html:
        print(render_html(report))
    else:
        print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
