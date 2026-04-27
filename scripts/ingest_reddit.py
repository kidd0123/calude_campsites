"""Pull Reddit posts/comments mentioning campsites — NO AUTH required.

Uses Reddit's public JSON endpoints (e.g. /r/<sub>/top.json) with a polite
User-Agent. Limited to ~60 req/min by Reddit so we sleep between calls.

Run: python scripts/ingest_reddit.py
"""
import json
import re
import sys
import time
import requests
from rapidfuzz import fuzz

from db import connect, init_schema, load_config

UA = {"User-Agent": "camp-finder/0.1 (research; contact: nc.vamsi@gmail.com)"}
SLEEP = 1.2  # seconds between requests, stay under 60/min


def normalize(name):
    n = re.sub(r"\b(campground|campsites?|state park|national (forest|park|monument|recreation area)|recreation area)\b",
               "", name, flags=re.I)
    return re.sub(r"\s+", " ", n).strip().lower()


def detect_themes(text, theme_map):
    text_l = text.lower()
    return ",".join(t for t, kws in theme_map.items() if any(k in text_l for k in kws))


_GENERIC = {"college","spring","princess","lewis","berger","logger","headquarters",
            "skyline","lookout","paradise","horse camp","lone pine","obsidian",
            "council","fremont","grizzly","sunset cove","sand bar flat","west point",
            "denny","baker","pleasant","pines","mill creek","mad river","topsy",
            "stewart","acorn","oak knoll"}

def load_aliases():
    """Load aliases.yaml: returns list of (alias, facility_contains)."""
    import os, yaml
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aliases.yaml")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return [(a["alias"].lower(), a["facility_contains"].lower()) for a in data.get("aliases", [])]


def match_facility(text, facility_index, aliases=None):
    """Match by:
    1. Aliases (e.g. 'big basin' → any facility whose name contains 'Basin')
    2. Exact normalized name match (multi-word, ≥10 chars)
    """
    text_l = text.lower()
    out, seen = [], set()
    if aliases:
        for alias, frag in aliases:
            if alias in text_l:
                for fid, norm, full in facility_index:
                    if fid in seen:
                        continue
                    if frag in full.lower():
                        out.append((fid, full, 100))
                        seen.add(fid)
    for fid, norm, full in facility_index:
        if fid in seen or len(norm) < 10 or " " not in norm or norm in _GENERIC:
            continue
        if norm in text_l:
            out.append((fid, full, 100))
            seen.add(fid)
        elif fuzz.partial_ratio(norm, text_l) >= 95:
            out.append((fid, full, 95))
            seen.add(fid)
    return out[:5]


def reddit_get(url):
    for attempt in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=20, params={"raw_json": 1})
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 2:
                print(f"  fail {url}: {e}")
                return None
            time.sleep(2)
    return None


def fetch_top_posts(sub, limit):
    posts = []
    after = None
    while len(posts) < limit:
        url = f"https://www.reddit.com/r/{sub}/top.json?t=year&limit=100"
        if after:
            url += f"&after={after}"
        data = reddit_get(url)
        time.sleep(SLEEP)
        if not data:
            break
        children = data.get("data", {}).get("children", [])
        if not children:
            break
        for c in children:
            posts.append(c["data"])
        after = data["data"].get("after")
        if not after:
            break
    return posts[:limit]


def fetch_comments(sub, post_id):
    url = f"https://www.reddit.com/r/{sub}/comments/{post_id}.json?limit=200&depth=2"
    data = reddit_get(url)
    time.sleep(SLEEP)
    if not data or len(data) < 2:
        return []
    out = []
    def walk(node):
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        if kind == "Listing":
            for c in node.get("data", {}).get("children", []):
                walk(c)
        elif kind == "t1":
            d = node.get("data", {})
            out.append(d)
            replies = d.get("replies")
            if isinstance(replies, dict):
                walk(replies)
    walk(data[1])
    return out


def main():
    cfg = load_config()
    conn = connect()
    init_schema(conn)
    facs = conn.execute("SELECT id, name FROM facilities").fetchall()
    if not facs:
        sys.exit("Run ingest_ridb.py first")
    index = [(r["id"], normalize(r["name"]), r["name"]) for r in facs]
    aliases = load_aliases()
    print(f"Loaded {len(aliases)} curated aliases")
    theme_map = cfg["themes"]
    subs = cfg["reddit"]["subreddits"]
    posts_per = min(cfg["reddit"]["posts_per_sub"], 200)  # JSON cap
    comments_per = cfg["reddit"]["comments_per_post"]

    total = 0
    for sub in subs:
        print(f"r/{sub}…", flush=True)
        posts = fetch_top_posts(sub, posts_per)
        print(f"  {len(posts)} posts")
        for p in posts:
            blob = (p.get("title") or "") + "\n" + (p.get("selftext") or "")
            for fid, full, score in match_facility(blob, index, aliases):
                themes = detect_themes(blob, theme_map)
                conn.execute(
                    """INSERT OR IGNORE INTO reddit_mentions
                       (facility_id, subreddit, post_id, comment_id, score, permalink, snippet, themes, created_utc)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (fid, sub, p["id"], p["id"], p.get("score", 0),
                     f"https://reddit.com{p.get('permalink','')}",
                     blob[:300], themes, int(p.get("created_utc", 0))),
                )
                total += 1
            # Only fetch comments if the post itself had a hit, to save bandwidth
            mentioned = any(match_facility(blob, index, aliases))
            if not mentioned:
                continue
            for c in fetch_comments(sub, p["id"])[:comments_per]:
                body = c.get("body") or ""
                if len(body) < 30:
                    continue
                for fid, full, score in match_facility(body, index, aliases):
                    themes = detect_themes(body, theme_map)
                    conn.execute(
                        """INSERT OR IGNORE INTO reddit_mentions
                           (facility_id, subreddit, post_id, comment_id, score, permalink, snippet, themes, created_utc)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (fid, sub, p["id"], c.get("id"), c.get("score", 0),
                         f"https://reddit.com{c.get('permalink','')}",
                         body[:300], themes, int(c.get("created_utc", 0))),
                    )
                    total += 1
        conn.commit()

    # Also do targeted searches for "campground in northern california" etc.
    for term in cfg["reddit"]["search_terms"]:
        url = f"https://www.reddit.com/search.json?q={term}+california&sort=top&t=year&limit=100"
        data = reddit_get(url)
        time.sleep(SLEEP)
        if not data:
            continue
        for child in data.get("data", {}).get("children", []):
            p = child["data"]
            blob = (p.get("title") or "") + "\n" + (p.get("selftext") or "")
            for fid, full, score in match_facility(blob, index, aliases):
                themes = detect_themes(blob, theme_map)
                conn.execute(
                    """INSERT OR IGNORE INTO reddit_mentions
                       (facility_id, subreddit, post_id, comment_id, score, permalink, snippet, themes, created_utc)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (fid, p.get("subreddit", "search"), p["id"], p["id"], p.get("score", 0),
                     f"https://reddit.com{p.get('permalink','')}",
                     blob[:300], themes, int(p.get("created_utc", 0))),
                )
                total += 1
        conn.commit()

    print(f"Inserted ~{total} mention rows")
    conn.close()


if __name__ == "__main__":
    main()
