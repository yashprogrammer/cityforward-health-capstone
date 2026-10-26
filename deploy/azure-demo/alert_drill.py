"""Alert fire/clear drill against a Kubernetes cluster's Prometheus, with every connection named on the command line.

Nothing is read from the ambient kube context. A drill that touches the wrong cluster is worse than no drill, so the cluster,
kubeconfig, namespace, gateway URL and Prometheus port are all required, and the context must start with `kind-` unless --allow-non-kind is given.

Two real-signal drills (no safety control is weakened, no model is called, no patient data beyond the synthetic chart is touched):

  denials   Sign in through MediTrack's SMART flow as an IT admin (a role with no clinical access) and ask for a stay summary 40 times.
            Each request is refused 403 before any chart or model is read. Expect UnusualAccessDenials (>25 in 10 min) to fire, then clear
            once the 10 minute window slides past.
  down      Scale the copilot Deployment to zero, expect CopilotDown (absent for 5 min) to fire, scale back, expect it to clear.
            The replica count is restored in a finally block.

Alertmanager is disabled in the local profile, so "firing" means Prometheus' own alert state (/api/v1/alerts): no notification is delivered.

    python deploy/azure-demo/alert_drill.py --kube-context kind-cityforward --kubeconfig .data/kubeconfig-kind \\
        --namespace cityforward --base-url http://localhost:8081 --prometheus-port 19090 denials down
"""
import argparse
import base64
import hashlib
import http.client
import json
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

CLIENT_ID = "cityforward-ai-dashboard"
SCOPE = "launch openid fhirUser patient/*.read patient/DocumentReference.write user/Practitioner.read"
ADMISSION = "IP2610-0142"
EVIDENCE: list[dict] = []


def log(event: str, **kw) -> None:
    row = {"t": datetime.now(timezone.utc).strftime("%H:%M:%S"), "event": event, **kw}
    EVIDENCE.append(row)
    print(json.dumps(row), flush=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def http_get(url, headers=None):
    """(status, headers, body). Redirects are returned, not followed."""
    try:
        with OPENER.open(urllib.request.Request(url, headers=headers or {}), timeout=20) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def kubectl(args, *a, check=True):
    cmd = ["kubectl", "--kubeconfig", args.kubeconfig, "--context", args.kube_context, *a]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if check and r.returncode:
        raise SystemExit(f"kubectl {' '.join(a[:3])} failed: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def smart_token(base: str, user: str) -> str:
    """The same authorization-code + PKCE flow the dashboard performs, for a named MediTrack user. The token is never printed."""
    cookie = {"Cookie": f"mt_user={user}"}
    links = json.loads(http_get(f"{base}/int/admin/extlinks")[2])
    lnk = next(entry["LNK_ID"] for entry in links if entry["DISP_MD"] == "SMART")
    st, h, _ = http_get(f"{base}/smart/ehr-launch?lnk_id={lnk}&ip_no={ADMISSION}", cookie)
    launch = urllib.parse.parse_qs(urllib.parse.urlparse(h["Location"]).query)["launch"][0]
    cfg = json.loads(http_get(f"{base}/smart/.well-known/smart-configuration")[2])
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    redirect = f"{base}/ai/auth/callback"
    q = urllib.parse.urlencode({"response_type": "code", "client_id": CLIENT_ID, "redirect_uri": redirect, "scope": SCOPE, "state": secrets.token_urlsafe(12),
                                "aud": f"{base}/fhir", "launch": launch, "code_challenge": challenge, "code_challenge_method": "S256"})
    st, h, _ = http_get(f"{cfg['authorization_endpoint']}?{q}", cookie)
    code = urllib.parse.parse_qs(urllib.parse.urlparse(h["Location"]).query).get("code", [None])[0]
    if not code:
        raise SystemExit("SMART authorize returned no code")
    body = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect, "client_id": CLIENT_ID, "code_verifier": verifier}).encode()
    req = urllib.request.Request(cfg["token_endpoint"], data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)["access_token"]


class Prom:
    def __init__(self, port: int):
        self.url = f"http://127.0.0.1:{port}"

    def get(self, path, **params):
        with urllib.request.urlopen(f"{self.url}{path}?{urllib.parse.urlencode(params)}", timeout=15) as r:
            return json.load(r)["data"]

    def alert_state(self, name: str) -> str:
        """pending | firing | inactive (Prometheus omits alerts that are not active)."""
        return next((a["state"] for a in self.get("/api/v1/alerts")["alerts"] if a["labels"]["alertname"] == name), "inactive")

    def value(self, expr: str):
        res = self.get("/api/v1/query", query=expr)["result"]
        return float(res[0]["value"][1]) if res else None


def wait_state(prom: Prom, name: str, want: str, timeout: int, poll: int = 10) -> float:
    start = time.time()
    last = None
    while time.time() - start < timeout:
        state = prom.alert_state(name)
        if state != last:
            log("alert-state", alert=name, state=state, after_s=round(time.time() - start))
            last = state
        if state == want:
            return time.time() - start
        time.sleep(poll)
    raise SystemExit(f"{name} did not reach '{want}' within {timeout}s (last state: {last})")


def drill_denials(args, prom: Prom) -> None:
    name = "UnusualAccessDenials"
    token = smart_token(args.base_url, "it.admin")
    log("signed-in", user="it.admin", role="IT_ADMIN (no clinical actions)")
    def burst(n: int) -> int:
        refused = 0
        for _ in range(n):
            req = urllib.request.Request(f"{args.base_url}/ai/api/patients/1/summary", method="POST", headers={"Authorization": f"Bearer {token}"})
            try:
                urllib.request.urlopen(req, timeout=20)
            except urllib.error.HTTPError as e:
                refused += e.code == 403
        return refused

    # A counter series that appears already at N has no earlier sample, so increase() reports 0 for it. The first denial of a new
    # (action, outcome) label pair is therefore invisible to the alert: prime the series, let Prometheus scrape it, then send the burst.
    primed = burst(1)
    time.sleep(15)
    denied = burst(40)
    log("traffic-sent", primer=primed, requests=40, refused_403=denied)
    if primed != 1 or denied < 30:
        raise SystemExit("expected the requests to be refused with 403; refusing to continue")
    fired = wait_state(prom, name, "firing", 120)
    log("FIRED", alert=name, seconds_after_traffic=round(fired), denial_increase_10m=prom.value('sum(increase(copilot_access_total{outcome="denied"}[10m]))'))
    cleared = wait_state(prom, name, "inactive", 900, poll=15)
    log("CLEARED", alert=name, seconds_after_firing=round(cleared))


def drill_down(args, prom: Prom) -> None:
    name = "CopilotDown"
    dep = "deploy/copilot"
    replicas = kubectl(args, "-n", args.namespace, "get", dep, "-o", "jsonpath={.spec.replicas}")
    log("baseline", replicas=replicas, up=prom.value('up{job="copilot-metrics"}'))
    try:
        kubectl(args, "-n", args.namespace, "scale", dep, "--replicas=0")
        log("scaled", replicas=0)
        fired = wait_state(prom, name, "firing", 600)
        log("FIRED", alert=name, seconds_after_outage=round(fired), rule_for="5m")
    finally:
        kubectl(args, "-n", args.namespace, "scale", dep, f"--replicas={replicas}")
        log("restored", replicas=replicas)
    kubectl(args, "-n", args.namespace, "rollout", "status", dep, "--timeout=180s")
    cleared = wait_state(prom, name, "inactive", 300)
    log("CLEARED", alert=name, seconds_after_restore=round(cleared))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--kube-context", required=True)
    p.add_argument("--kubeconfig", required=True)
    p.add_argument("--namespace", required=True)
    p.add_argument("--base-url", required=True, help="the gateway origin, e.g. http://localhost:8081")
    p.add_argument("--prometheus-port", type=int, required=True, help="local port for a port-forward the drill opens itself")
    p.add_argument("--prometheus-service", default="monitoring/monitoring-kube-prometheus-prometheus")
    p.add_argument("--allow-non-kind", action="store_true")
    p.add_argument("drills", nargs="+", choices=["denials", "down"])
    args = p.parse_args()
    args.base_url = args.base_url.rstrip("/")
    if not args.kube_context.startswith("kind-") and not args.allow_non_kind:
        raise SystemExit("refusing a non-kind context without --allow-non-kind")
    log("connections", kube_context=args.kube_context, kubeconfig=args.kubeconfig, namespace=args.namespace, base_url=args.base_url,
        prometheus=f"port-forward svc/{args.prometheus_service} -> 127.0.0.1:{args.prometheus_port}",
        cluster_server=kubectl(args, "config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}"))
    pns, psvc = args.prometheus_service.split("/")
    pf = subprocess.Popen(["kubectl", "--kubeconfig", args.kubeconfig, "--context", args.kube_context, "-n", pns, "port-forward", f"svc/{psvc}", f"{args.prometheus_port}:9090"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        prom = Prom(args.prometheus_port)
        for _ in range(30):
            try:
                prom.get("/api/v1/status/buildinfo")
                break
            except (urllib.error.URLError, ConnectionError, http.client.HTTPException):
                time.sleep(1)
        else:
            raise SystemExit("Prometheus not reachable through the port-forward")
        rules = {r["name"] for g in prom.get("/api/v1/rules")["groups"] for r in g["rules"]}
        for needed in ("UnusualAccessDenials", "CopilotDown"):
            if needed not in rules:
                raise SystemExit(f"rule {needed} is not loaded in this Prometheus")
        active = [a["labels"]["alertname"] for a in prom.get("/api/v1/alerts")["alerts"]]
        if active:
            raise SystemExit(f"baseline is not clean, alerts already active: {active}")
        log("preflight-ok", rules_loaded=len(rules), copilot_up=prom.value('up{job="copilot-metrics"}'), active_alerts=0)
        for d in args.drills:
            {"denials": drill_denials, "down": drill_down}[d](args, prom)
        log("DONE", drills=args.drills, note="Alertmanager disabled in this profile: state observed in Prometheus, no notification delivered")
        return 0
    finally:
        pf.terminate()


if __name__ == "__main__":
    sys.exit(main())
