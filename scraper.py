import asyncio
import json
import sqlite3
import urllib.parse
import aiohttp
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


# ── LOCATION PARSING ──────────────────────────────────────────
def format_location_for_supreme(raw_input: str) -> tuple:
    """Convert 'Miami, FL' -> ('florida', 'miami')"""
    parts = [p.strip().lower() for p in raw_input.split(",")]
    city_slug = parts[0].replace(" ", "-")

    us_state_abbrevs = {
        "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas",
        "ca": "california", "co": "colorado", "ct": "connecticut", "de": "delaware",
        "fl": "florida", "ga": "georgia", "hi": "hawaii", "id": "idaho",
        "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas",
        "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland",
        "ma": "massachusetts", "mi": "michigan", "mn": "minnesota", "ms": "mississippi",
        "mo": "missouri", "mt": "montana", "ne": "nebraska", "nv": "nevada",
        "nh": "new-hampshire", "nj": "new-jersey", "nm": "new-mexico", "ny": "new-york",
        "nc": "north-carolina", "nd": "north-dakota", "oh": "ohio", "ok": "oklahoma",
        "or": "oregon", "pa": "pennsylvania", "ri": "rhode-island", "sc": "south-carolina",
        "sd": "south-dakota", "tn": "tennessee", "tx": "texas", "ut": "utah",
        "vt": "vermont", "va": "virginia", "wa": "washington", "wv": "west-virginia",
        "wi": "wisconsin", "wy": "wyoming"
    }
    us_states_full = {
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

    state_slug = ""
    for p in parts[1:]:
        p = p.strip().lower()
        if p in us_state_abbrevs:
            state_slug = us_state_abbrevs[p]
            break
        if p in us_states_full:
            state_slug = us_states_full[p]
            break

    return state_slug, city_slug


# ── GET CITY ID ───────────────────────────────────────────────
async def get_city_id(state_slug: str, city_slug: str) -> int:
    """Fetch the Supreme Golf cityId for a given city via the /find API (no Cloudflare)"""
    url = (
        f"https://api.supremegolf.com/find"
        f"?hierarchized_url=/united-states/{state_slug}/{city_slug}"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www.supremegolf.com/"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    city_id = data.get("city", {}).get("id")
                    print(f"  City ID: {city_id}")
                    return city_id
    except Exception as e:
        print(f"  Could not fetch city ID: {e}")
    return None


# ── PLAYWRIGHT SCRAPER ────────────────────────────────────────
async def scrape_supreme_golf(
    state_slug: str,
    city_slug: str,
    date: str,
    players: int = 1,
    holes: int = 18
) -> list:

    # Get city ID first (no Cloudflare on this endpoint)
    city_id = await get_city_id(state_slug, city_slug)

    search_url = (
        f"https://www.supremegolf.com/explore/united-states/{state_slug}/{city_slug}"
        f"?date={date}"
        f"&players={players}"
        f"&holes={holes}"
        f"&hotDealsSearch=false"
        f"&isPrepaidOnly=false"
        f"&networkMembershipOnly=false"
    )
    if city_id:
        search_url += f"&cityId={city_id}"

    print(f"Loading: {search_url}")

    intercepted_courses = []
    intercepted_tee_times = {}

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
                    parts = url.split("tee_time_groups/at/")
                    if len(parts) > 1:
                        course_id = int(parts[1].split("?")[0])
                        groups = data.get("tee_time_groups", [])
                        if groups:
                            intercepted_tee_times[course_id] = groups
            except Exception:
                pass

        page.on("response", handle_response)

        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

        # Wait up to 15 seconds for location_list to fire
        for _ in range(15):
            await asyncio.sleep(1)
            if intercepted_courses:
                await asyncio.sleep(2)
                break

        # Use browser's own fetch() to call tee_time_groups for each course
        # Requests originate inside the browser so Cloudflare cookies are attached
        if intercepted_courses:
            print(f"  Fetching tee times for {len(intercepted_courses)} courses via in-browser fetch...")
            for course in intercepted_courses:
                course_id = course["id"]
                api_url = (
                    f"https://api.supremegolf.com/api/v6/tee_time_groups/at/{course_id}"
                    f"?date={date}&num_holes={holes}&is_prepaid_only=false"
                    f"&include_featured=true&network_membership_only=false"
                )
                try:
                    result = await page.evaluate(f"""
                        async () => {{
                            const resp = await fetch("{api_url}", {{
                                headers: {{
                                    "Accept": "application/json",
                                    "Referer": "https://www.supremegolf.com/"
                                }}
                            }});
                            if (resp.ok) return await resp.json();
                            return null;
                        }}
                    """)
                    if result and result.get("tee_time_groups"):
                        intercepted_tee_times[course_id] = result["tee_time_groups"]
                        print(f"    Course {course_id}: {len(result['tee_time_groups'])} tee times")
                except Exception as e:
                    print(f"    Error for course {course_id}: {e}")
                await asyncio.sleep(0.3)

        await browser.close()

    print(f"Intercepted {len(intercepted_courses)} courses, {len(intercepted_tee_times)} with tee time details")

    # ── BUILD RESULTS ─────────────────────────────────────────
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
            # Detailed tee times from tee_time_groups API
            for group in tee_time_groups:
                tee_off = group.get("tee_off_at_timezone", "")
                try:
                    clean = tee_off.replace("Z", "").split(".")[0]
                    dt = datetime.fromisoformat(clean)
                    tee_time_str = dt.strftime("%I:%M %p").lstrip("0")
                except Exception:
                    tee_time_str = ""

                price = group.get("starting_rate", 0.0)
                hole_count = (group.get("holes") or [holes])[0]
                walking = 1 if "is_walking" in group.get("amenity_codes", []) else 0

                for player_count in group.get("players", [players]):
                    if tee_time_str:
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
            # Fallback: summary data from location_list
            stats = course.get("stats", {})
            min_rate = stats.get("min_rate") or course.get("min_rate") or 0.0
            min_tee_off = stats.get("min_tee_off_at", "")

            try:
                clean = min_tee_off.replace("Z", "").split(".")[0]
                dt = datetime.fromisoformat(clean)
                tee_time_str = dt.strftime("%I:%M %p").lstrip("0")
            except Exception:
                tee_time_str = "See site"

            results.append({
                "course_id": course_id,
                "course_name": course_name,
                "address": address,
                "city": city,
                "state": state,
                "price": float(min_rate) if min_rate else 0.0,
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


# ── CALLED BY BACKEND ─────────────────────────────────────────
async def run_scraper(city: str, state_abbrev: str, date: str, players: int = 1, holes: int = 18):
    """Entry point called by backend.py"""
    init_db()
    location = f"{city}, {state_abbrev}"
    state_slug, city_slug = format_location_for_supreme(location)
    print(f"Supreme Golf path: /united-states/{state_slug}/{city_slug}")

    results = await scrape_supreme_golf(
        state_slug=state_slug,
        city_slug=city_slug,
        date=date,
        players=players,
        holes=holes
    )
    if results:
        save_tee_times(results)
    return len(results)


# ── MAIN (standalone) ─────────────────────────────────────────
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

    state_slug, city_slug = format_location_for_supreme(location)
    print(f"Supreme Golf path: /united-states/{state_slug}/{city_slug}\n")

    base_date = datetime.today()
    total = []

    for i in range(days_ahead):
        date_str = (base_date + timedelta(days=i + 1)).strftime("%Y-%m-%d")
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
        print("No results found.")


if __name__ == "__main__":
    asyncio.run(main())