import asyncio
import json
import sqlite3
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
import time
import random
import urllib.request
import urllib.parse
import re

def format_location_for_supreme(display_name: str) -> tuple:
    """
    Convert 'Miami, Miami-Dade County, Florida, United States'
    into ('florida', 'miami', 'Miami')
    """
    parts = [p.strip().lower() for p in display_name.split(",")]
    
    # parts[0] = city, parts[-2] = state (usually), parts[-1] = country
    city_slug = parts[0].replace(" ", "-")
    
    # Find the state — it's usually the second to last part before "United States"
    state_slug = ""
    for part in parts:
        part = part.strip()
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
        if part in us_states:
            state_slug = us_states[part]
            break

    return state_slug, city_slug

def get_coordinates(location: str) -> tuple:
    """Convert a city/zip to lat/lng using free Nominatim API"""
    import urllib.parse
    import urllib.request
    import json

    query = urllib.parse.quote(location)
    url = (
        f"https://nominatim.openstreetmap.org/search"
        f"?q={query}"
        f"&format=json"
        f"&limit=5"
        f"&addressdetails=1"
        f"&countrycodes=us"  # restrict to USA only
    )

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "TeeTimeFinder/1.0 rgsroda@yahoo.com"}
    )
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read())

    if not data:
        raise ValueError(f"Could not find coordinates for: {location}")

    # Print all candidates so you can see what it found
    print("\nLocation candidates found:")
    for i, result in enumerate(data):
        print(f"  [{i}] {result['display_name']} (lat={result['lat']}, lon={result['lon']})")

    # Prefer results where the type is city/town/village
    preferred_types = ["city", "town", "village", "municipality", "administrative"]
    best = None
    for result in data:
        if result.get("type") in preferred_types or result.get("class") == "place":
            best = result
            break

    # Fall back to first result if nothing preferred found
    if not best:
        best = data[0]

    print(f"\nUsing: {best['display_name']}")
    lat = float(best["lat"])
    lng = float(best["lon"])

    # Let user confirm or pick a different one
    confirm = input(f"\nIs this correct? (y/n): ").strip().lower()
    if confirm != "y":
        idx = input(f"Enter the number [0-{len(data)-1}] of the correct location: ").strip()
        if idx.isdigit() and int(idx) < len(data):
            best = data[int(idx)]
            lat = float(best["lat"])
            lng = float(best["lon"])
            print(f"Using: {best['display_name']}")

    return lat, lng, best["display_name"]


# ── DATABASE SETUP ──────────────────────────────────────────
def init_db():
    conn = sqlite3.connect("tee_times.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS tee_times (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            source_platform TEXT,
            booking_url TEXT,
            scraped_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_tee_times(tee_times: list):
    conn = sqlite3.connect("tee_times.db")
    c = conn.cursor()
    c.executemany("""
        INSERT INTO tee_times (
            course_name, address, city, state, price, tee_time,
            date, holes, players, walking, rating, source_platform,
            booking_url, scraped_at
        ) VALUES (
            :course_name, :address, :city, :state, :price, :tee_time,
            :date, :holes, :players, :walking, :rating, :source_platform,
            :booking_url, :scraped_at
        )
    """, tee_times)
    conn.commit()
    conn.close()

# ── SCRAPER ─────────────────────────────────────────────────
async def scrape_supreme_golf(
    location: str,
    lat: float,
    lng: float,
    date: str,
    players: int = 1,
    holes: int = 18,
    state_slug: str = "",
    city_slug: str = ""
):
    # Build URL matching Supreme Golf's actual format
    base = f"https://www.supremegolf.com/explore/united-states/{state_slug}/{city_slug}"
    
    params = (
        f"?date={date}"
        f"&players={players}"
        f"&holes={holes if holes else ''}"
        f"&cart="
        f"&dealsOnly=false"
        f"&foreplayReviewedOnly=false"
        f"&hotDealsSearch=false"
        f"&isPrepaidOnly=false"
        f"&barstoolBestOnly=false"
        f"&marketingPromotionOnly=false"
        f"&networkMembershipOnly=false"
        f"&isRecommendedDate=false"
        f"&price="
        f"&rate="
        f"&time="
    )
    
    url = base + params
    print(f"Scraping: {url}")

    print(f"Scraping: {url}")
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        # Navigate and wait for results to load
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # Give JS a moment to fully render
        await asyncio.sleep(random.uniform(2, 4))

        # Scroll down to trigger lazy loading
        for _ in range(5):
            await page.keyboard.press("End")
            await asyncio.sleep(random.uniform(0.8, 1.5))

        # ── PARSE COURSE CARDS ──────────────────────────────
        # Wait for course tiles to appear
        await page.wait_for_selector('[data-qa-file="CourseTile"]', timeout=15000)
        await asyncio.sleep(random.uniform(2, 4))

        # Scroll to load all results
        for _ in range(5):
            await page.keyboard.press("End")
            await asyncio.sleep(random.uniform(0.8, 1.5))

        # Get all top-level course tile wrappers
        course_cards = await page.query_selector_all('[id="Course-tile-wrapper"]')
        print(f"Found {len(course_cards)} course cards")

        if course_cards:
            html = await course_cards[0].inner_html()
            with open("debug_card.html", "w") as f:
                f.write(html)
            print("First card HTML written to debug_card.html")

        for card in course_cards:
            try:
                # Course name — first w-full flex items-center div contains name
                name_el = await card.query_selector('[data-qa-node="h2"], h2, h3')
                course_name = await name_el.inner_text() if name_el else "Unknown"

                # City and state — Supreme Golf shows them in a p tag like "Westbury, NY"
                address = ""
                city = ""
                state = ""
                try:
                    # Get all grey p tags — first is "X miles away", second is "City, ST"
                    grey_ps = await card.query_selector_all('p.text-grey-3')
                    for p in grey_ps:
                        text = await p.inner_text()
                        # City/state tag contains a comma like "Miami, FL"
                        if "," in text and "miles" not in text.lower():
                            address = text.strip()
                            city = extract_city(address)
                            state = extract_state(address)
                            break
                except:
                    pass

                # Rating — sits next to the StarFilledIcon svg
                rating = 0.0
                try:
                    rating_text = await card.evaluate("""
                        card => {
                            const star = card.querySelector('[data-qa-node="StarFilledIcon"]');
                            if (star && star.nextElementSibling) {
                                return star.nextElementSibling.innerText;
                            }
                            return '0';
                        }
                    """)
                    rating = float(rating_text.strip())
                except:
                    rating = 0.0

                # Price — we can see it uses data-qa-file="CourseTile" and class "text-dark font-black"
                price_els = await card.query_selector_all('p[data-qa-file="CourseTile"]')
                price = 0.0
                for p_el in price_els:
                    text = await p_el.inner_text()
                    if "$" in text:
                        price = parse_price(text)
                        break

                # Tee time slots
                time_slots = await card.query_selector_all('[data-qa-file="CourseTile"][data-qa-node="div"]')
                tee_times_found = []
                for slot in time_slots:
                    text = await slot.inner_text()
                    # Look for time patterns like "8:30 AM"
                    import re
                    times = re.findall(r'\d{1,2}:\d{2}\s?[AP]M', text)
                    tee_times_found.extend(times)

                # Booking URL
                booking_el = await card.query_selector("a")
                booking_url = await booking_el.get_attribute("href") if booking_el else ""
                if booking_url and not booking_url.startswith("http"):
                    booking_url = "https://www.supremegolf.com" + booking_url

                # Save one record per tee time, or one record if no times found
                times_to_save = tee_times_found if tee_times_found else [""]
                for t in times_to_save:
                    results.append({
                        "course_name": course_name.strip(),
                        "address": address,
                        "city": city,
                        "state": state,
                        "price": price,
                        "tee_time": t,
                        "date": date,
                        "holes": holes,
                        "players": players,
                        "walking": 0,
                        "rating": rating,
                        "source_platform": "Supreme Golf",
                        "booking_url": booking_url,
                        "scraped_at": datetime.now().isoformat()
                    })

            except Exception as e:
                print(f"Error parsing card: {e}")
                continue

        await browser.close()

    print(f"Scraped {len(results)} tee times")
    return results


# ── HELPERS ─────────────────────────────────────────────────
def parse_price(text: str) -> float:
    try:
        cleaned = text.replace("$", "").replace(",", "").strip().split()[0]
        return float(cleaned)
    except:
        return 0.0

def extract_city(address: str) -> str:
    """Extract city from Supreme Golf's address format: 'X miles away | City, ST'"""
    try:
        # Supreme Golf shows city/state like "Westbury, NY"
        # address string from the p tags is just "Westbury, NY"
        parts = address.split(",")
        if len(parts) >= 2:
            return parts[0].strip()
        return address.strip()
    except:
        return ""

def extract_state(address: str) -> str:
    """Extract state from Supreme Golf's address format"""
    try:
        parts = address.split(",")
        if len(parts) >= 2:
            # State may have extra text like " NY 11590" — just grab first word
            return parts[1].strip().split()[0]
        return ""
    except:
        return ""


# ── MAIN: run a scrape job ───────────────────────────────────
async def main():
    init_db()

    # Prompt user for location
    location = input("Enter your location (city, state or zip code): ").strip()
    if not location:
        print("No location entered.")
        return

    # Prompt for date range
    days_ahead = input("How many days ahead to search? (default 3): ").strip()
    days_ahead = int(days_ahead) if days_ahead.isdigit() else 3

    # Prompt for players
    players = input("Number of players? (default 1): ").strip()
    players = int(players) if players.isdigit() else 1

    # Prompt for holes
    holes = input("9 or 18 holes? (default 18): ").strip()
    holes = int(holes) if holes in ["9", "18"] else 18

    # Look up coordinates
    print(f"\nLooking up coordinates for '{location}'...")
    try:
        lat, lng, display_name = get_coordinates(location)
        print(f"Found: {display_name}")
        print(f"Coordinates: {lat}, {lng}\n")
    except ValueError as e:
        print(e)
        return

    # Get state and city slugs for Supreme Golf URL
    state_slug, city_slug = format_location_for_supreme(display_name)
    print(f"Supreme Golf path: /explore/united-states/{state_slug}/{city_slug}")

    # Scrape for each date
    base_date = datetime.today()
    total_results = []

    for i in range(days_ahead):
        date_str = (base_date + timedelta(days=i)).strftime("%Y-%m-%d")
        print(f"Scraping tee times for {date_str}...")

        results = await scrape_supreme_golf(
            location=location,
            lat=lat,
            lng=lng,
            date=date_str,
            players=players,
            holes=holes,
            state_slug=state_slug,
            city_slug=city_slug
        )

        if results:
            save_tee_times(results)
            total_results.extend(results)

        await asyncio.sleep(random.uniform(3, 6))

    # Summary
    print(f"\n── Done: {len(total_results)} tee times saved ──")
    conn = sqlite3.connect("tee_times.db")
    c = conn.cursor()
    c.execute("SELECT course_name, tee_time, price, rating FROM tee_times LIMIT 10")
    rows = c.fetchall()
    conn.close()

    print("\n── Sample Results ──────────────────")
    for row in rows:
        print(f"{row[0]} | {row[1]} | ${row[2]} | ⭐{row[3]}")


if __name__ == "__main__":
    asyncio.run(main())