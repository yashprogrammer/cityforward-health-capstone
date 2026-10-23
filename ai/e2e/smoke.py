"""End-to-end smoke test of the SMART app in a real browser: EHR launch -> PKCE -> dashboard -> every tab.

Needs the stack running behind one origin (docker compose up, or the three processes in ai/scripts/devgateway.py) and: pip install playwright.
    python ai/e2e/smoke.py ./shots [chromium-path]
"""
import sys
from playwright.sync_api import sync_playwright
S = sys.argv[1]
BASE = "http://localhost:8001"
with sync_playwright() as p:
    b = p.chromium.launch(**({"executable_path": sys.argv[2]} if len(sys.argv) > 2 else {}), args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1360, "height": 900})
    logs = []
    pg.on("console", lambda m: logs.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
    pg.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
    # MediTrack: find the SMART link and launch it for Hale, acting as Dr. Raman
    links = pg.request.get(f"{BASE}/int/admin/extlinks").json()
    smart = next(l for l in links if l["DISP_MD"] == "SMART")
    ctx = pg.context
    ctx.add_cookies([{"name": "mt_user", "value": "attending.raman", "url": BASE}])
    pg.goto(f"{BASE}/smart/ehr-launch?lnk_id={smart['LNK_ID']}&ip_no=IP2610-0142")
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
