"""Outside-in checker for the capstone assignment. No credentials needed.

    python course/check_submission.py --url https://host --eval metrics.json --recording URL_OR_PATH --summary summary.md [--json]

Exit code 0 when every check passes. The functions take an httpx.Client so they can be tested without a network.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx

SAFETY_GATES = ("ask.directive_or_risk_violations", "ask.identifier_leaks", "summary.forbidden_hits", "coding.forbidden_model_codes")


def check(name: str, ok: bool, detail: str = "") -> dict:
    return {"check": name, "ok": bool(ok), "detail": detail}


def remote_checks(client: httpx.Client, base: str) -> list[dict]:
    base = base.rstrip("/")
    out = []
    try:
        r = client.get(f"{base}/ai/healthz")
        out.append(check("copilot health", r.status_code == 200 and r.json().get("ok") is True, f"HTTP {r.status_code}"))
        h = r.headers
        r2 = client.get(f"{base}/ai/")
        hh = r2.headers
        out.append(check("security headers on the app", "default-src" in hh.get("content-security-policy", "") and hh.get("x-content-type-options") == "nosniff"
                         and hh.get("referrer-policy") == "no-referrer", "CSP, nosniff, Referrer-Policy"))
        if base.startswith("https://"):
            out.append(check("HSTS on https", "max-age" in hh.get("strict-transport-security", h.get("strict-transport-security", "")), "Strict-Transport-Security"))
    except (httpx.HTTPError, ValueError) as e:
        out.append(check("copilot health", False, f"{type(e).__name__}: {e}"))
        return out
    try:
        r = client.get(f"{base}/fhir/metadata")
        out.append(check("FHIR capability statement", r.status_code == 200 and r.json().get("resourceType") == "CapabilityStatement", f"HTTP {r.status_code}"))
        cfg = client.get(f"{base}/fhir/.well-known/smart-configuration").json()
        out.append(check("SMART configuration advertises PKCE S256", "S256" in cfg.get("code_challenge_methods_supported", []) and cfg.get("token_endpoint") and cfg.get("authorization_endpoint")))
    except (httpx.HTTPError, ValueError) as e:
        out.append(check("FHIR / SMART endpoints", False, f"{type(e).__name__}: {e}"))
    for path in ("/ai/api/patients/1/ask", "/ai/api/patients/1/summary", "/ai/api/audit"):
        r = client.request("POST" if "patients" in path else "GET", f"{base}{path}", json={"question": "x"} if "ask" in path else None)
        out.append(check(f"unauthenticated {path} is refused", r.status_code == 401, f"HTTP {r.status_code}"))
    r = client.get(f"{base}/ai/metrics")
    out.append(check("metrics are not exposed through the ingress", r.status_code in (404, 405) or "copilot_" not in r.text, f"HTTP {r.status_code}"))
    body = json.dumps({"specversion": "1.0", "id": "check-1", "type": "org.cityforward.meditrack.document.added.v1", "data": {"patientId": "0"}})
    r = client.post(f"{base}/ai/webhooks/meditrack", content=body, headers={"x-meditrack-signature": f"t={int(time.time())},v1={'0' * 64}"})
    out.append(check("unsigned webhook is rejected", r.status_code == 401, f"HTTP {r.status_code}"))
    return out


def eval_checks(path: Path) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        return [check("evaluation results readable", False, str(e))]
    m = data.get("metrics", {})
    meta = data.get("meta", {})
    out = [check("evaluation records the model used", bool(meta.get("model")), str(meta.get("model")))]
    for g in SAFETY_GATES:
        v = m.get(g)
        out.append(check(f"safety gate {g} == 0", v == 0, f"value {v}"))
    out.append(check("golden set was actually run (>= 30 cases)", meta.get("cases", 0) >= 30, f"{meta.get('cases')} cases"))
    return out


def artifact_checks(recording: str | None, summary: str | None) -> list[dict]:
    out = []
    if recording:
        ok = recording.startswith(("http://", "https://")) or (Path(recording).exists() and Path(recording).stat().st_size > 100_000)
        out.append(check("executive readout recording provided", ok, recording))
    else:
        out.append(check("executive readout recording provided", False, "--recording not given"))
    if summary and Path(summary).exists():
        text = Path(summary).read_text().lower()
        out.append(check("summary mentions what was not proven", any(k in text for k in ("not yet", "not proven", "not measured", "gap", "limitation")), summary))
        out.append(check("summary avoids unearned claims", not any(k in text for k in ("hipaa compliant", "fda approved", "fda cleared", "clinically validated", "production-ready")), "no 'HIPAA compliant' / 'FDA approved' / ..."))
    else:
        out.append(check("summary provided", False, "--summary missing or not found"))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--eval", dest="eval_path")
    ap.add_argument("--recording")
    ap.add_argument("--summary")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    with httpx.Client(timeout=15.0, follow_redirects=False) as client:
        results = remote_checks(client, a.url)
    results += eval_checks(Path(a.eval_path)) if a.eval_path else [check("evaluation results provided", False, "--eval not given")]
    results += artifact_checks(a.recording, a.summary)
    if a.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['check']}" + (f"  ({r['detail']})" if r["detail"] else ""))
        print(f"\n{sum(r['ok'] for r in results)}/{len(results)} checks passed")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
