"""Create the application database roles on the private PostgreSQL server from INSIDE the cluster, so the database never opens to the internet.

Reads the admin password and the two role passwords from Key Vault (Terraform wrote them there), puts the SQL and the admin password in a short-lived
Kubernetes Secret, runs deploy/terraform/azure/scripts/db-roles.sql in a one-shot Job, shows psql's output (no secrets in it) and removes both.
Idempotent: db-roles.sql updates passwords when the roles already exist.

    python deploy/azure-demo/azure_db_roles.py --vault kv-... --kube-context aks-cityforward-demo --kubeconfig .data/kubeconfig-azure
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from azure_secrets import ROOT, Vault          # noqa: E402

NS = "default"
JOB = "cf-db-roles"


def kubectl(args, *a, stdin=None, check=True) -> str:
    r = subprocess.run(["kubectl", "--kubeconfig", args.kubeconfig, "--context", args.kube_context, "-n", NS, *a], input=stdin, text=True, capture_output=True, timeout=300)
    if check and r.returncode:
        raise SystemExit("kubectl failed: " + r.stderr.strip()[:300])
    return r.stdout


def parse(url: str) -> tuple[str, str]:
    u = urllib.parse.urlparse(url)
    return urllib.parse.unquote(u.password or ""), u.hostname or ""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--vault", required=True)
    p.add_argument("--kube-context", required=True)
    p.add_argument("--kubeconfig", required=True)
    args = p.parse_args()
    vault = Vault(args.vault)
    admin = vault.get("postgres-admin-password")
    mt_pw, host = parse(vault.get("meditrack-url") or "")
    co_pw, _ = parse(vault.get("copilot-url") or "")
    if not (admin and mt_pw and co_pw and host):
        raise SystemExit("Key Vault is missing the Terraform-written database secrets; nothing changed")

    sql = f"\\set meditrack_pw '{mt_pw}'\n\\set copilot_pw '{co_pw}'\n" + (ROOT / "deploy/terraform/azure/scripts/db-roles.sql").read_text()
    kubectl(args, "delete", "job", JOB, "--ignore-not-found")
    secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "cf-db-roles", "namespace": NS}, "type": "Opaque", "stringData": {"admin": admin, "roles.sql": sql}}
    kubectl(args, "apply", "--validate=false", "-f", "-", stdin=json.dumps(secret))
    job = {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": JOB, "namespace": NS},
           "spec": {"backoffLimit": 2, "ttlSecondsAfterFinished": 300, "template": {"spec": {"restartPolicy": "Never", "containers": [{
               "name": "psql", "image": "postgres:16",
               "command": ["psql", f"host={host} user=cfadmin dbname=postgres sslmode=require", "-v", "ON_ERROR_STOP=1", "-f", "/sql/roles.sql"],
               "env": [{"name": "PGPASSWORD", "valueFrom": {"secretKeyRef": {"name": "cf-db-roles", "key": "admin"}}}],
               "volumeMounts": [{"name": "sql", "mountPath": "/sql", "readOnly": True}]}],
               "volumes": [{"name": "sql", "secret": {"secretName": "cf-db-roles", "items": [{"key": "roles.sql", "path": "roles.sql"}]}}]}}}}
    try:
        kubectl(args, "apply", "--validate=false", "-f", "-", stdin=json.dumps(job))
        done = False
        for _ in range(60):
            st = json.loads(kubectl(args, "get", "job", JOB, "-o", "json"))["status"]
            if st.get("succeeded"):
                done = True
                break
            if st.get("failed", 0) > 2:
                break
            time.sleep(5)
        print(kubectl(args, "logs", f"job/{JOB}", check=False).strip()[-600:])
        if not done:
            raise SystemExit("database role job did not succeed (see output above)")
        print("database roles ready")
    finally:
        kubectl(args, "delete", "job", JOB, "--ignore-not-found", check=False)
        kubectl(args, "delete", "secret", "cf-db-roles", "--ignore-not-found", check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
