"""Main query: take date window + filters → ranked, available campsites.

Run: python scripts/find.py [--start YYYY-MM-DD] [--nights 2] [--max-hours 5]
                            [--theme redwoods] [--limit 15] [--hidden-gems]
"""
import argparse
import datetime as dt
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from db import connect, init_schema, load_config


def next_friday():
    today = dt.date.today()
    return today + dt.timedelta(days=(4 - today.weekday()) % 7 or 7)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--start", help="YYYY-MM-DD (default: next Friday)")
    p.add_argument("--nights", type=int, default=None)
    p.add_argument("--max-hours", type=float, default=None)
    p.add_argument("--theme", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--candidates", type=int, default=50, help="Top-N quality candidates to availability-check")
    p.add_argument("--hidden-gems", action="store_true")
    p.add_argument("--no-availability", action="store_true", help="Skip live availability check")
    return p.parse_args()


def check_availability(facility_id, start_date, nights, retries=2):
    """Return (distinct_available_dates, nights) or (None, nights) on error."""
    import time
    if not facility_id.startswith("rg:"):
        return (None, nights)
    rg_id = facility_id.split(":", 1)[1]
    try:
        from camply.containers import SearchWindow
        from camply.search import SearchRecreationDotGov
    except Exception as e:
        print(f"camply import failed: {e}", file=sys.stderr)
        return (None, nights)
    end_date = start_date + dt.timedelta(days=nights)
    last_err = None
    for attempt in range(retries + 1):
        try:
            search = SearchRecreationDotGov(
                search_window=SearchWindow(start_date=start_date, end_date=end_date),
                campgrounds=[int(rg_id)],
                nights=1,
            )
            results = search.get_matching_campsites(log=False, verbose=False, continuous=False)
            by_date = set()
            for r in results or []:
                d = getattr(r, "booking_date", None) or getattr(r, "availability_date", None)
                if d:
                    d_key = d.date() if hasattr(d, "date") else d
                    if start_date <= d_key < end_date:
                        by_date.add(d_key)
            return (len(by_date), nights)
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    return (None, nights)


def main():
    args = parse_args()
    cfg = load_config()
    d = cfg["defaults"]
    start = dt.date.fromisoformat(args.start) if args.start else next_friday()
    nights = args.nights or d["nights"]
    max_hours = args.max_hours if args.max_hours is not None else d["max_hours"]
    limit = args.limit or d["limit"]

    conn = connect()
    init_schema(conn)

    sql = """
    SELECT f.id, f.name, f.url, f.miles_from_home, f.drive_hours_est, f.lat, f.lon,
           q.composite, q.themes, q.uniqueness, q.reddit_mention_count
    FROM facilities f
    LEFT JOIN quality_score q ON q.facility_id = f.id
    WHERE f.car_accessible = 1
      AND f.drive_hours_est <= ?
    """
    params = [max_hours]
    if args.theme:
        sql += " AND q.themes LIKE ?"
        params.append(f"%{args.theme}%")
    if args.hidden_gems:
        sql += " AND q.uniqueness >= 1"
    sql += " ORDER BY COALESCE(q.composite, 0) DESC LIMIT ?"
    params.append(args.candidates)

    candidates = conn.execute(sql, params).fetchall()
    if not candidates:
        print("No candidates. Run the ingest scripts first?")
        return

    # Get a sample reddit quote per facility
    quotes = {}
    for c in candidates:
        row = conn.execute(
            "SELECT snippet, permalink FROM reddit_mentions WHERE facility_id=? ORDER BY score DESC LIMIT 1",
            (c["id"],),
        ).fetchone()
        if row:
            quotes[c["id"]] = (row["snippet"][:140], row["permalink"])

    print(f"\nQuery: {start} for {nights} night(s), within {max_hours}h drive, "
          f"{'theme=' + args.theme + ', ' if args.theme else ''}"
          f"{'hidden gems only, ' if args.hidden_gems else ''}"
          f"{len(candidates)} candidates\n")

    if args.no_availability:
        results = [(c, None, None) for c in candidates[:limit]]
    else:
        results = []
        # Lower parallelism (3) to reduce camply rate-limit failures.
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(check_availability, c["id"], start, nights): c for c in candidates}
            for f in as_completed(futs):
                c = futs[f]
                try:
                    avail_dates, _ = f.result()
                except Exception:
                    avail_dates = None
                # Only include sites we know are available; drop unknowns from output.
                if avail_dates is not None and avail_dates >= nights:
                    results.append((c, avail_dates, None))
        results = results[:limit]

    # Re-rank by 0.6*quality + 0.4*proximity
    blend = cfg["scoring"]["final_blend"]
    max_q = max((r[0]["composite"] or 0 for r in results), default=1) or 1
    max_h = max((r[0]["drive_hours_est"] or 0 for r in results), default=1) or 1
    def score(c, avail):
        q = (c["composite"] or 0) / max_q
        p = 1 - ((c["drive_hours_est"] or 0) / max_h)
        bonus = 0.05 if avail else 0
        return blend["quality"] * q + blend["proximity"] * p + bonus
    results.sort(key=lambda x: -score(x[0], x[1]))

    print(f"| # | Site | Drive | Avail | Themes | Hidden | Quote |")
    print(f"|---|------|-------|-------|--------|--------|-------|")
    for i, (c, avail, _) in enumerate(results, 1):
        quote, plink = quotes.get(c["id"], ("", ""))
        avail_str = f"{avail}/{nights} d" if avail is not None else "—"
        gem = "★" if c["uniqueness"] and c["uniqueness"] >= 1 else ""
        themes = c["themes"] or ""
        link = f"[{c['name']}]({c['url']})"
        print(f"| {i} | {link} | {c['drive_hours_est']:.1f}h | {avail_str} | "
              f"{themes} | {gem} | {quote.replace(chr(10),' ')} |")

    conn.close()


if __name__ == "__main__":
    main()
