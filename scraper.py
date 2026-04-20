#!/usr/bin/env python3
"""
BE Tools — Performology Data Scraper
Runs via GitHub Actions daily at 6am MDT.
Reads credentials from environment variables.
Writes data.json to the current directory (repo root).
"""

import asyncio
import json
import re
import os
from datetime import datetime
from playwright.async_api import async_playwright

# ── Config ────────────────────────────────────────────────────────────────────
PERFORMOLOGY_URL  = "https://app.performology.com"
LOGIN_EMAIL       = os.environ.get("PERFORMOLOGY_EMAIL", "bericson@allstate.com")
LOGIN_PASSWORD    = os.environ.get("PERFORMOLOGY_PASSWORD", "G00dHandsSpring!")
OUTPUT_JSON       = "data.json"   # relative to repo root (CWD in Actions)

# Active staff only (Joseph Garcia and Kara Ott removed)
STAFF = [
    {"name": "Amado Plata",            "id": "18216"},
    {"name": "Brendon Ericson",        "id": "18211"},
    {"name": "Jackie Schmidt",         "id": "18215"},
    {"name": "Jacqulyn Alcala-Casica", "id": "26601"},
    {"name": "Noel Elhardt",           "id": "20274"},
    {"name": "Ross MacDonald",         "id": "22976"},
    {"name": "Sara Ross",              "id": "26599"},
    {"name": "Sujan Sharma",           "id": "26037"},
    {"name": "Tiffany Keutz",          "id": "35884"},
]

# ── Helpers ───────────────────────────────────────────────────────────────────
def parse_number(text):
    """Extract numeric value, handling k/K suffix."""
    if not text:
        return 0
    text = str(text).strip()
    k_match = re.match(r'^\$?([\d,]+\.?\d*)[kK]$', text)
    if k_match:
        return float(k_match.group(1).replace(',', '')) * 1000
    clean = re.sub(r'[^0-9.]', '', text.replace(',', ''))
    try:
        return float(clean)
    except:
        return 0

def parse_goal_section(text, section_keyword, metric_type="Items"):
    """
    Parse a named goal section from Goals page text.
    Stops at the 'Needed' line to avoid bleeding into the next section.
    """
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    goal_val = 0
    achievement_val = 0

    for i, line in enumerate(lines):
        if section_keyword.lower() in line.lower():
            chunk = []
            for cl in lines[i:i+25]:
                chunk.append(cl)
                if 'Needed' in cl and len(chunk) > 3:
                    break

            goal_label = f"Goal ({metric_type})"
            for j, cl in enumerate(chunk):
                if goal_label in cl and j > 0:
                    goal_val = parse_number(chunk[j-1])
                if 'Goal Achievement' in cl and j > 0:
                    achievement_val = parse_number(chunk[j-1])
            break

    if metric_type == 'Premium':
        return achievement_val, goal_val
    return int(achievement_val), int(goal_val)

async def wait_for_user_change(page, user_id, element_id="totalPremium"):
    """Set user filter and wait for AJAX to update the element."""
    try:
        before = await page.evaluate(f"document.getElementById('{element_id}')?.innerText || ''")
    except:
        before = None

    await page.evaluate(f"""
        var sel = document.getElementById('selectedUserId');
        if (sel) {{
            sel.value = '{user_id}';
            sel.dispatchEvent(new Event('change', {{bubbles: true}}));
        }}
    """)

    if before:
        before_json = json.dumps(before)
        try:
            await page.wait_for_function(
                f"document.getElementById('{element_id}')?.innerText !== {before_json}",
                timeout=8000
            )
        except:
            pass
    await page.wait_for_timeout(2000)

# ── Main scraper ──────────────────────────────────────────────────────────────
async def scrape():
    staff_data = {}
    team_goals = {"vc_current": 0, "vc_goal": 126, "premium_current": 0, "premium_goal": 150000}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context()
        page = await context.new_page()

        # ── Login ──────────────────────────────────────────────────────────────
        print("Logging in to Performology...")
        await page.goto(f"{PERFORMOLOGY_URL}/", wait_until="networkidle")
        await page.wait_for_timeout(2000)
        try:
            # Performology uses id="Email" (type=text) and id="Password"
            await page.fill('#Email', LOGIN_EMAIL)
            await page.fill('#Password', LOGIN_PASSWORD)
            await page.click('#loginBtn')
            await page.wait_for_url("**/Reports/**", timeout=20000)
            print("  Login successful.")
        except Exception as e:
            print(f"  Login error: {e}")
            # Try fallback selectors
            try:
                await page.fill('input[placeholder="Email"]', LOGIN_EMAIL)
                await page.fill('input[placeholder="Password"]', LOGIN_PASSWORD)
                await page.click('button:has-text("Login")')
                await page.wait_for_url("**/Reports/**", timeout=20000)
                print("  Login successful (fallback).")
            except Exception as e2:
                print(f"  Login fallback error: {e2}")

        # ── PASS 1: Sales data for each staff member ───────────────────────────
        print("\nPass 1: Sales data...")
        await page.goto(f"{PERFORMOLOGY_URL}/Reports/SalesNew", wait_until="networkidle")
        await page.wait_for_timeout(2000)

        for staff in STAFF:
            sid = staff["id"]
            name = staff["name"]
            print(f"  {name}...")
            try:
                await wait_for_user_change(page, sid)
                body_text = await page.inner_text("body")

                # Extract premium
                prem_el = await page.evaluate("document.getElementById('totalPremium')?.innerText || '0'")
                premium = parse_number(prem_el)

                # Extract items from body text
                items = 0
                items_match = re.search(r'Total Items[^\d]*(\d+)', body_text)
                if items_match:
                    items = int(items_match.group(1))
                else:
                    items_el = await page.evaluate("document.getElementById('totalItems')?.innerText || document.getElementById('totalPolicies')?.innerText || '0'")
                    items = int(parse_number(items_el))

                staff_data[sid] = {
                    "name": name,
                    "premium_mtd": premium,
                    "items_mtd": items,
                    "vc_mtd": 0,
                    "vc_goal": 20,
                    "quotes_mtd": 0,
                    "quotes_goal": 80,
                    "premium_goal": 0,
                }
                print(f"    Premium: ${premium:,.0f} | Items: {items}")
            except Exception as e:
                print(f"    Error: {e}")
                staff_data[sid] = {
                    "name": name, "premium_mtd": 0, "items_mtd": 0,
                    "vc_mtd": 0, "vc_goal": 20, "quotes_mtd": 0,
                    "quotes_goal": 80, "premium_goal": 0,
                }

        # ── PASS 2: Individual Goals (VC + Quotes) ─────────────────────────────
        print("\nPass 2: Individual goals (VC + Quotes)...")
        await page.goto(f"{PERFORMOLOGY_URL}/Reports/GoalsNew", wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # Dismiss any modal
        try:
            close_btn = page.locator('button.close, button:has-text("×"), button:has-text("Close"), [data-dismiss="modal"]').first
            if await close_btn.is_visible():
                await close_btn.click()
                await page.wait_for_timeout(500)
        except:
            pass

        for staff in STAFF:
            sid = staff["id"]
            name = staff["name"]
            print(f"  {name}...")
            try:
                await wait_for_user_change(page, sid, element_id="totalPremium")
                goals_text = await page.inner_text("body")

                vc_current, vc_goal = parse_goal_section(goals_text, "VC Goal for", "Items")
                if vc_goal == 0:
                    vc_goal = 20
                quotes_current, quotes_goal = parse_goal_section(goals_text, "Quote Minimum", "Items")
                if quotes_goal == 0:
                    quotes_goal = 80
                prem_current, prem_goal = parse_goal_section(goals_text, "P&C Premium Goal", "Premium")

                staff_data[sid]["vc_mtd"]       = vc_current
                staff_data[sid]["vc_goal"]       = vc_goal
                staff_data[sid]["quotes_mtd"]    = quotes_current
                staff_data[sid]["quotes_goal"]   = quotes_goal
                staff_data[sid]["premium_goal"]  = prem_goal
                print(f"    VC: {vc_current}/{vc_goal} | Quotes: {quotes_current}/{quotes_goal}")
            except Exception as e:
                print(f"    Error: {e}")

        # ── PASS 3: Team-level Goals (agency view, no user filter) ─────────────
        print("\nPass 3: Team goals...")
        try:
            await page.goto(f"{PERFORMOLOGY_URL}/Reports/GoalsNew", wait_until="networkidle")
            await page.wait_for_timeout(3000)
            try:
                close_btn = page.locator('button.close, button:has-text("×"), button:has-text("Close"), [data-dismiss="modal"]').first
                if await close_btn.is_visible():
                    await close_btn.click()
                    await page.wait_for_timeout(500)
            except:
                pass
            goals_text = await page.inner_text("body")
            vc_current, vc_goal = parse_goal_section(goals_text, "VC Qualifying", "Items")
            if vc_goal == 0:
                vc_goal = 126
            prem_current, prem_goal = parse_goal_section(goals_text, "P&C Premium Goal", "Premium")
            if prem_goal == 0:
                prem_goal = 150000
            team_goals = {
                "vc_current":      vc_current,
                "vc_goal":         vc_goal,
                "premium_current": prem_current,
                "premium_goal":    prem_goal,
            }
            print(f"    Team VC: {vc_current}/{vc_goal} | Team Premium: ${prem_current:,.0f}/${prem_goal:,.0f}")
        except Exception as e:
            print(f"    Team goals error: {e}")

        await browser.close()

    # ── Assemble and write JSON ────────────────────────────────────────────────
    result = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "staff": [staff_data[s["id"]] for s in STAFF],
        "team_goals": team_goals,
    }
    with open(OUTPUT_JSON, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\n  Data written to {OUTPUT_JSON}")
    for sd in result["staff"]:
        print(f"  {sd['name']}: Premium ${sd['premium_mtd']:,.0f} | Items {sd['items_mtd']} | VC {sd['vc_mtd']}/{sd['vc_goal']} | Quotes {sd['quotes_mtd']}")
    print(f"  Team VC: {result['team_goals']['vc_current']}/{result['team_goals']['vc_goal']}")
    print(f"\n[{datetime.now()}] Scrape complete.")
    return result

if __name__ == "__main__":
    asyncio.run(scrape())
