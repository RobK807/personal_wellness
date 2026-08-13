"""
Strava Run Activities Scraper
------------------------------
Scrapes all of your "Run" activities from Strava's "My Activities" page and,
for each one, extracts: Name, Date, Distance, Moving Time, Pace, and the
"Best Efforts" table (Distance / Time / Pace / Heart Rate / Elev).

Results are written incrementally to a CSV file you can open in Excel/Sheets.

HOW IT WORKS
  1. Opens a real Chrome window and takes you to the Strava login page.
     YOU log in yourself in that window — this script never sees, stores,
     or transmits your password.
  2. Once logged in, it reuses your browser's session cookies to call the
     JSON endpoint that powers the "My Activities" table, collecting every
     Run activity (id, name, date, distance, moving time).
  3. For each activity it opens /activities/<id>/best-efforts in the same
     browser, waits for the table to render (it's loaded client-side), and
     scrapes it.

REQUIREMENTS
    pip install selenium requests webdriver-manager

USAGE
    python strava_runs_scraper.py

NOTE: This only accesses YOUR OWN account data via your own logged-in
session — it doesn't bypass login, CAPTCHA, or any access controls.
"""

import csv
import time
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

BASE = "https://www.strava.com"
OUTPUT_CSV = "strava_runs.csv"
PER_PAGE = 20
MAX_WAIT_RETRIES = 20   # x 0.3s = up to 6s per activity waiting for table to render


def start_browser():
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service)


def wait_for_login(driver):
    driver.get(f"{BASE}/login")
    print("A Chrome window has opened.")
    print("Please log in to Strava in that window.")
    input("Once logged in and on your Dashboard, press Enter here to continue...")


def get_session_from_driver(driver):
    """Copy the authenticated cookies from Selenium into a requests.Session."""
    session = requests.Session()
    for cookie in driver.get_cookies():
        session.cookies.set(cookie["name"], cookie["value"])
    session.headers.update({
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "User-Agent": "Mozilla/5.0",
    })
    return session


def fetch_all_runs(session):
    """Page through the training_activities JSON endpoint, filtered to Run."""
    runs = []
    page = 1
    while True:
        params = {
            "keywords": "", "sport_type": "Run", "tags": "", "commute": "",
            "private_activities": "", "trainer": "", "gear": "",
            "new_activity_only": "false", "page": page, "per_page": PER_PAGE,
            "order": "",
        }
        resp = session.get(f"{BASE}/athlete/training_activities", params=params)
        resp.raise_for_status()
        models = resp.json().get("models", [])
        if not models:
            break
        for m in models:
            runs.append({
                "id": m.get("id_str") or str(m.get("id")),
                "name": m.get("name"),
                "date": m.get("start_date"),
                "distance": f'{m.get("distance")} {m.get("short_unit")}',
                "moving_time": m.get("moving_time"),
            })
        if len(models) < PER_PAGE:
            break
        page += 1
        time.sleep(0.5)  # be gentle on Strava's servers
    return runs


def compute_pace(distance_str, moving_time_str):
    """Fallback min:sec/km pace calc, in case you want to cross-check the page value."""
    try:
        km = float(distance_str.split()[0])
        parts = [int(p) for p in moving_time_str.split(":")]
        secs = parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0] * 3600 + parts[1] * 60 + parts[2]
        if km <= 0:
            return ""
        pace_secs = secs / km
        m, s = divmod(int(round(pace_secs)), 60)
        return f"{m}:{s:02d}/km"
    except Exception:
        return ""


def scrape_best_efforts(driver, activity_id):
    """Load the Best Efforts tab for one activity and scrape the table."""
    driver.get(f"{BASE}/activities/{activity_id}/best-efforts")
    for _ in range(MAX_WAIT_RETRIES):
        tables = driver.find_elements(By.TAG_NAME, "table")
        if tables:
            rows = tables[0].find_elements(By.TAG_NAME, "tr")
            return [
                [cell.text.strip() for cell in row.find_elements(By.XPATH, "./td|./th")]
                for row in rows
            ]
        time.sleep(0.3)
    return []  # no best-efforts table for this activity


def main():
    driver = start_browser()
    wait_for_login(driver)

    session = get_session_from_driver(driver)
    print("Fetching list of Run activities...")
    runs = fetch_all_runs(session)
    print(f"Found {len(runs)} run activities.")

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Activity ID", "Name", "Date", "Distance", "Moving Time", "Pace",
            "Best Effort Distance", "Best Effort Time", "Best Effort Pace",
            "Best Effort Heart Rate", "Best Effort Elev",
        ])

        for i, run in enumerate(runs, 1):
            print(f"[{i}/{len(runs)}] {run['name']} ({run['date']})")
            pace = compute_pace(run["distance"], run["moving_time"])
            best_efforts = scrape_best_efforts(driver, run["id"])

            if len(best_efforts) <= 1:
                writer.writerow([run["id"], run["name"], run["date"], run["distance"],
                                  run["moving_time"], pace, "", "", "", "", ""])
            else:
                for row in best_efforts[1:]:  # skip header row
                    dist, t, p, hr, elev = (row + ["", "", "", "", ""])[:5]
                    writer.writerow([run["id"], run["name"], run["date"], run["distance"],
                                      run["moving_time"], pace, dist, t, p, hr, elev])
            f.flush()
            time.sleep(0.5)  # be gentle on Strava's servers

    print(f"Done. Results saved to {OUTPUT_CSV}")
    driver.quit()


if __name__ == "__main__":
    main()