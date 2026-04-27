# California Car-Camping Finder Skill (v2)

## Context

You live in **Oakland** and want a repeatable way to find car-accessible campsites within ~3–5 hours' drive that are (a) **bookable for a 2-night window**, and (b) **actually great** — beautiful redwoods, unique spots, the kind of thing surfaced on Reddit and lesser-known lists, not just whatever Recreation.gov happens to show first. The existing federal/state booking systems give you availability but no quality signal; community sources (Reddit, curated blog lists) give you quality but no availability. This skill's job is to **fuse the two**.

## Why a local DB

Two sources have very different cadences:
- **Quality / catalog data** (RIDB facility list, Reddit mentions, curated lists) changes slowly → scrape/build once, refresh monthly. Expensive to recompute on every query.
- **Availability** changes every minute → fetch live via camply at query time.

So: **catalog + quality scores live in SQLite; availability is always live**. Without the cache, every `/camp-finder` call would re-scrape Reddit and re-download RIDB — minutes of work per query, and rate-limit risk.

If you'd rather skip persistence entirely we can use a flat JSON file instead — same idea, lighter. But for the Reddit ingestion (10k+ comments, dedupe, score aggregation) SQLite earns its keep.

## OSS Building Blocks

| Need | OSS | Role |
|---|---|---|
| Live availability | **[camply](https://github.com/juftin/camply)** | Python API — covers Recreation.gov + ReserveCalifornia + Yellowstone + GoingToCamp. Used at query time. |
| Federal facility catalog | **[RIDB bulk CSV](https://ridb.recreation.gov/download)** + **[ships/ridb](https://github.com/ships/ridb)** generated client | Bulk CSV for the catalog; ridb client for any live facility-detail lookups (campsite-level metadata, photos, descriptions camply doesn't expose). |
| Reddit quality signal | **[PRAW](https://praw.readthedocs.io/)** (read-only API, no OAuth needed for public reads) | Pull top posts/comments from r/CaliCamping, r/CampingandHiking, r/SanFrancisco, r/AskSF, r/bayarea, r/CampingGear, r/dispersedcamping. |
| Entity extraction (matching Reddit text → facility IDs) | **[rapidfuzz](https://github.com/rapidfuzz/RapidFuzz)** + small custom NER on facility name list | Fuzzy-match "Big Basin", "Pinnacles east side", etc. to RIDB rows. |
| Distance | `geopy` Haversine; **OSRM public** for drive-time on top-N only | Cheap ranking pass + accurate top results. |
| Curated lists | Manual one-time scrape of e.g. *Outside*, *SFGate*, *Hipcamp* public "Best Northern California campgrounds" articles via WebFetch | Adds editorial signal Reddit misses. |

## Architecture

```
~/.claude/skills/camp-finder/
├── SKILL.md
├── scripts/
│   ├── ingest_ridb.py        # one-time/monthly: RIDB CSV → facilities table
│   ├── ingest_reddit.py      # monthly: PRAW → reddit_mentions table
│   ├── ingest_curated.py     # WebFetch curated lists → curated_mentions table
│   ├── score.py              # combine signals → quality_score per facility
│   └── find.py               # MAIN: dates → camply availability → ranked output
├── data/
│   └── camps.sqlite
└── config.yaml               # home=Oakland (37.8044,-122.2712), max_hours=5, subreddits, defaults
```

## Data Model

```sql
CREATE TABLE facilities (
  id TEXT PRIMARY KEY,                 -- RIDB FacilityID, or 'rcal:<park_id>', or 'community:<slug>'
  provider TEXT,                       -- 'recreation_gov' | 'reserve_california' | 'community'
  name TEXT, aliases TEXT,             -- JSON array of fuzzy-match aliases
  lat REAL, lon REAL, state TEXT,
  facility_type TEXT,
  car_accessible INTEGER,
  amenities_json TEXT,
  url TEXT, description TEXT,
  miles_from_home REAL,
  drive_hours_est REAL
);
CREATE TABLE reddit_mentions (
  id INTEGER PRIMARY KEY,
  facility_id TEXT, subreddit TEXT, post_id TEXT, comment_id TEXT,
  score INTEGER, permalink TEXT, snippet TEXT,
  themes TEXT,                         -- 'redwoods,ocean,quiet,first-come' (extracted)
  created_utc INTEGER
);
CREATE TABLE curated_mentions (
  facility_id TEXT, source TEXT, url TEXT, rank INTEGER, snippet TEXT
);
CREATE TABLE quality_score (
  facility_id TEXT PRIMARY KEY,
  reddit_mention_count INTEGER,
  reddit_score_sum INTEGER,
  curated_count INTEGER,
  uniqueness REAL,                     -- inverse of mention_count_in_top_50 — boost lesser-known
  themes TEXT,                         -- aggregated
  composite REAL                       -- final ranking score
);
```

## Quality Scoring (the "great campsite" part)

For each facility:
- `reddit_score = log(1 + sum(comment.score for mentions))` — Reddit upvotes as a noisy proxy for "people loved this"
- `curated_score = count of editorial list appearances`
- `uniqueness_bonus`: if a facility shows up in Reddit but **not** in the top-50 most-mentioned (i.e. not Yosemite/Big Sur/Big Basin which everyone knows), boost it. This directly addresses "find new unique places."
- `theme_match`: extract keywords (`redwoods`, `coast`, `alpine`, `hot springs`, `dispersed`, `quiet`, `first-come-first-served`) from comment text — let the user filter (e.g. `--theme redwoods`).
- `composite = 0.4*reddit + 0.2*curated + 0.3*uniqueness + 0.1*theme_match` (tunable in `config.yaml`).

This is the answer to "does camply cover which campsites are great?" — **no, it doesn't**, so we layer Reddit + curated lists on top.

## Skill Behavior (`/camp-finder`)

```
/camp-finder [--start YYYY-MM-DD] [--nights 2] [--max-hours 5] [--theme redwoods] [--limit 15] [--hidden-gems]
```

Defaults: `start=next Friday`, `nights=2`, `max_hours=5`, `home=Oakland`.

Flow:
1. Pull candidates: `SELECT * FROM facilities JOIN quality_score WHERE car_accessible=1 AND drive_hours_est <= max_hours` (optional theme filter, optional `--hidden-gems` flag → only `uniqueness > threshold`).
2. Sort by `composite` quality, take top 50.
3. For each, call `camply` for the date window → keep only those with availability for all N nights.
4. Re-rank kept results by `0.6*quality + 0.4*proximity` (tunable).
5. Output markdown table: rank, name, drive time, theme tags, sample Reddit quote, booking URL.

## Bootstrap Sequence (one-time)

```bash
python scripts/ingest_ridb.py            # ~5 min, ~1500 CA-area rows
python scripts/ingest_reddit.py          # ~10 min, last 2y of top posts/comments from configured subs
python scripts/ingest_curated.py         # ~3 min, ~10 curated articles
python scripts/score.py                  # <1 min, builds quality_score table
```

Re-run monthly via `cron` or a `/loop` schedule. Reddit ingest is incremental after the first run.

## Critical Files

- `~/.claude/skills/camp-finder/SKILL.md`
- `~/.claude/skills/camp-finder/scripts/ingest_ridb.py`
- `~/.claude/skills/camp-finder/scripts/ingest_reddit.py`
- `~/.claude/skills/camp-finder/scripts/ingest_curated.py`
- `~/.claude/skills/camp-finder/scripts/score.py`
- `~/.claude/skills/camp-finder/scripts/find.py`
- `~/.claude/skills/camp-finder/config.yaml`

## Verification

1. **Bootstrap sanity**: after ingest, `SELECT name FROM facilities ORDER BY composite_score DESC LIMIT 20` — should include obvious greats (Big Basin, Pinnacles, Henry Cowell, Salt Point, Russian Gulch) AND a few you haven't heard of (the hidden-gems test).
2. **Theme test**: `/camp-finder --theme redwoods --max-hours 4` → results dominated by Santa Cruz mts / Mendocino coast.
3. **Hidden-gems test**: `/camp-finder --hidden-gems` → filters out Yosemite/Big Sur, surfaces lesser-known.
4. **Availability fusion**: pick a result, manually verify the booking URL + dates on recreation.gov match.
5. **Edge**: fully-booked Memorial Day weekend should return "no availability" gracefully without crashing.

## Open Risks / Decisions

- **Reddit API**: PRAW read-only is fine for our volume but will need free Reddit API credentials in `~/.claude/skills/camp-finder/.env` (free, 100 req/min). Alternative: scrape via [pushshift.io](https://pushshift.io) archives, no auth.
- **Fuzzy matching false positives**: "Big Basin" vs "Big Basin Redwoods" vs "Basin campground" — rapidfuzz threshold tuning needed. Plan: aggressive threshold + hand-curated `aliases.yaml` for the top 100 facilities.
- **camply rate limits**: top-50 candidates × N date checks may hit limits. Mitigate with `availability_cache` table (TTL 30 min) and parallelism cap.
- **Dispersed camping** (BLM, USFS no-reservation areas) doesn't show up in camply — but it WILL show up in Reddit. We can record these in the `community` provider and mark `availability='walk_up'` so they're returned as "no booking needed, drive there."

## Out of Scope (v1)

- Hipcamp paid private-land listings (no public API)
- iOverlander / Campendium ingestion (can add as additional `community` ingesters later)
- Drive-time via Google (using OSRM public; falls back to miles/50 heuristic)
- Notifications on cancellations (camply supports it; wire up after v1 lands)
