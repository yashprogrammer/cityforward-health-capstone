"""Apply LLM credentials via stdin. No credential in argv, Helm values or files."""
import json
import os
import subprocess
import sys


def main():
    provider = os.environ.get("LLM_PROVIDER", "offline")
    if provider == "offline":
        return
    if provider not in ("foundry", "groq"):
        sys.exit("LLM_PROVIDER must be offline, foundry or groq")
    key = os.environ.get("LLM_API_KEY" if provider == "foundry" else "GROQ_API_KEY")
    base = os.environ.get("LLM_BASE_URL")
    if not key or (provider == "foundry" and not base):
        sys.exit("Missing provider credentials; values withheld")
    if os.environ.get("CF_PAID_MODEL_ACK") != "yes":
        sys.exit("Paid model use requires an approved budget and CF_PAID_MODEL_ACK=yes")
    secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "cityforward-llm", "namespace": "cityforward"},
              "type": "Opaque", "stringData": {"api-key": key}}
    if base:
        secret["stringData"]["base-url"] = base
    r = subprocess.run(["kubectl", "--context", "kind-cityforward", "apply", "--server-side", "--field-manager=cf-bootstrap", "-f", "-"],
                       input=json.dumps(secret), text=True, capture_output=True)
    if r.returncode:
        sys.exit("LLM Secret apply failed; response withheld to protect credentials")
    print("LLM Secret applied (values withheld)")


if __name__ == "__main__":
    main()
