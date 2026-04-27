# claude_campsites

Claude Code skill that finds great car-camping sites within ~5 hours of Oakland, fusing **live availability** from Recreation.gov (via [camply](https://github.com/juftin/camply)) with **quality signals** scraped from Reddit and curated lists. Built around the observation that booking systems tell you what's *bookable* but say nothing about what's *good* — Reddit and editorial lists fill the gap.

See [plan.md](./plan.md) for design and [SKILL.md](./SKILL.md) for skill metadata.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install camply praw rapidfuzz geopy pyyaml requests
cp .env.example .env   # then add your RIDB_API_KEY (free at ridb.recreation.gov)
set -a && source .env && set +a
```

## Bootstrap (run once, then monthly)

```bash
python scripts/ingest_ridb.py      # federal facility catalog → SQLite
python scripts/ingest_reddit.py    # Reddit mentions (no auth, public JSON)
python scripts/ingest_curated.py   # curated blog lists
python scripts/score.py            # composite quality_score
```

Or run a 30-site curated NorCal seed without any API keys:
```bash
python scripts/seed_norcal.py
```

## Query

```bash
# Memorial Day weekend, 2 nights, within 5h of Oakland
python scripts/find.py --start 2026-05-24 --nights 2 --max-hours 5

# Hidden-gem redwoods only
python scripts/find.py --hidden-gems --theme redwoods

# Skip live availability (catalog-only ranking)
python scripts/find.py --no-availability
```

## Install as a Claude Code skill

Symlink (or copy) into your skills directory so `/camp-finder` works in Claude Code:

```bash
ln -s "$(pwd)" ~/.claude/skills/camp-finder
```

## Architecture

| Layer | Source | Cadence |
|---|---|---|
| Catalog (`facilities`) | RIDB API | Monthly |
| Quality signal (`reddit_mentions`, `curated_mentions`, `quality_score`) | Reddit public JSON, curated blog lists | Monthly |
| Live availability | camply → Recreation.gov | Per query |

SQLite holds the slow-moving data so queries are fast; availability is always live.
