"""Find out which PostgreSQL Flexible Server SKU/region combinations this subscription can actually create.

`az postgres flexible-server list-skus` listed Burstable SKUs for eastus2 that the create call then refused (SkuNotAvailable), so the only reliable test
is to create one. Each probe makes a tiny server (32 GB, no HA, no network, random throwaway password sent in the request body, never in argv or output)
in its own tagged scratch resource group and deletes the group straight away. A refused SKU is free; an accepted one bills for the minutes it exists.

    python deploy/azure-demo/pg_probe.py eastus2:GP_Standard_D2ds_v5 centralus:B_Standard_B1ms centralus:GP_Standard_D2ds_v5 ...
"""
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from foundry import Azure           # noqa: E402  (subscription guard, token, throttling retry)

API = "2024-08-01"
TAGS = {"purpose": "cf-demo", "owner": "yashpatil", "role": "pg-probe"}


def put_server(az: Azure, path: str, region: str, sku: str) -> str | None:
    tier = {"B": "Burstable", "G": "GeneralPurpose", "M": "MemoryOptimized"}[sku[0]]
    body = {"location": region, "tags": TAGS, "sku": {"name": "Standard_" + sku.split("Standard_", 1)[1], "tier": tier},
            "properties": {"version": "16", "administratorLogin": "probeadmin", "administratorLoginPassword": secrets.token_urlsafe(24) + "aA1!",
                           "storage": {"storageSizeGB": 32}, "highAvailability": {"mode": "Disabled"},
                           "backup": {"backupRetentionDays": 7, "geoRedundantBackup": "Disabled"}, "network": {"publicNetworkAccess": "Disabled"}}}
    req = urllib.request.Request(f"https://management.azure.com{path}?api-version={API}", method="PUT", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + az.token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.headers.get("Azure-AsyncOperation")
    except urllib.error.HTTPError as e:                     # refused synchronously
        try:
            err = json.loads(e.read()).get("error", {})
        except ValueError:
            err = {}
        raise RuntimeError(f"rejected at request time: HTTP {e.code} {err.get('code', '')} {err.get('message', '')[:140]}") from None


def poll(az: Azure, url: str, limit: int = 1200) -> tuple[str, str]:
    start = time.time()
    while time.time() - start < limit:
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + az.token})
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.load(r)
        if d.get("status") in ("Succeeded", "Failed", "Canceled"):
            err = d.get("error") or {}
            return d["status"], f"{err.get('code', '')} {err.get('message', '')[:160]}".strip()
        time.sleep(15)
    return "TimedOut", "no terminal state within the limit; the scratch group is deleted anyway"


def probe(az: Azure, region: str, sku: str) -> dict:
    group = f"rg-cf-demo-pgprobe-{region}"
    gpath = az.scope + "/resourceGroups/" + group
    started = time.time()
    tags = TAGS | {"delete-after": (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()}
    result = {"region": region, "sku": sku}
    az.request("PUT", gpath, {"location": region, "tags": tags}, version="2021-04-01")
    try:
        op = put_server(az, gpath + "/providers/Microsoft.DBforPostgreSQL/flexibleServers/cfprobe-" + secrets.token_hex(4), region, sku)
        status, detail = poll(az, op) if op else ("Succeeded", "")
        result |= {"status": status, "detail": detail}
    except RuntimeError as e:
        result |= {"status": "Rejected", "detail": str(e)}
    finally:
        az.request("DELETE", gpath, version="2021-04-01")
        for _ in range(120):
            if az.request("GET", gpath, version="2021-04-01", allow_missing=True) is None:
                break
            time.sleep(10)
        else:
            result["cleanup"] = "SCRATCH GROUP STILL EXISTS: " + group
    result["seconds"] = round(time.time() - started)
    print(json.dumps(result), flush=True)
    return result


def main() -> int:
    pairs = [a.split(":", 1) for a in sys.argv[1:]]
    if not pairs:
        raise SystemExit(__doc__)
    az = Azure()
    results = [probe(az, region, sku) for region, sku in pairs]
    leftovers = [r for r in results if "cleanup" in r]
    print("\nWORKS: " + (", ".join(f"{r['region']}/{r['sku']}" for r in results if r["status"] == "Succeeded") or "none"))
    return 1 if leftovers else 0


if __name__ == "__main__":
    sys.exit(main())
