import os
import json
import sqlite3
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import anthropic

# Load .env
load_dotenv()

app = FastAPI()

# Allow frontend to talk to backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ── MODELS ──────────────────────────────────────────────────
class ChatRequest(BaseModel):
    messages: list
    session_state: dict = {}

# ── DATABASE QUERY ───────────────────────────────────────────
def query_tee_times(filters: dict) -> list:
    conn = sqlite3.connect("tee_times.db")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    query = "SELECT * FROM tee_times WHERE 1=1"
    params = []

    if filters.get("date"):
        query += " AND date = ?"
        params.append(filters["date"])

    if filters.get("max_price"):
        query += " AND price <= ? AND price > 0"
        params.append(filters["max_price"])

    if filters.get("min_price"):
        query += " AND price >= ?"
        params.append(filters["min_price"])

    if filters.get("players"):
        query += " AND players <= ?"
        params.append(filters["players"])

    if filters.get("holes"):
        query += " AND holes = ?"
        params.append(filters["holes"])

    if filters.get("city"):
        query += " AND (city LIKE ? OR course_name LIKE ?)"
        params.append(f"%{filters['city']}%")
        params.append(f"%{filters['city']}%")

    if filters.get("tee_time_after"):
        query += " AND tee_time >= ?"
        params.append(filters["tee_time_after"])

    if filters.get("tee_time_before"):
        query += " AND tee_time <= ?"
        params.append(filters["tee_time_before"])

    query += " ORDER BY rating DESC, price ASC LIMIT 20"

    c.execute(query, params)
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    return rows


# ── CHECK IF WE HAVE DATA FOR THIS SEARCH ────────────────────
def has_data_for(city: str, date: str) -> bool:
    conn = sqlite3.connect("tee_times.db")
    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*) FROM tee_times WHERE city LIKE ? AND date = ?",
        (f"%{city}%", date)
    )
    count = c.fetchone()[0]
    conn.close()
    return count > 0


# ── SYSTEM PROMPT ────────────────────────────────────────────
SYSTEM_PROMPT = """You are a friendly golf tee time assistant. Your job is to help users find and book golf tee times.

You have two modes:

1. GATHERING MODE — collect the user's preferences through natural conversation:
   - Location (city, state)
   - Date or date range
   - Number of players
   - 9 or 18 holes
   - Budget / price range
   - Preferred tee time (morning, afternoon, specific time)
   - Walking or cart

   Ask for missing info naturally. Once you have location and date at minimum, you can search.

2. RESULTS MODE — when you have enough info to search, respond with a JSON block like this:

<search>
{
  "ready": true,
  "location": "Miami, FL",
  "city": "Miami",
  "state": "florida",
  "city_slug": "miami",
  "state_slug": "florida",
  "date": "2026-04-05",
  "players": 2,
  "holes": 18,
  "max_price": 80,
  "min_price": 0,
  "tee_time_after": "08:00 AM",
  "tee_time_before": "12:00 PM",
  "walking": false
}
</search>

Then add a friendly message like "Great, searching for tee times in Miami on April 5th..."

Always use YYYY-MM-DD format for dates. If the user says "this Saturday" calculate the actual date.
Today's date is """ + datetime.now().strftime("%Y-%m-%d") + """.

3. RANKING MODE — when given a list of tee times as JSON, rank them from best to worst based on the user's preferences. 
   Format each result clearly showing:
   - Rank number
   - Course name
   - Tee time
   - Price
   - Rating
   - Why you ranked it here
   
   End with a brief recommendation of the top pick."""


# ── MAIN CHAT ENDPOINT ───────────────────────────────────────
@app.post("/chat")
async def chat(request: ChatRequest):
    messages = request.messages
    session_state = request.session_state

    # Call Claude to get response
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=messages
    )

    assistant_message = response.content[0].text

    # Check if Claude wants to search
    if "<search>" in assistant_message:
        try:
            # Extract the JSON from the <search> block
            search_json = assistant_message.split("<search>")[1].split("</search>")[0].strip()
            search_params = json.loads(search_json)

            if search_params.get("ready"):
                city = search_params.get("city", "")
                date = search_params.get("date", "")
                state_slug = search_params.get("state_slug", "")
                city_slug = search_params.get("city_slug", "")

                # Check if we already have data, if not scrape
                if not has_data_for(city, date):
                    # Import and run scraper
                    from scraper import scrape_supreme_golf, save_tee_times, init_db
                    init_db()

                    print(f"Scraping {city} for {date}...")
                    tee_times = await scrape_supreme_golf(
                        location=search_params.get("location", city),
                        lat=0,
                        lng=0,
                        date=date,
                        players=search_params.get("players", 1),
                        holes=search_params.get("holes", 18),
                        state_slug=state_slug,
                        city_slug=city_slug
                    )
                    if tee_times:
                        save_tee_times(tee_times)

                # Query the database with filters
                results = query_tee_times({
                    "city": city,
                    "date": date,
                    "players": search_params.get("players"),
                    "holes": search_params.get("holes"),
                    "max_price": search_params.get("max_price"),
                    "min_price": search_params.get("min_price"),
                    "tee_time_after": search_params.get("tee_time_after"),
                    "tee_time_before": search_params.get("tee_time_before"),
                })

                if results:
                    # Send results back to Claude for ranking
                    ranking_messages = messages + [
                        {"role": "assistant", "content": assistant_message},
                        {"role": "user", "content": f"Here are the available tee times I found. Please rank them best to worst based on the user's preferences:\n\n{json.dumps(results, indent=2)}"}
                    ]

                    ranking_response = client.messages.create(
                        model="claude-sonnet-4-20250514",
                        max_tokens=2000,
                        system=SYSTEM_PROMPT,
                        messages=ranking_messages
                    )

                    return {
                        "message": ranking_response.content[0].text,
                        "results": results,
                        "search_params": search_params,
                        "session_state": session_state
                    }
                else:
                    return {
                        "message": f"I searched for tee times in {city} on {date} but didn't find any results matching your criteria. Would you like to try different dates or adjust your budget?",
                        "results": [],
                        "session_state": session_state
                    }

        except Exception as e:
            print(f"Search error: {e}")

    return {
        "message": assistant_message,
        "results": [],
        "session_state": session_state
    }


# ── SERVE FRONTEND ───────────────────────────────────────────
@app.get("/")
def serve_frontend():
    return FileResponse("index.html")


# ── RUN ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend:app", host="0.0.0.0", port=8000, reload=True)