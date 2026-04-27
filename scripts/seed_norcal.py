"""Seed a small set of well-known NorCal car-camping facilities.

Lets you exercise score.py and find.py --no-availability without any API keys.
For the real catalog, run ingest_ridb.py once you have RIDB_API_KEY.

IDs use the real Recreation.gov FacilityIDs where known so live availability
checks work later. Manually curated.
"""
from db import connect, init_schema, load_config, haversine_miles

SEED = [
    # (rg_id, name, lat, lon, url_path)
    ("232447", "Big Basin Redwoods State Park", 37.1721, -122.2231, None),
    ("234064", "Pinnacles Campground", 36.4906, -121.1431, "232486"),
    ("232463", "Henry Cowell Redwoods State Park", 37.0421, -122.0633, None),
    ("232450", "Salt Point State Park", 38.5701, -123.3316, None),
    ("234646", "Russian Gulch State Park", 39.3290, -123.8023, None),
    ("232452", "Samuel P. Taylor State Park", 38.0179, -122.7299, None),
    ("234077", "Bothe-Napa Valley State Park", 38.5511, -122.5263, None),
    ("234058", "Sunset State Beach", 36.8901, -121.8237, None),
    ("232447", "Mount Diablo State Park", 37.8816, -121.9142, None),
    ("232486", "Pinnacles National Park", 36.4906, -121.1431, None),
    ("232453", "Manresa Uplands State Beach", 36.9240, -121.8479, None),
    ("232484", "Fremont Peak State Park", 36.7611, -121.5042, None),
    ("234050", "Mendocino Headlands", 39.3072, -123.7989, None),
    ("232446", "Castle Rock State Park", 37.2289, -122.0958, None),
    ("232492", "Calaveras Big Trees State Park", 38.2742, -120.3066, None),
    ("232489", "D.L. Bliss State Park", 39.0398, -120.0993, None),
    ("232488", "Sugar Pine Point State Park", 39.0573, -120.1188, None),
    ("232490", "Plumas-Eureka State Park", 39.7507, -120.7035, None),
    ("232491", "Sly Creek (USFS)", 39.5810, -121.0954, None),
    ("234052", "Kirby Cove (Marin Headlands)", 37.8265, -122.4843, None),
    ("232448", "Limekiln State Park", 36.0085, -121.5180, None),
    ("232449", "Pfeiffer Big Sur State Park", 36.2528, -121.7867, None),
    ("232451", "Andrew Molera State Park", 36.2855, -121.8467, None),
    ("232454", "Sonoma Coast (Wright's Beach)", 38.4180, -123.0982, None),
    ("232455", "Doran Regional Park", 38.3138, -123.0433, None),
    ("232456", "Lassen Volcanic NP - Manzanita Lake", 40.5346, -121.5640, None),
    ("232457", "Lava Beds NM Indian Well", 41.7124, -121.5071, None),
    ("232458", "Lake Sonoma (Liberty Glen)", 38.7165, -123.0121, None),
    ("232459", "Wrights Lake (USFS)", 38.8434, -120.2320, None),
    ("232460", "Yosemite Hodgdon Meadow", 37.7995, -119.8541, None),
]


def main():
    cfg = load_config()
    home_lat, home_lon = cfg["home"]["lat"], cfg["home"]["lon"]
    conn = connect()
    init_schema(conn)
    n = 0
    for rg_id, name, lat, lon, _ in SEED:
        miles = haversine_miles(home_lat, home_lon, lat, lon)
        conn.execute(
            """INSERT OR REPLACE INTO facilities
               (id, provider, name, lat, lon, state, facility_type,
                car_accessible, url, miles_from_home, drive_hours_est)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"rg:{rg_id}",
                "recreation_gov",
                name,
                lat, lon, "CA",
                "Campground",
                1,
                f"https://www.recreation.gov/camping/campgrounds/{rg_id}",
                round(miles, 1),
                round(miles / 50.0, 2),
            ),
        )
        n += 1
    conn.commit()
    print(f"Seeded {n} facilities.")
    for r in conn.execute(
        "SELECT name, miles_from_home, drive_hours_est FROM facilities ORDER BY drive_hours_est LIMIT 10"
    ):
        print(f"  {r['drive_hours_est']:.1f}h  {r['miles_from_home']:.0f}mi  {r['name']}")
    conn.close()


if __name__ == "__main__":
    main()
