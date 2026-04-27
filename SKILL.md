---
name: camp-finder
description: Find great car-camping sites in California within driving distance of Oakland, fusing live availability (Recreation.gov, ReserveCalifornia via camply) with quality signals scraped from Reddit and curated lists. Use when the user asks about camping trips, weekend campsite ideas, "where should I camp", checking availability for specific dates, finding hidden-gem campgrounds, or filtering by themes like redwoods, coast, or alpine.
---

# Camp Finder

Repeatable lookup for car-accessible campsites within ~5 hours of Oakland, ranked by a fusion of community quality signal (Reddit + curated lists) and live availability.

## Quick start

```bash
cd ~/.claude/skills/camp-finder
source .venv/bin/activate

# One-time bootstrap (or monthly refresh)
python scripts/ingest_ridb.py        # federal facility catalog
python scripts/ingest_reddit.py      # reddit mentions
python scripts/ingest_curated.py     # curated blog lists
python scripts/score.py              # composite quality_score

# Main query
python scripts/find.py --start 2026-05-08 --nights 2 --max-hours 5
python scripts/find.py --hidden-gems --theme redwoods
```

## When to invoke

User asks any of:
- "Find me a campsite for [dates]"
- "Where can I car camp near the Bay Area?"
- "What are some hidden-gem campgrounds within X hours?"
- "Any redwoods/coast/alpine sites available [dates]?"
- "Refresh the campsite database"

## How it works

1. **Catalog** (`facilities` table): RIDB bulk CSV → all CA-area car-accessible campgrounds, with lat/lon and precomputed drive distance from Oakland.
2. **Quality** (`quality_score` table): Reddit mentions + upvotes + curated list appearances → composite score, with a `uniqueness` boost for sites that aren't the obvious top-50 (Yosemite/Big Sur etc.) — this is how hidden gems surface.
3. **Live availability** (camply): for top-N quality-ranked candidates, check the user's date window. Only sites bookable for all N nights are returned.
4. **Output**: markdown table with name, drive time, theme tags, sample Reddit quote, booking URL.

## Configuration

`config.yaml` controls home location (Oakland: 37.8044, -122.2712), max drive hours, subreddits, scoring weights, and curated source URLs.

## Refresh cadence

Catalog + Reddit + curated: monthly. Availability: every query (live).
