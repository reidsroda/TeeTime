import asyncio
import json
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from playwright.async_api import async_playwright


# ── DATABASE ─────────────────────────────────────────────────
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
            distance_miles REAL,
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


# ── GEOCODING ─────────────────────────────────────────────────
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

    preferred = ["city", "town", "village", "municipality", "administrative"]
    best = next(
        (r for r in data if r.get("type") in preferred or r.get("class") == "place"),
        data[0]
    )
    print(f"Using location: {best['display_name']}")
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


# ── PLAYWRIGHT SCRAPER ────────────────────────────────────────
async def scrape_supreme_golf(
    state_slug: str,
    city_slug: str,
    date: str,
    players: int = 1,
    holes: int = 18
) -> list:

    search_url = (
        f"https://www.supremegolf.com/search"
        f"?hierarchized_url=/united-states/{state_slug}/{city_slug}"
        f"&date={date}"
        f"&players={players}"
        f"&holes={holes}"
    )

    print(f"Loading: {search_url}")

    intercepted_courses = []
    intercepted_tee_times = {}  # course_id -> list of tee time groups

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900}
        )
        page = await context.new_page()

        async def handle_response(response):
            url = response.url
            try:
                if "location_list" in url and response.status == 200:
                    data = await response.json()
                    courses = []
                    for item in data.get("location_results", []):
                        if item.get("type") == "Course":
                            c = item["course"]
                            if c.get("stats", {}).get("tee_times_count", 0) > 0:
                                courses.append(c)
                    intercepted_courses.extend(courses)
                    print(f"  Intercepted location_list: {len(courses)} courses with tee times")

                elif "tee_time_groups/at/" in url and response.status == 200:
                    data = await response.json()
                    # Extract course_id from URL
                    parts = url.split("tee_time_groups/at/")
                    if len(parts) > 1:
                        course_id = int(parts[1].split("?")[0])
                        groups = data.get("tee_time_groups", [])
                        if groups:
                            intercepted_tee_times[course_id] = groups
            except Exception as e:
                pass

        page.on("response", handle_response)

        # Navigate to search page — this triggers location_list API call
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(5)

        # Now click each course card to trigger tee_time_groups API calls
        # Or scroll to trigger lazy loading
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(3)
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(2)

        await browser.close()

    print(f"Intercepted {len(intercepted_courses)} courses, {len(intercepted_tee_times)} with tee time details")

    # Build results
    results = []
    scraped_at = datetime.now().isoformat()

    for course in intercepted_courses:
        course_id = course["id"]
        course_name = course["name"]
        address = course.get("formatted_address", "")
        city = course.get("address_city", "")
        state = course.get("address_state", "")
        rating = course.get("rating", {}).get("value", 0.0)
        rating_count = course.get("rating", {}).get("count", 0)
        photo_url = course.get("photo_medium_url", "")
        distance = course.get("distance", 0)
        hierarchized_url = course.get("hierarchized_url", "")
        booking_url = f"https://www.supremegolf.com{hierarchized_url}"

        tee_time_groups = intercepted_tee_times.get(course_id, [])

        if tee_time_groups:
            # We have detailed tee times
            for group in tee_time_groups:
                tee_off = group.get("tee_off_at_timezone", "")
                try:
                    dt = datetime.fromisoformat(tee_off)
                    tee_time_str = dt.strftime("%I:%M %p").lstrip("0")
                except:
                    tee_time_str = ""

                price = group.get("starting_rate", 0.0)
                hole_count = (group.get("holes") or [holes])[0]
                walking = 1 if "is_walking" in group.get("amenity_codes", []) else 0

                for player_count in group.get("players", [players]):
                    if price > 0 and tee_time_str:
                        results.append({
                            "course_id": course_id,
                            "course_name": course_name,
                            "address": address,
                            "city": city,
                            "state": state,
                            "price": float(price),
                            "tee_time": tee_time_str,
                            "date": date,
                            "holes": hole_count,
                            "players": player_count,
                            "walking": walking,
                            "rating": round(float(rating), 2),
                            "rating_count": int(rating_count),
                            "photo_url": photo_url,
                            "distance_miles": float(distance),
                            "source_platform": "Supreme Golf",
                            "booking_url": booking_url,
                            "scraped_at": scraped_at
                        })
        else:
            # Fall back to summary data from location_list
            stats = course.get("stats", {})
            min_rate = stats.get("min_rate", 0.0)
            min_tee_off = stats.get("min_tee_off_at", "")
            max_tee_off = stats.get("max_tee_off_at", "")

            try:
                dt = datetime.fromisoformat(min_tee_off.replace("Z", "+00:00"))
                tee_time_str = dt.strftime("%I:%M %p").lstrip("0")
            except:
                tee_time_str = "See site"

            if min_rate > 0:
                results.append({
                    "course_id": course_id,
                    "course_name": course_name,
                    "address": address,
                    "city": city,
                    "state": state,
                    "price": float(min_rate),
                    "tee_time": tee_time_str,
                    "date": date,
                    "holes": holes,
                    "players": players,
                    "walking": 0,
                    "rating": round(float(rating), 2),
                    "rating_count": int(rating_count),
                    "photo_url": photo_url,
                    "distance_miles": float(distance),
                    "source_platform": "Supreme Golf",
                    "booking_url": booking_url,
                    "scraped_at": scraped_at
                })

    print(f"Total tee times built: {len(results)}")
    return results


# ── MAIN ─────────────────────────────────────────────────────
async def main():
    init_db()

    location = input("Enter city, state (e.g. Miami, FL): ").strip()
    if not location:
        return

    days_ahead = input("How many days ahead? (default 1): ").strip()
    days_ahead = int(days_ahead) if days_ahead.isdigit() else 1

    players = input("Number of players? (default 1): ").strip()
    players = int(players) if players.isdigit() else 1

    holes = input("9 or 18 holes? (default 18): ").strip()
    holes = int(holes) if holes in ["9", "18"] else 18

    print(f"\nLooking up '{location}'...")
    try:
        lat, lng, display_name = get_coordinates(location)
    except ValueError as e:
        print(e)
        return

    state_slug, city_slug = format_location_for_supreme(display_name)
    print(f"Supreme Golf path: /united-states/{state_slug}/{city_slug}\n")

    base_date = datetime.today()
    total = []

    for i in range(days_ahead):
        date_str = (base_date + timedelta(days=i)).strftime("%Y-%m-%d")
        print(f"Scraping {date_str}...")
        results = await scrape_supreme_golf(
            state_slug=state_slug,
            city_slug=city_slug,
            date=date_str,
            players=players,
            holes=holes
        )
        if results:
            save_tee_times(results)
            total.extend(results)

    print(f"\n── Done: {len(total)} tee times saved ──\n")

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

    if rows:
        print(f"{'Course':<40} {'Time':<10} {'Price':<8} {'Rating':<7} {'City':<20} {'Miles'}")
        print("-" * 100)
        for row in rows:
            print(f"{row[0]:<40} {row[1]:<10} ${row[2]:<7.2f} {row[3]:<7} {row[4]:<20} {row[5]}")
    else:
        print("No results. Check the interception counts above.")


if __name__ == "__main__":
    asyncio.run(main())
