"""Post-deploy smoke test through the public gateway. Exit 0 only when every check passes; run it twice in a row before trusting a deploy.

Positive paths (health, SMART sign-in, a grounded model answer) and the negative paths that matter for a hospital (no token, wrong role).
Synthetic patient only. One model call (about 0.0003 USD) when --model-call is given.

    python deploy/azure-demo/smoke.py --base-url http://<gateway-ip> [--expect-model foundry] [--model-call]
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from alert_drill import smart_token      # the same SMART authorization-code + PKCE flow the dashboard uses

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)
    return ok


def call(url: str, method: str = "GET", token: str | None = None, body: dict | None = None, timeout: int = 60):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, method=method, headers=headers, data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw and raw[:1] in b"{[" else raw.decode(errors="replace")[:200])
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:200]
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        return 0, str(e)[:120]


def wait_ready(url: str, seconds: int) -> tuple[int, object]:
    """Poll instead of sleeping: a fresh deploy answers 502/503 and database auth errors for a while."""
    end = time.time() + seconds
    while True:
        status, body = call(url)
        if status == 200 or time.time() > end:
            return status, body
        time.sleep(5)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", required=True)
    p.add_argument("--expect-model", default="", help="prefix the AI service must report, e.g. foundry")
    p.add_argument("--model-call", action="store_true", help="make one real model call (Ask the chart)")
    p.add_argument("--wait", type=int, default=300, help="seconds to wait for the services to answer")
    args = p.parse_args()
    base = args.base_url.rstrip("/")

    s, _ = wait_ready(f"{base}/gateway-healthz", args.wait)
    check("gateway answers", s == 200, f"HTTP {s}")
    s, b = wait_ready(f"{base}/ai/healthz", args.wait)
    check("AI service healthy (database reachable)", s == 200 and isinstance(b, dict) and b.get("ok") is True, f"HTTP {s}")
    if args.expect_model:
        model = b.get("model", "") if isinstance(b, dict) else ""
        check("AI service uses the expected model provider", model.startswith(args.expect_model), model)
    s, _ = wait_ready(f"{base}/int/healthz", args.wait)
    check("MediTrack healthy", s == 200, f"HTTP {s}")

    s, _ = call(f"{base}/ai/api/patients/1/summary", "POST")
    check("no token is refused (401)", s == 401, f"HTTP {s}")
    try:
        admin = smart_token(base, "it.admin")
        s, _ = call(f"{base}/ai/api/patients/1/summary", "POST", admin)
        check("IT admin cannot read clinical content (403)", s == 403, f"HTTP {s}")
        clinician = smart_token(base, "attending.raman")
        s, b = call(f"{base}/ai/api/patients/1/dashboard", token=clinician)
        check("attending opens the dashboard", s == 200, f"HTTP {s}")
        if args.model_call:
            s, b = call(f"{base}/ai/api/patients/1/ask", "POST", clinician, {"question": "How often is ticagrelor given?"})
            ok = s == 200 and isinstance(b, dict) and b.get("mode") == "llm" and not b.get("problems")
            check("grounded model answer", ok, f"HTTP {s}, mode={b.get('mode') if isinstance(b, dict) else '?'}, note={(b.get('note') if isinstance(b, dict) else '') or ''}"[:140])
    except SystemExit as e:
        check("SMART sign-in", False, str(e))
    except (urllib.error.URLError, KeyError, StopIteration, ValueError) as e:
        check("SMART sign-in", False, type(e).__name__)

    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
