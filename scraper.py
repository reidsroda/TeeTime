import asyncio
import json
import sqlite3
import aiohttp
import urllib.parse
import urllib.request
from datetime import datetime, timedelta


# ── DATABASE SETUP ──────────────────────────────────────────
def init_db():
    conn = sqlite3.connect("tee_times.db")
    c = conn.cursor()
    c.execute("DROP TABLE IF EXISTS tee_times")
    c.execute("""
        CREATE TABLE IF NOT EXISTS tee_times (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER,
            course_name TEXT,
            address TEXT,
            city TEXT,
            state TEXT,
            price REAL,
            tee_time TEXT,
            date TEXT,
            holes INTEGER,
            players INTEGER,
            walking INTEGER,
            rating REAL,
            rating_count INTEGER,
            photo_url TEXT,
            distance_miles INTEGER,
            source_platform TEXT,
            booking_url TEXT,
            scraped_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_tee_times(tee_times: list):
    if not tee_times:
        return
    conn = sqlite3.connect("tee_times.db")
    c = conn.cursor()
    c.executemany("""
        INSERT INTO tee_times (
            course_id, course_name, address, city, state, price, tee_time,
            date, holes, players, walking, rating, rating_count, photo_url,
            distance_miles, source_platform, booking_url, scraped_at
        ) VALUES (
            :course_id, :course_name, :address, :city, :state, :price, :tee_time,
            :date, :holes, :players, :walking, :rating, :rating_count, :photo_url,
            :distance_miles, :source_platform, :booking_url, :scraped_at
        )
    """, tee_times)
    conn.commit()
    conn.close()


# ── GEOCODING ────────────────────────────────────────────────
def get_coordinates(location: str) -> tuple:
    query = urllib.parse.quote(location)
    url = (
        f"https://nominatim.openstreetmap.org/search"
        f"?q={query}&format=json&limit=5&addressdetails=1&countrycodes=us"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "TeeTimeFinder/1.0"})
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read())

    if not data:
        raise ValueError(f"Could not find coordinates for: {location}")

    print("\nLocation candidates found:")
    for i, result in enumerate(data):
        print(f"  [{i}] {result['display_name']}")

    preferred = ["city", "town", "village", "municipality", "administrative"]
    best = next((r for r in data if r.get("type") in preferred or r.get("class") == "place"), data[0])

    print(f"\nUsing: {best['display_name']}")
    confirm = input("Is this correct? (y/n): ").strip().lower()
    if confirm != "y":
        idx = input(f"Enter number [0-{len(data)-1}]: ").strip()
        if idx.isdigit() and int(idx) < len(data):
            best = data[int(idx)]

    return float(best["lat"]), float(best["lon"]), best["display_name"]


def format_location_for_supreme(display_name: str) -> tuple:
    parts = [p.strip().lower() for p in display_name.split(",")]
    city_slug = parts[0].replace(" ", "-")
    us_states = {
        "alabama": "alabama", "alaska": "alaska", "arizona": "arizona",
        "arkansas": "arkansas", "california": "california", "colorado": "colorado",
        "connecticut": "connecticut", "delaware": "delaware", "florida": "florida",
        "georgia": "georgia", "hawaii": "hawaii", "idaho": "idaho",
        "illinois": "illinois", "indiana": "indiana", "iowa": "iowa",
        "kansas": "kansas", "kentucky": "kentucky", "louisiana": "louisiana",
        "maine": "maine", "maryland": "maryland", "massachusetts": "massachusetts",
        "michigan": "michigan", "minnesota": "minnesota", "mississippi": "mississippi",
        "missouri": "missouri", "montana": "montana", "nebraska": "nebraska",
        "nevada": "nevada", "new hampshire": "new-hampshire", "new jersey": "new-jersey",
        "new mexico": "new-mexico", "new york": "new-york", "north carolina": "north-carolina",
        "north dakota": "north-dakota", "ohio": "ohio", "oklahoma": "oklahoma",
        "oregon": "oregon", "pennsylvania": "pennsylvania", "rhode island": "rhode-island",
        "south carolina": "south-carolina", "south dakota": "south-dakota",
        "tennessee": "tennessee", "texas": "texas", "utah": "utah",
        "vermont": "vermont", "virginia": "virginia", "washington": "washington",
        "west virginia": "west-virginia", "wisconsin": "wisconsin", "wyoming": "wyoming"
    }
    state_slug = next((us_states[p.strip()] for p in parts if p.strip() in us_states), "")
    return state_slug, city_slug


# ── STEP 1: Get course list via location_list API ────────────
async def fetch_course_list(
    state_slug: str,
    city_slug: str,
    date: str,
    holes: int = 18,
    players: int = 1
) -> list:
    hierarchized_url = f"/united-states/{state_slug}/{city_slug}"
    url = (
        f"https://api.supremegolf.com/location_list"
        f"?hierarchized_url={urllib.parse.quote(hierarchized_url)}"
        f"&date={date}"
        f"&holes={holes if holes else ''}"
        f"&players={players}"
        f"&is_prepaid_only=false"
        f"&hot_deals_search=false"
    )

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.supremegolf.com/"
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                data = await resp.json()
                courses = []
                for item in data.get("location_results", []):
                    if item.get("type") == "Course":
                        c = item["course"]
                        # Only include courses with available tee times
                        if c.get("stats", {}).get("tee_times_count", 0) > 0:
                            courses.append({
                                "id": c["id"],
                                "name": c["name"],
                                "address": c.get("formatted_address", ""),
                                "city": c.get("address_city", ""),
                                "state": c.get("address_state", ""),
                                "rating": c.get("rating", {}).get("value", 0.0),
                                "rating_count": c.get("rating", {}).get("count", 0),
                                "photo_url": c.get("photo_medium_url", ""),
                                "distance": c.get("distance", 0),
                                "min_rate": c.get("min_rate", 0),
                                "max_rate": c.get("max_rate", 0),
                                "hierarchized_url": c.get("hierarchized_url", ""),
                            })
                print(f"Found {len(courses)} courses with available tee times")
                return courses
            else:
                print(f"location_list API returned {resp.status}")
                return []


# ── STEP 2: Get tee times per course via tee_time_groups API ─
async def fetch_tee_times_for_course(
    session: aiohttp.ClientSession,
    course: dict,
    date: str,
    holes: int = 18
) -> list:
    course_id = course["id"]
    url = (
        f"https://api.supremegolf.com/api/v6/tee_time_groups/at/{course_id}"
        f"?date={date}"
        f"&num_holes={holes}"
        f"&is_prepaid_only=false"
        f"&include_featured=true"
        f"&network_membership_only=false"
    )

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.supremegolf.com/"
    }

    results = []
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                booking_url = f"https://www.supremegolf.com{course['hierarchized_url']}"

                for group in data.get("tee_time_groups", []):
                    tee_off = group.get("tee_off_at_timezone", "")
                    try:
                        dt = datetime.fromisoformat(tee_off)
                        tee_time_str = dt.strftime("%I:%M %p").lstrip("0")
                    except:
                        tee_time_str = ""

                    amenities = group.get("amenity_codes", [])
                    walking = 1 if "is_walking" in amenities else 0
                    holes_list = group.get("holes", [holes])
                    hole_count = holes_list[0] if holes_list else holes

                    for player_count in group.get("players", [1]):
                        results.append({
                            "course_id": course_id,
                            "course_name": course["name"],
                            "address": course["address"],
                            "city": course["city"],
                            "state": course["state"],
                            "price": group.get("starting_rate", 0.0),
                            "tee_time": tee_time_str,
                            "date": date,
                            "holes": hole_count,
                            "players": player_count,
                            "walking": walking,
                            "rating": round(course["rating"], 2),
                            "rating_count": course["rating_count"],
                            "photo_url": course["photo_url"],
                            "distance_miles": course["distance"],
                            "source_platform": "Supreme Golf",
                            "booking_url": booking_url,
                            "scraped_at": datetime.now().isoformat()
                        })
    except Exception as e:
        print(f"  Error fetching tee times for {course['name']}: {e}")

    return results


# ── MAIN SCRAPE FUNCTION ─────────────────────────────────────
async def scrape_supreme_golf(
    location: str,
    lat: float,
    lng: float,
    date: str,
    players: int = 1,
    holes: int = 18,
    state_slug: str = "",
    city_slug: str = ""
) -> list:
    print(f"\nFetching course list for {city_slug}, {state_slug} on {date}...")

    # Step 1: get all courses
    courses = await fetch_course_list(state_slug, city_slug, date, holes, players)
    if not courses:
        print("No courses found.")
        return []

    # Step 2: fetch tee times for each course concurrently
    all_results = []
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_tee_times_for_course(session, course, date, holes) for course in courses]
        batch_size = 10  # limit concurrent requests
        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i + batch_size]
            batch_results = await asyncio.gather(*batch)
            for r in batch_results:
                all_results.extend(r)
            print(f"  Fetched tee times for {min(i + batch_size, len(tasks))}/{len(tasks)} courses")
            await asyncio.sleep(0.5)  # small delay between batches

    print(f"Total tee times fetched: {len(all_results)}")
    return all_results


# ── MAIN ─────────────────────────────────────────────────────
async def main():
    init_db()

    location = input("Enter your location (city, state or zip code): ").strip()
    if not location:
        print("No location entered.")
        return

    days_ahead = input("How many days ahead to search? (default 3): ").strip()
    days_ahead = int(days_ahead) if days_ahead.isdigit() else 3

    players = input("Number of players? (default 1): ").strip()
    players = int(players) if players.isdigit() else 1

    holes = input("9 or 18 holes? (default 18): ").strip()
    holes = int(holes) if holes in ["9", "18"] else 18

    print(f"\nLooking up coordinates for '{location}'...")
    try:
        lat, lng, display_name = get_coordinates(location)
        print(f"Coordinates: {lat}, {lng}\n")
    except ValueError as e:
        print(e)
        return

    state_slug, city_slug = format_location_for_supreme(display_name)
    print(f"Supreme Golf path: /explore/united-states/{state_slug}/{city_slug}")

    base_date = datetime.today()
    total_results = []

    for i in range(days_ahead):
        date_str = (base_date + timedelta(days=i)).strftime("%Y-%m-%d")
        print(f"\nScraping {date_str}...")
        results = await scrape_supreme_golf(
            location=location, lat=lat, lng=lng,
            date=date_str, players=players, holes=holes,
            state_slug=state_slug, city_slug=city_slug
        )
        if results:
            save_tee_times(results)
            total_results.extend(results)

    print(f"\n── Done: {len(total_results)} tee times saved ──")

    conn = sqlite3.connect("tee_times.db")
    c = conn.cursor()
    c.execute("""
        SELECT course_name, tee_time, price, rating, city, distance_miles
        FROM tee_times
        ORDER BY rating DESC, price ASC
        LIMIT 15
    """)
    rows = c.fetchall()
    conn.close()

    print(f"\n{'Course':<40} {'Time':<10} {'Price':<8} {'Rating':<7} {'City':<20} {'Miles'}")
    print("-" * 100)
    for row in rows:
        print(f"{row[0]:<40} {row[1]:<10} ${row[2]:<7} {row[3]:<7} {row[4]:<20} {row[5]}")


if __name__ == "__main__":
    asyncio.run(main())