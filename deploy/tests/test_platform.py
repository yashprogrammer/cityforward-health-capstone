"""Static checks on everything that is not the application chart: Argo CD apps, platform policies and rules, Terraform.
None of this can be applied without a real cluster/subscription, so these tests assert the properties that make it *correct*: consistency between files and with the code."""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parents[1]
REPO = DEPLOY.parent
APPS = sorted((DEPLOY / "argocd" / "apps").glob("*.yaml"))
sys.path.insert(0, str(REPO / "ai"))


def load(p):
    return yaml.safe_load(Path(p).read_text())


def test_argo_apps_are_wellformed_and_ordered():
    proj = load(DEPLOY / "argocd" / "project.yaml")["spec"]
    repos, dests = set(proj["sourceRepos"]), {d["namespace"] for d in proj["destinations"]}
    waves = {}
    assert len(APPS) == 12
    for f in APPS:
        a = load(f)
        assert a["kind"] == "Application" and a["spec"]["project"] == "cityforward"
        assert a["spec"]["destination"]["namespace"] in dests, f.name
        sources = a["spec"].get("sources") or [a["spec"]["source"]]
        for s in sources:
            assert s["repoURL"] in repos, (f.name, s["repoURL"])
            assert s.get("targetRevision"), f.name
            if "chart" in s:
                assert s["targetRevision"] != "latest" and "*" in s["targetRevision"] or re.match(r"^\d", s["targetRevision"]), f.name
        assert a["spec"]["syncPolicy"]["automated"]["selfHeal"] is True
        waves[a["metadata"]["name"]] = int(a["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"])
    assert waves["linkerd-crds"] < waves["linkerd-control-plane"] < waves["cityforward"]
    assert waves["kyverno"] < waves["kyverno-policies"] < waves["cityforward"]
    assert waves["monitoring"] < waves["observability-rules"] and max(waves.values()) == waves["cityforward"]
    app = load(DEPLOY / "argocd" / "apps" / "20-cityforward.yaml")["spec"]
    assert app["syncPolicy"]["managedNamespaceMetadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted"
    assert app["syncPolicy"]["managedNamespaceMetadata"]["annotations"]["linkerd.io/inject"] == "enabled"
    assert (DEPLOY / "helm" / "cityforward" / app["source"]["helm"]["valueFiles"][0]).exists()


def test_argo_value_files_exist():
    for f in APPS:
        for s in (load(f)["spec"].get("sources") or []):
            for vf in s.get("helm", {}).get("valueFiles", []):
                assert (REPO / vf.replace("$values/", "")).exists(), vf


def test_kyverno_policies_target_the_app_namespace_and_cover_the_security_promises():
    policies = list(yaml.safe_load_all((DEPLOY / "platform" / "kyverno" / "policies.yaml").read_text()))
    rules = {r["name"]: r for p in policies for r in p["spec"]["rules"]}
    assert {"run-as-non-root", "require-resource-limits", "disallow-latest-tag", "no-host-namespaces-or-privileged", "registry-allow-list", "verify-image-signature", "no-secrets-in-env-values"} <= set(rules)
    for p in policies:
        assert p["spec"]["validationFailureAction"] == "Enforce"
        for r in p["spec"]["rules"]:
            ns = r["match"]["any"][0]["resources"]["namespaces"]
            assert ns == ["cityforward"], r["name"]
            assert {"validate", "verifyImages"} & set(r)
    sig = rules["verify-image-signature"]["verifyImages"][0]
    assert sig["required"] is True and sig["attestors"][0]["entries"][0]["keyless"]["issuer"] == "https://token.actions.githubusercontent.com"
    assert "cr.l5d.io/linkerd/*" in rules["registry-allow-list"]["validate"]["pattern"]["spec"]["containers"][0]["image"]     # the mesh proxy must stay admissible


def test_falco_rules_are_complete_and_consistent_with_network_policy():
    values = load(DEPLOY / "platform" / "falco" / "values.yaml")
    rules = [r for r in yaml.safe_load(values["customRules"]["cityforward-rules.yaml"]) if "rule" in r]
    assert {r["rule"] for r in rules} >= {"Shell in a Copilot or MediTrack container", "Read of the AI service private key", "Unexpected outbound connection from Copilot"}
    for r in rules:
        assert r["condition"] and r["output"] and r["priority"] and r["tags"]
    outbound = next(r for r in rules if r["rule"].startswith("Unexpected outbound"))["condition"]
    allowed = {int(x) for x in re.search(r"fd\.sport in \(([^)]*)\)", outbound).group(1).replace(" ", "").split(",")}
    chart_ports = {8001, 8003, 443, 5432}
    assert chart_ports <= allowed        # Falco must not alarm on traffic the NetworkPolicy permits


def test_prometheus_alerts_only_use_metrics_the_service_exports():
    from copilot import telemetry
    exported = set()
    for fam in telemetry.REGISTRY.collect():
        exported |= {fam.name, fam.name + "_total", fam.name + "_bucket", fam.name + "_count", fam.name + "_sum"}
    rules = load(DEPLOY / "platform" / "monitoring" / "prometheus-rules.yaml")["spec"]["groups"]
    alerts = [r for g in rules for r in g["rules"]]
    assert len(alerts) >= 8
    for a in alerts:
        assert a["alert"] and a["expr"] and a["labels"]["severity"]
        for metric in re.findall(r"copilot_[a-z_]+", a["expr"]):
            assert metric in exported, f"{a['alert']} uses {metric}, which the service does not export"
    names = {a["alert"] for a in alerts}
    assert {"AuditLogWriteFailing", "RedactionStoppedWorking", "HighFallbackRate", "BreakGlassUsed"} <= names


def test_keycloak_kustomization_points_at_the_realm_file():
    k = load(DEPLOY / "platform" / "keycloak" / "kustomization.yaml")
    src = k["configMapGenerator"][0]["files"][0].split("=")[1]
    assert (DEPLOY / "platform" / "keycloak" / src).resolve().exists()


# ------------------------------------------------------------------ terraform
TF = DEPLOY / "terraform" / "azure"


def tf_text() -> str:
    return "\n".join(p.read_text() for p in sorted(TF.glob("*.tf")))


def test_terraform_is_formatted_and_parses():
    hcl2 = pytest.importorskip("hcl2")
    for p in TF.glob("*.tf"):
        hcl2.loads(p.read_text())
    binary = shutil.which("terraform") or "/tmp/claude-0/-home-user-FDE-health-care/540a7992-2971-5692-8382-0ee7ca01b1bd/scratchpad/bin/terraform"
    if Path(binary).exists():
        r = subprocess.run([binary, "fmt", "-check", "-recursive", str(TF)], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout


def test_terraform_security_properties():
    t = tf_text()
    # everything that stores PHI is private-endpoint only
    for res in ("azurerm_postgresql_flexible_server", "azurerm_key_vault", "azurerm_container_registry", "azurerm_storage_account"):
        block = re.search(rf'resource "{res}" "\w+" \{{(.*?)\n\}}\n', t, flags=re.S).group(1)
        assert "public_network_access_enabled" in block and "= false" in re.search(r"public_network_access_enabled\s*=\s*(\w+)", block).group(0), res
    assert re.search(r"purge_protection_enabled\s*=\s*true", t) and re.search(r"enable_rbac_authorization\s*=\s*true", t)
    assert re.search(r'variable "private_cluster"[^}]*default\s*=\s*true', t, flags=re.S)
    def has(pattern):
        return re.search(pattern, t) is not None
    assert has(r"local_account_disabled\s*=\s*true") and has(r"azure_rbac_enabled\s*=\s*true")
    assert has(r'network_policy\s*=\s*"cilium"') and has(r"oidc_issuer_enabled\s*=\s*true")
    assert "require_secure_transport" in t and has(r"geo_redundant_backup_enabled\s*=\s*true") and has(r'min_tls_version\s*=\s*"TLS1_2"')
    assert "infrastructure_encryption_enabled" in t
    # no literal credentials: passwords are generated and stored in Key Vault
    assert not re.search(r'(password|secret)\s*=\s*"[^"$]+"', t, flags=re.I)
    assert t.count("random_password") >= 3 and "azurerm_key_vault_secret" in t
    assert "VECTOR" in t           # pgvector is allow-listed
    assert "PGAUDIT" in t and re.search(r'"pgaudit.log"', t)      # database-level audit independent of the application


def test_terraform_outputs_match_what_the_chart_needs():
    t = tf_text()
    for out in ("key_vault_name", "copilot_identity_client_id", "acr_login_server"):
        assert f'output "{out}"' in t
    values = load(DEPLOY / "helm" / "cityforward" / "values-aks.yaml")
    needed = {o["name"] for o in load(DEPLOY / "helm" / "cityforward" / "values.yaml")["keyVault"]["objects"]}
    assert {"meditrack-url", "copilot-url"} <= needed and values["keyVault"]["enabled"] is True
    for name in ("meditrack-url", "copilot-url"):
        assert re.search(rf'name\s*=\s*"{name}"', t)        # Terraform stores the two connection secrets the chart syncs
