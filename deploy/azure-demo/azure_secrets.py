"""Fill the Key Vault the chart reads from, and publish the AI service's PUBLIC key set to the cluster. Secret-safe and idempotent.

Creates only what is missing (an existing signing key is never rotated by a rerun):
  copilot-webhook-secret, payer-api-key, ai-backend-key (PEM), meditrack-signing-key (PEM), llm-api-key, llm-base-url
and applies the ConfigMap cityforward-ai-jwks (public keys only). The two connection-URL secrets are written by Terraform.

Credentials travel in memory and over HTTPS only: nothing goes in argv, files outside a 0700 temp dir that is removed on exit, logs or stdout.
The model key is read from the Foundry account with the caller's own Azure login and is never printed.

    python deploy/azure-demo/azure_secrets.py --vault kv-cityforward-xxxxx --kube-context aks-cityforward-demo --kubeconfig .data/kubeconfig-azure
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".data" / "foundry-resource.json"


def az(*args) -> str:
    r = subprocess.run(["az", *args, "-o", "tsv"], capture_output=True, text=True, timeout=60)
    if r.returncode or not r.stdout.strip():
        raise SystemExit("Azure CLI call failed or returned nothing (details withheld)")
    return r.stdout.strip()


class Vault:
    def __init__(self, name: str):
        self.base = f"https://{name}.vault.azure.net/secrets"
        self.token = az("account", "get-access-token", "--resource", "https://vault.azure.net", "--query", "accessToken")

    def call(self, method: str, name: str, body: dict | None = None, attempt: int = 0):
        req = urllib.request.Request(f"{self.base}/{name}?api-version=7.4", method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (401, 403) and attempt < 12:      # a fresh role assignment takes a few minutes to reach the data plane
                time.sleep(15)
                return self.call(method, name, body, attempt + 1)
            raise SystemExit(f"Key Vault {method} {name} failed: HTTP {e.code} (response withheld)") from None

    def get(self, name: str):
        r = self.call("GET", name)
        return r["value"] if r else None

    def put_if_missing(self, name: str, make) -> str:
        existing = self.get(name)
        if existing is not None:
            print(f"kept     {name}")
            return existing
        value = make()
        self.call("PUT", name, {"value": value})
        print(f"created  {name}")
        return value


def kubectl(args, *a, stdin: str | None = None) -> None:
    r = subprocess.run(["kubectl", "--kubeconfig", args.kubeconfig, "--context", args.kube_context, *a], input=stdin, text=True, capture_output=True, timeout=120)
    if r.returncode:
        raise SystemExit("kubectl failed: " + r.stderr.strip()[:200])


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--vault", required=True)
    p.add_argument("--kube-context", required=True)
    p.add_argument("--kubeconfig", required=True)
    p.add_argument("--namespace", default="cityforward")
    args = p.parse_args()
    python = os.environ.get("PYTHON", sys.executable)
    foundry = json.loads(STATE.read_text())
    vault = Vault(args.vault)

    vault.put_if_missing("copilot-webhook-secret", lambda: secrets.token_hex(24))
    vault.put_if_missing("payer-api-key", lambda: secrets.token_hex(16))

    with tempfile.TemporaryDirectory() as tmp:
        os.chmod(tmp, 0o700)
        keys = Path(tmp) / "keys"
        keys.mkdir(mode=0o700)

        def new_ai_key() -> str:
            subprocess.run([python, str(ROOT / "ai/scripts/gen_keys.py"), str(keys)], check=True, capture_output=True, cwd=ROOT)
            return (keys / "ai-backend.pem").read_text()

        pem = vault.put_if_missing("ai-backend-key", new_ai_key)
        (keys / "ai-backend.pem").write_text(pem)
        (keys / "ai-backend.pem").chmod(0o600)
        subprocess.run([python, str(ROOT / "ai/scripts/gen_keys.py"), str(keys)], check=True, capture_output=True, cwd=ROOT)    # existing PEM -> same public JWKS
        jwks = (keys / "ai-backend.jwks.json").read_text()

        def new_signing_key() -> str:
            path = Path(tmp) / "sig.pem"
            subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(path)], check=True, capture_output=True)
            return path.read_text()

        vault.put_if_missing("meditrack-signing-key", new_signing_key)

    manifest = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "cityforward-ai-jwks", "namespace": args.namespace}, "data": {"ai-backend.jwks.json": jwks}}
    kubectl(args, "apply", "--validate=false", "-f", "-", stdin=json.dumps(manifest))       # skips the multi-minute OpenAPI schema download; the manifest is ours
    print("applied  ConfigMap cityforward-ai-jwks (public keys only)")

    key = az("cognitiveservices", "account", "keys", "list", "-g", foundry["group"], "-n", foundry["account"], "--query", "key1")
    if len(key) < 20:
        raise SystemExit("Foundry key lookup returned an unexpected value")
    vault.call("PUT", "llm-api-key", {"value": key})
    vault.call("PUT", "llm-base-url", {"value": f"https://{foundry['account']}.openai.azure.com/openai/v1/"})
    print("synced   llm-api-key, llm-base-url (values withheld)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
