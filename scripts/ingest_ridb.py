"""Ingest the federal facility catalog into SQLite.

Uses the RIDB API (requires RIDB_API_KEY env var, free at ridb.recreation.gov).
Pages through all Campground facilities for CA + neighbor states, computes
distance from home, applies a coarse car-accessible heuristic.

Run: python scripts/ingest_ridb.py
"""
import json
import os
import sys
import time
import requests

from db import connect, init_schema, load_config, haversine_miles

RIDB_BASE = "https://ridb.recreation.gov/api/v1"
PAGE_SIZE = 50


def fetch_facilities(api_key, state):
    headers = {"apikey": api_key}
    offset = 0
    while True:
        params = {
            "state": state,
            "activity": "CAMPING",
            "limit": PAGE_SIZE,
            "offset": offset,
        }
        r = requests.get(f"{RIDB_BASE}/facilities", headers=headers, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        recdata = data.get("RECDATA", [])
        if not recdata:
            break
        for fac in recdata:
            yield fac
        if len(recdata) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(0.2)


def car_accessible(name, description, exclude_keywords):
    blob = f"{name or ''} {description or ''}".lower()
    return 0 if any(k in blob for k in exclude_keywords) else 1


def main():
    api_key = os.environ.get("RIDB_API_KEY")
    if not api_key:
        sys.exit("Set RIDB_API_KEY (free at https://ridb.recreation.gov/ → Account → API Key)")

    cfg = load_config()
    home_lat = cfg["home"]["lat"]
    home_lon = cfg["home"]["lon"]
    excludes = [k.lower() for k in cfg["catalog"]["exclude_keywords"]]
    max_radius = cfg["catalog"]["max_radius_miles"]

    conn = connect()
    init_schema(conn)
    inserted = skipped_far = skipped_nogeo = 0

    for state in cfg["catalog"]["states"]:
        print(f"Fetching {state}…", flush=True)
        for fac in fetch_facilities(api_key, state):
            if (fac.get("FacilityTypeDescription") or "").lower() != "campground":
                continue
            try:
                lat = float(fac.get("FacilityLatitude") or 0) or None
                lon = float(fac.get("FacilityLongitude") or 0) or None
            except (TypeError, ValueError):
                lat = lon = None
            if not lat or not lon:
                skipped_nogeo += 1
                continue
            miles = haversine_miles(home_lat, home_lon, lat, lon)
            if miles is None or miles > max_radius:
                skipped_far += 1
                continue
            name = fac.get("FacilityName") or ""
            description = fac.get("FacilityDescription") or ""
            conn.execute(
                """INSERT OR REPLACE INTO facilities
                   (id, provider, name, lat, lon, state, facility_type,
                    car_accessible, amenities_json, url, description,
                    miles_from_home, drive_hours_est)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"rg:{fac['FacilityID']}",
                    "recreation_gov",
                    name,
                    lat, lon, state,
                    "Campground",
                    car_accessible(name, description, excludes),
                    json.dumps(fac.get("ACTIVITY") or []),
                    f"https://www.recreation.gov/camping/campgrounds/{fac['FacilityID']}",
                    description[:2000],
                    round(miles, 1),
                    round(miles / 50.0, 2),
                ),
            )
            inserted += 1
        conn.commit()

    print(f"Inserted {inserted}, skipped {skipped_far} (too far), {skipped_nogeo} (no geo)")
    conn.close()


if __name__ == "__main__":
    main()
