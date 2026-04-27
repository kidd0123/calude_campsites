"""Pull curated 'best campgrounds in NorCal' lists, fuzzy-match to facility IDs.

Pages are fetched with requests + a basic readability strip.
Run: python scripts/ingest_curated.py
"""
import re
import sys
import requests
from rapidfuzz import fuzz

from db import connect, init_schema, load_config
from ingest_reddit import normalize


def fetch_text(url):
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 camp-finder"})
        r.raise_for_status()
    except Exception as e:
        print(f"  fail {url}: {e}")
        return ""
    html = r.text
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def main():
    cfg = load_config()
    conn = connect()
    init_schema(conn)
    facs = conn.execute("SELECT id, name FROM facilities").fetchall()
    if not facs:
        sys.exit("Run ingest_ridb.py first")
    index = [(r["id"], normalize(r["name"]), r["name"]) for r in facs]

    inserted = 0
    for url in cfg["curated_sources"]:
        print(f"Fetching {url}…", flush=True)
        text = fetch_text(url)
        if not text:
            continue
        text_l = text.lower()
        # rank by order of mention
        rank_seen = {}
        for fid, norm, full in index:
            if len(norm) < 4:
                continue
            idx = text_l.find(norm)
            if idx == -1 and fuzz.partial_ratio(norm, text_l) < 92:
                continue
            if idx == -1:
                idx = 999999
            rank_seen.setdefault(fid, (idx, full, text_l[max(0, idx-80):idx+200]))
        ranked = sorted(rank_seen.items(), key=lambda kv: kv[1][0])
        for rank, (fid, (idx, full, snippet)) in enumerate(ranked, 1):
            conn.execute(
                """INSERT OR IGNORE INTO curated_mentions
                   (facility_id, source, url, rank, snippet) VALUES (?,?,?,?,?)""",
                (fid, url.split("/")[2], url, rank, snippet[:300]),
            )
            inserted += 1
        conn.commit()

    print(f"Inserted {inserted} curated mentions")
    conn.close()


if __name__ == "__main__":
    main()
