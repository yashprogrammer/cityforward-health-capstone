"""Small, secret-safe Foundry lifecycle for approved development only (no AKS).

Credentials stay in memory. Local state contains resource IDs, never keys/endpoints.
Use `plan`, then `up --approved --email ADDRESS`. `down` requires the RG name.
"""
import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".data" / "foundry-resource.json"
API = "2025-06-01"
MODEL = "gpt-oss-120b"


def az_json(*args):
    r = subprocess.run(["az", *args, "-o", "json"], capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise RuntimeError("Azure CLI operation failed; raw response withheld")
    return json.loads(r.stdout)


class Azure:
    def __init__(self):
        account = az_json("account", "show")
        if account["name"] != "Databricks_live" or not account["id"].endswith("13767b6e"):
            raise RuntimeError("Select the approved Databricks_live subscription first")
        self.scope = "/subscriptions/" + account["id"]
        self.token = az_json("account", "get-access-token", "--resource", "https://management.azure.com/")["accessToken"]

    def request(self, method, path, body=None, version=API, allow_missing=False, attempt=0):
        url = "https://management.azure.com" + path + "?api-version=" + version
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                                     headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                delay = max(30, int(e.headers.get("Retry-After", "30")))
                print(f"Azure throttled the operation; waiting {delay}s before retry {attempt + 1}/3", flush=True)
                while delay:
                    pause = min(delay, 30)
                    time.sleep(pause)
                    delay -= pause
                return self.request(method, path, body, version, allow_missing, attempt + 1)
            if allow_missing and e.code == 404:
                return None
            try:
                code = json.loads(e.read()).get("error", {}).get("code", "unknown")
            except (ValueError, AttributeError):
                code = "unknown"
            raise RuntimeError(f"Azure operation failed: HTTP {e.code}, code={code}; details withheld") from None

    def wait(self, path, label):
        for _ in range(90):
            d = self.request("GET", path)
            state = d.get("properties", {}).get("provisioningState", "Succeeded")
            if state == "Succeeded":
                print(label + ": Succeeded", flush=True)
                return d
            if state in ("Failed", "Canceled"):
                raise RuntimeError(label + ": " + state)
            time.sleep(5)
        raise RuntimeError(label + ": readiness timed out; inspect status before retrying")


def load_state():
    if not STATE.exists():
        raise RuntimeError("No local Foundry resource state; run the approved up step first")
    return json.loads(STATE.read_text())


def save_state(s):
    STATE.parent.mkdir(mode=0o700, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2) + "\n")
    STATE.chmod(0o600)


def up(az, email):
    print("Checking billing currency before creating the budget", flush=True)
    # Cost budgets use the subscription's billing currency, not the retail API currency.
    cost = az.request("POST", az.scope + "/providers/Microsoft.CostManagement/query",
                      {"type": "ActualCost", "timeframe": "MonthToDate", "dataset": {"granularity": "None", "aggregation": {"totalCost": {"name": "PreTaxCost", "function": "Sum"}}}}, version="2023-03-01")
    cols = [c["name"] for c in cost["properties"]["columns"]]
    currencies = {r[cols.index("Currency")] for r in cost["properties"]["rows"]} if "Currency" in cols else set()
    if len(currencies) != 1 or not currencies <= {"USD", "INR"}:
        raise RuntimeError("Budget currency is unsupported or unknown; currencies=" + str(sorted(currencies)) + ". No resources created.")
    currency = next(iter(currencies))
    amount = 5
    if currency == "INR":
        query = {"$filter": "productName eq 'Azure OpenAI OSS Models' and armRegionName eq 'eastus2' and meterName eq 'gpt-oss-120B Inp glbl Tokens'", "currencyCode": "'INR'"}
        with urllib.request.urlopen("https://prices.azure.com/api/retail/prices?" + urllib.parse.urlencode(query), timeout=30) as r:
            prices = json.load(r)["Items"]
        if len(prices) != 1 or prices[0]["currencyCode"] != "INR" or prices[0]["unitOfMeasure"] != "1K":
            raise RuntimeError("Could not verify INR conversion for the approved USD budget")
        # Allow 20% headroom for taxes/conversion; cost budgets themselves are pre-tax.
        amount = int(5 * prices[0]["retailPrice"] / 0.00015 / 1.2)
        if amount <= 0:
            raise RuntimeError("Invalid converted budget")
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    budget = az.scope + "/providers/Microsoft.Consumption/budgets/cf-demo-5usd"
    body = {"properties": {"category": "Cost", "amount": amount, "timeGrain": "Monthly",
            "timePeriod": {"startDate": now.strftime("%Y-%m-01T00:00:00Z"), "endDate": (now + timedelta(days=365)).strftime("%Y-%m-%dT00:00:00Z")},
            "filter": {"tags": {"name": "purpose", "operator": "In", "values": ["cf-demo"]}},
            "notifications": {f"actual{n}": {"enabled": True, "operator": "GreaterThanOrEqualTo", "threshold": n, "thresholdType": "Actual", "contactEmails": [email]} for n in [50, 80, 100]}}}
    print(f"Creating budget: {currency} {amount}", flush=True)
    az.request("PUT", budget, body, version="2024-08-01")
    actual = az.request("GET", budget, version="2024-08-01")["properties"]
    assert actual["amount"] == amount and len(actual["notifications"]) == 3
    print(f"Budget verified: {currency} {amount}, cf-demo tag filter, alerts 50/80/100%", flush=True)
    if STATE.exists():
        s = load_state()
        if s["subscriptionScope"] != az.scope:
            raise RuntimeError("Stored subscription differs from approved subscription")
    else:
        suffix = uuid.uuid4().hex[:8]
        s = {"subscriptionScope": az.scope, "group": "rg-cf-demo-foundry-" + suffix, "account": "cf-demo-foundry-" + suffix,
             "region": "eastus2", "project": "cityforward", "deployment": MODEL, "budget": budget,
             "createdAt": now.isoformat(), "deleteAfter": (now + timedelta(days=1)).date().isoformat()}
        save_state(s)
    tags = {"purpose": "cf-demo", "owner": "yashpatil", "delete-after": s["deleteAfter"]}
    group = az.scope + "/resourceGroups/" + s["group"]
    existing = az.request("GET", group, version="2021-04-01", allow_missing=True)
    if existing and existing.get("tags", {}).get("purpose") != "cf-demo":
        raise RuntimeError("Refusing to modify a resource group without the cf-demo tag")
    if not existing:
        az.request("PUT", group, {"location": s["region"], "tags": tags}, version="2021-04-01")
    account = group + "/providers/Microsoft.CognitiveServices/accounts/" + s["account"]
    if not az.request("GET", account, allow_missing=True):
        az.request("PUT", account, {"location": s["region"], "kind": "AIServices", "sku": {"name": "S0"}, "tags": tags,
                   "identity": {"type": "SystemAssigned"},
                   "properties": {"customSubDomainName": s["account"], "allowProjectManagement": True, "publicNetworkAccess": "Enabled"}})
    az.wait(account, "Foundry account")
    project = account + "/projects/" + s["project"]
    if not az.request("GET", project, allow_missing=True):
        az.request("PUT", project, {"location": s["region"], "tags": tags, "identity": {"type": "SystemAssigned"}, "properties": {"displayName": "CityForward synthetic development", "description": "Short-lived course development; synthetic data only"}})
    az.wait(project, "Foundry project")
    deployment = account + "/deployments/" + s["deployment"]
    if not az.request("GET", deployment, allow_missing=True):
        az.request("PUT", deployment, {"sku": {"name": "GlobalStandard", "capacity": 10},
                   "properties": {"model": {"format": "OpenAI-OSS", "name": MODEL, "version": "1"}}})
    az.wait(deployment, "Model deployment")
    print("Resource group: " + s["group"], flush=True)
    print("No inference calls made. No AKS, provisioned throughput, database or GPU created.", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["plan", "up", "status", "down"])
    p.add_argument("--approved", action="store_true")
    p.add_argument("--email")
    args = p.parse_args()
    if args.action == "plan":
        print("Plan: USD 5 tag-filtered monthly budget with 50/80/100% alerts; one tagged resource group, AIServices account, project, gpt-oss-120b v1 GlobalStandard deployment in eastus2.")
        print("Inference: $0.15/M input + $0.60/M output. Validation allowance <= $0.50. No provisioned compute; account/project/model have no idle token charge. No AKS.")
        return
    az = Azure()
    if args.action == "up":
        if not args.approved or not args.email or "@" not in args.email:
            p.error("up requires --approved and --email after the owner approves the displayed plan")
        up(az, args.email)
        return
    s = load_state()
    if s["subscriptionScope"] != az.scope:
        raise RuntimeError("Subscription mismatch")
    group = az.scope + "/resourceGroups/" + s["group"]
    d = az.request("GET", group, version="2021-04-01", allow_missing=True)
    if args.action == "status":
        print(json.dumps({"resourceGroup": s["group"], "exists": d is not None, "model": MODEL, "deleteAfter": s["deleteAfter"], "idleComputeUsdPerHour": 0, "destroy": "python deploy/azure-demo/foundry.py down"}))
        return
    if d:
        if d.get("tags", {}).get("purpose") != "cf-demo" or d.get("tags", {}).get("owner") != "yashpatil":
            raise RuntimeError("Refusing to delete a resource group not owned by this demo")
        if input("Type resource group name to destroy " + s["group"] + ": ") != s["group"]:
            raise RuntimeError("Confirmation did not match")
        az.request("DELETE", group, version="2021-04-01")
        for _ in range(120):
            if az.request("GET", group, version="2021-04-01", allow_missing=True) is None:
                break
            time.sleep(5)
        else:
            raise RuntimeError("Resource group deletion still pending; run status")
    deleted = az.request("GET", az.scope + "/providers/Microsoft.CognitiveServices/deletedAccounts")
    reserved = any(a.get("name") == s["account"] or a.get("properties", {}).get("accountName") == s["account"] for a in deleted.get("value", []))
    print(json.dumps({"resourceGroupDeleted": True, "softDeletedAccountFound": reserved, "budgetRetained": True}))
    s["destroyed"] = True
    save_state(s)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, urllib.error.URLError, TimeoutError) as e:
        # Never print request URLs, headers, raw SDK errors or credentials.
        print(str(e) if isinstance(e, RuntimeError) else "Network operation failed; details withheld")
        raise SystemExit(1)
