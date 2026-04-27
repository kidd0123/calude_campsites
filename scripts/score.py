"""Compute composite quality_score from reddit + curated mentions.

Run: python scripts/score.py
"""
import math
from collections import Counter

from db import connect, init_schema, load_config


def main():
    cfg = load_config()
    weights = cfg["scoring"]["weights"]
    hidden_top = cfg["scoring"]["hidden_gem_top_n"]

    conn = connect()
    init_schema(conn)
    conn.execute("DELETE FROM quality_score")

    rows = conn.execute("""
        SELECT f.id, f.name,
               COUNT(DISTINCT rm.id) AS mention_count,
               COALESCE(SUM(MAX(rm.score, 0)), 0) AS reddit_sum,
               GROUP_CONCAT(rm.themes, ',') AS theme_blob,
               (SELECT COUNT(*) FROM curated_mentions cm WHERE cm.facility_id=f.id) AS curated_count
        FROM facilities f
        LEFT JOIN reddit_mentions rm ON rm.facility_id = f.id
        GROUP BY f.id
    """).fetchall()

    enriched = []
    for r in rows:
        reddit_n = r["mention_count"] or 0
        reddit_sum = r["reddit_sum"] or 0
        curated = r["curated_count"] or 0
        themes = ",".join(sorted({t for t in (r["theme_blob"] or "").split(",") if t}))
        reddit_score = math.log1p(reddit_sum) + 0.5 * math.log1p(reddit_n)
        enriched.append({
            "id": r["id"], "reddit_n": reddit_n, "reddit_sum": reddit_sum,
            "curated": curated, "themes": themes, "reddit_score": reddit_score,
        })

    # uniqueness: anything ranked > hidden_top by raw mention_count gets a boost
    enriched.sort(key=lambda x: -x["reddit_n"])
    for i, e in enumerate(enriched):
        if e["reddit_n"] == 0:
            e["uniqueness"] = 0.0
        elif i < hidden_top:
            e["uniqueness"] = 0.0
        else:
            e["uniqueness"] = 1.0  # lesser-known but mentioned → hidden gem

    # normalise components 0..1
    max_reddit = max((e["reddit_score"] for e in enriched), default=1) or 1
    max_curated = max((e["curated"] for e in enriched), default=1) or 1

    for e in enriched:
        rn = e["reddit_score"] / max_reddit
        cn = e["curated"] / max_curated
        un = e["uniqueness"]
        tn = 1.0 if e["themes"] else 0.0
        e["composite"] = (
            weights["reddit"] * rn
            + weights["curated"] * cn
            + weights["uniqueness"] * un
            + weights["theme"] * tn
        )

    for e in enriched:
        conn.execute(
            """INSERT INTO quality_score
               (facility_id, reddit_mention_count, reddit_score_sum, curated_count,
                uniqueness, themes, composite)
               VALUES (?,?,?,?,?,?,?)""",
            (e["id"], e["reddit_n"], e["reddit_sum"], e["curated"],
             e["uniqueness"], e["themes"], round(e["composite"], 4)),
        )
    conn.commit()
    top = conn.execute("""
        SELECT f.name, q.composite, q.reddit_mention_count, q.uniqueness, q.themes
        FROM quality_score q JOIN facilities f ON f.id=q.facility_id
        ORDER BY q.composite DESC LIMIT 25
    """).fetchall()
    print("Top 25 by quality:")
    for r in top:
        print(f"  {r['composite']:.3f}  [{r['reddit_mention_count']:3d}m gem={int(r['uniqueness'])}] "
              f"{r['name']}  {r['themes']}")
    conn.close()


if __name__ == "__main__":
    main()
