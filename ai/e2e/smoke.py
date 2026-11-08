"""End-to-end smoke test of the SMART app in a real browser: EHR launch -> PKCE -> dashboard -> every tab.

Needs the stack running behind one origin (docker compose up, or the three processes in ai/scripts/devgateway.py) and: pip install playwright.
    python ai/e2e/smoke.py ./shots [chromium-path]
"""
import sys
import os
import getpass
from playwright.sync_api import sync_playwright
S = sys.argv[1]
BASE = os.environ.get("CITYFORWARD_BASE_URL", "http://localhost:8001").rstrip("/")
with sync_playwright() as p:
    b = p.chromium.launch(**({"executable_path": sys.argv[2]} if len(sys.argv) > 2 else {}), args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1360, "height": 900})
    logs = []
    pg.on("console", lambda m: logs.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
    pg.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
    # Sign in as a provisioned local staff member, then launch from the chart.
    username = os.getenv('CITYFORWARD_STAFF_ID', 'attending.raman')
    password = os.getenv('CITYFORWARD_STAFF_PASSWORD') or getpass.getpass('Local staff password: ')
    pg.goto(BASE)
    pg.locator('#staff-id').fill(username)
    pg.locator('#staff-password').fill(password)
    pg.get_by_role('button', name='Sign in').click()
    pg.get_by_text('IPD Census - Current In-Patients').wait_for(timeout=10000)
    chart = pg.request.get(f'{BASE}/int/ip/IP2610-0142')
    if not chart.ok:
        raise RuntimeError(f'Unable to open patient chart: HTTP {chart.status}')
    smart = next(link for link in chart.json()['links'] if link['DISP_MD'] == 'SMART')
    pg.goto(f"{BASE}{smart['HREF']}")
    pg.wait_for_selector(".banner h1", timeout=20000)
    pg.wait_for_selector(".flag")
    print("URL after launch:", pg.url)
    pg.screenshot(path=f"{S}/dash_overview.png")
    pg.click("text=Summarize this stay")
    pg.wait_for_selector(".headline", timeout=20000)
    pg.click(".chip >> nth=0")
    pg.wait_for_selector(".why .source")
    pg.screenshot(path=f"{S}/dash_summary_why.png")
    pg.click(".flag >> nth=0")
    pg.wait_for_selector(".why .rule")
    pg.screenshot(path=f"{S}/dash_flag_why.png")
    pg.click("role=tab[name='Timeline']")
    pg.wait_for_selector(".timeline")
    pg.screenshot(path=f"{S}/dash_timeline.png")
    pg.click("role=tab[name='Ask the chart']")
    pg.fill("#q", "How often is ticagrelor given?")
    pg.click("text=Ask >> nth=-1")
    pg.wait_for_selector(".qa")
    pg.screenshot(path=f"{S}/dash_ask.png")
    pg.click("role=tab[name='Prior authorization']")
    pg.wait_for_timeout(1500)
    pg.screenshot(path=f"{S}/dash_pa.png")
    pg.click("role=tab[name='Coding']")
    pg.wait_for_timeout(500)
    pg.click("text=Suggest codes for this stay")
    pg.wait_for_selector("table", timeout=20000)
    pg.screenshot(path=f"{S}/dash_coding.png", full_page=True)
    print("console:", logs[:10])
    b.close()
