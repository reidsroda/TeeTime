import asyncio
import json
import sqlite3
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
import time
import random
import urllib.request
import urllib.parse

def get_coordinates(location: str) -> tuple:
    """Convert a city/zip to lat/lng using free Nominatim API"""
    query = urllib.parse.quote(location)
    url = f"https://nominatim.openstreetmap.org/search?q={query}&format=json&limit=1"
    
    req = urllib.request.Request(url, headers={"User-Agent": "TeeTimeFinder/1.0"})
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read())
    
    if not data:
        raise ValueError(f"Could not find coordinates for: {location}")
    
    return float(data[0]["lat"]), float(data[0]["lon"]), data[0]["display_name"]


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
    date: str,           # "YYYY-MM-DD"
    players: int = 1,
    holes: int = 18
):
    url = (
        f"https://www.supremegolf.com/search"
        f"?address={location}"
        f"&lat={lat}&lng={lng}"
        f"&date={date}"
        f"&holes={holes}"
        f"&players={players}"
    )

    print(f"Scraping: {url}")
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
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
        # Supreme Golf renders course cards — adjust selectors
        # if they update their HTML structure
        course_cards = await page.query_selector_all(
            "[class*='course-card'], [class*='CourseCard'], [data-testid*='course']"
        )

        print(f"Found {len(course_cards)} course cards")

        for card in course_cards:
            try:
                # Course name
                name_el = await card.query_selector(
                    "[class*='course-name'], [class*='CourseName'], h2, h3"
                )
                course_name = await name_el.inner_text() if name_el else "Unknown"

                # Address
                addr_el = await card.query_selector(
                    "[class*='address'], [class*='location'], [class*='Address']"
                )
                address = await addr_el.inner_text() if addr_el else ""

                # Rating
                rating_el = await card.query_selector(
                    "[class*='rating'], [class*='Rating'], [class*='stars']"
                )
                rating_text = await rating_el.inner_text() if rating_el else "0"
                try:
                    rating = float(rating_text.strip().split()[0])
                except:
                    rating = 0.0

                # Tee time slots within this card
                time_slots = await card.query_selector_all(
                    "[class*='tee-time'], [class*='TeeTime'], [class*='time-slot']"
                )

                if not time_slots:
                    # Some cards show price without individual time slots
                    price_el = await card.query_selector(
                        "[class*='price'], [class*='Price'], [class*='rate']"
                    )
                    price_text = await price_el.inner_text() if price_el else "0"
                    price = parse_price(price_text)

                    booking_el = await card.query_selector("a[href*='book'], a[href*='tee']")
                    booking_url = await booking_el.get_attribute("href") if booking_el else ""

                    results.append({
                        "course_name": course_name.strip(),
                        "address": address.strip(),
                        "city": extract_city(address),
                        "state": extract_state(address),
                        "price": price,
                        "tee_time": "",
                        "date": date,
                        "holes": holes,
                        "players": players,
                        "walking": 0,
                        "rating": rating,
                        "source_platform": "Supreme Golf",
                        "booking_url": booking_url,
                        "scraped_at": datetime.now().isoformat()
                    })
                else:
                    for slot in time_slots:
                        time_text_el = await slot.query_selector(
                            "[class*='time'], span, div"
                        )
                        time_text = await time_text_el.inner_text() if time_text_el else ""

                        price_el = await slot.query_selector(
                            "[class*='price'], [class*='rate']"
                        )
                        price_text = await price_el.inner_text() if price_el else "0"
                        price = parse_price(price_text)

                        booking_el = await slot.query_selector("a")
                        booking_url = await booking_el.get_attribute("href") if booking_el else ""

                        results.append({
                            "course_name": course_name.strip(),
                            "address": address.strip(),
                            "city": extract_city(address),
                            "state": extract_state(address),
                            "price": price,
                            "tee_time": time_text.strip(),
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
    try:
        parts = address.split(",")
        return parts[-2].strip() if len(parts) >= 2 else ""
    except:
        return ""

def extract_state(address: str) -> str:
    try:
        parts = address.split(",")
        return parts[-1].strip().split()[0] if parts else ""
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
            holes=holes
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