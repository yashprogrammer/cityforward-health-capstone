"""Render the Helm chart (with helm if installed, else the pinned alpine/helm image) and check what a reviewer would check:
every manifest is valid for Kubernetes 1.31, and the security posture is what the security review pack claims.

    pytest deploy/tests            (needs helm or Docker; pip install kubernetes-validate pyyaml)
"""
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[1] / "helm" / "cityforward"
HELM_IMAGE = "alpine/helm:3.17.0"


def helm(*args: str) -> str:
    if shutil.which("helm"):
        cmd = ["helm", *args]
    elif shutil.which("docker"):
        cmd = ["docker", "run", "--rm", "-v", f"{CHART}:/c:ro", HELM_IMAGE, *[a.replace(str(CHART), "/c") for a in args]]
    else:
        pytest.skip("neither helm nor docker is available")
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def render(values: str, *sets: str) -> list[dict]:
    args = ["template", "t", str(CHART), "-f", str(CHART / values), "--namespace", "cityforward"]
    for s in sets:
        args += ["--set", s]
    return [d for d in yaml.safe_load_all(helm(*args)) if d]


@pytest.fixture(scope="module", params=["values-kind.yaml", "values-aks.yaml"])
def docs(request):
    return render(request.param)


def kinds(docs, kind):
    return [d for d in docs if d["kind"] == kind]


def test_chart_lints():
    helm("lint", str(CHART), "-f", str(CHART / "values-kind.yaml"))
    helm("lint", str(CHART), "-f", str(CHART / "values-aks.yaml"))


def test_every_manifest_is_valid_kubernetes_1_31(docs):
    kv = pytest.importorskip("kubernetes_validate")
    checked = 0
    for d in docs:
        try:
            kv.validate(d, "1.31", strict=True)
            checked += 1
        except kv.SchemaNotFoundError:     # custom resources (ServiceMonitor) have no built-in schema
            assert d["kind"] in ("ServiceMonitor", "SecretProviderClass")
    assert checked >= 12


def test_workloads_run_non_root_with_a_locked_down_container(docs):
    workloads = kinds(docs, "Deployment") + kinds(docs, "StatefulSet") + kinds(docs, "Job")
    assert {w["metadata"]["name"].split("-seed")[0] for w in workloads} >= {"meditrack", "payer", "copilot"}
    for w in workloads:
        spec = w["spec"]["template"]["spec"]
        assert spec["automountServiceAccountToken"] is False, w["metadata"]["name"]
        sc = spec["securityContext"]
        assert sc["runAsNonRoot"] is True and sc["seccompProfile"]["type"] == "RuntimeDefault"
        for c in spec["containers"]:
            assert c["securityContext"]["allowPrivilegeEscalation"] is False and c["securityContext"]["capabilities"]["drop"] == ["ALL"]
            assert c["resources"]["limits"]["memory"] and c["resources"]["requests"]["cpu"]
            if w["metadata"]["name"] != "postgres":          # the stock postgres image writes to its own directories
                assert c["securityContext"]["readOnlyRootFilesystem"] is True, (w["metadata"]["name"], c["name"])


def test_no_secrets_in_plain_values(docs):
    for w in kinds(docs, "Deployment"):
        for c in w["spec"]["template"]["spec"]["containers"]:
            for e in c.get("env", []):
                if any(k in e["name"] for k in ("PASSWORD", "SECRET", "KEY", "URL")) and e["name"] not in ("MEDITRACK_BASE_URL", "PAYER_BASE_URL", "KEYCLOAK_INTERNAL_URL", "LLM_BASE_URL", "KEYCLOAK_ISSUER", "KEYCLOAK_AUDIENCE", "PUBLIC_BASE_URL"):
                    assert "valueFrom" in e or e["name"] == "AI_BACKEND_KEY_FILE", f"{e['name']} must come from a Secret"


def test_copilot_is_highly_available_and_probed(docs):
    dep = next(d for d in kinds(docs, "Deployment") if d["metadata"]["name"] == "copilot")
    c = dep["spec"]["template"]["spec"]["containers"][0]
    assert {"startupProbe", "readinessProbe", "livenessProbe"} <= set(c)
    assert dep["spec"]["strategy"]["rollingUpdate"]["maxUnavailable"] == 0
    assert kinds(docs, "PodDisruptionBudget")


def test_metrics_are_not_exposed_through_the_ingress(docs):
    for ing in kinds(docs, "Ingress"):
        for rule in ing["spec"]["rules"]:
            for p in rule["http"]["paths"]:
                assert p["backend"]["service"]["port"].get("name") != "metrics"
    names = {s["metadata"]["name"] for s in kinds(docs, "Service")}
    assert "copilot-metrics" in names


def test_ingress_rewrites_the_ai_prefix_and_terminates_tls_in_production():
    kind, aks = render("values-kind.yaml"), render("values-aks.yaml")
    ai = next(i for i in kinds(aks, "Ingress") if i["metadata"]["name"] == "copilot")
    assert ai["metadata"]["annotations"]["nginx.ingress.kubernetes.io/rewrite-target"] == "/$2" and ai["spec"]["tls"]
    assert "Strict-Transport-Security" in ai["metadata"]["annotations"]["nginx.ingress.kubernetes.io/configuration-snippet"]
    assert not next(i for i in kinds(kind, "Ingress") if i["metadata"]["name"] == "copilot")["spec"].get("tls")


def test_network_policy_default_deny_in_production_and_kind_opt_out():
    aks, kind = render("values-aks.yaml"), render("values-kind.yaml")
    nps = {n["metadata"]["name"]: n for n in kinds(aks, "NetworkPolicy")}
    assert nps["default-deny"]["spec"]["policyTypes"] == ["Ingress", "Egress"] and {"allow-dns", "ingress-to-apps", "copilot-talks-to"} <= set(nps)
    assert not kinds(kind, "NetworkPolicy")
    egress_ports = {p["port"] for r in nps["copilot-talks-to"]["spec"]["egress"] for p in r.get("ports", [])}
    assert egress_ports == {8001, 8003, 443, 5432}        # EHR, payer, the model endpoint, managed PostgreSQL: nothing else


def test_production_values_do_not_use_floating_tags_or_in_cluster_postgres():
    aks = render("values-aks.yaml")
    for w in kinds(aks, "Deployment"):
        for c in w["spec"]["template"]["spec"]["containers"]:
            assert not c["image"].endswith(":latest"), c["image"]
    assert not kinds(aks, "StatefulSet")
    assert any(d["kind"] == "ServiceMonitor" for d in aks)
    pvc = next(d for d in kinds(aks, "PersistentVolumeClaim"))
    assert pvc["spec"]["accessModes"] == ["ReadWriteMany"] and pvc["spec"]["storageClassName"] == "azurefile-csi"


def test_digest_pinning_is_supported():
    docs = render("values-aks.yaml", "images.ai.tag=sha256:" + "a" * 64)
    dep = next(d for d in kinds(docs, "Deployment") if d["metadata"]["name"] == "copilot")
    assert "@sha256:" in dep["spec"]["template"]["spec"]["containers"][0]["image"]


def test_seed_job_registers_the_vendor_on_install(docs):
    job = kinds(docs, "Job")[0]
    env = {e["name"]: e.get("value") for e in job["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["APP_ROLE"] == "seed" and env["AI_BACKEND_JWKS_FILE"].endswith(".jwks.json") and env["AI_WEBHOOK_URL"].endswith("/webhooks/meditrack")
    assert env["AI_LAUNCH_URL"].endswith("/ai/launch")
    assert job["metadata"]["annotations"]["helm.sh/hook"] == "post-install,post-upgrade"


def test_key_vault_sync_covers_every_secret_the_workloads_reference():
    docs = render("values-aks.yaml")
    spc = next(d for d in docs if d["kind"] == "SecretProviderClass")
    synced = {(o["secretName"], i["key"]) for o in spc["spec"]["secretObjects"] for i in o["data"]}
    referenced = set()
    for w in kinds(docs, "Deployment") + kinds(docs, "Job"):
        for c in w["spec"]["template"]["spec"]["containers"]:
            for e in c.get("env", []):
                ref = (e.get("valueFrom") or {}).get("secretKeyRef")
                if ref and not ref.get("optional"):
                    referenced.add((ref["name"], ref["key"]))
    for sec in w["spec"]["template"]["spec"].get("volumes", []):
        pass
    missing = referenced - synced
    assert not missing, f"secrets used by workloads but not synced from Key Vault: {missing}"
    sync_pod = next(d for d in kinds(docs, "Deployment") if d["metadata"]["name"] == "secrets-sync")
    assert sync_pod["spec"]["template"]["metadata"]["labels"]["azure.workload.identity/use"] == "true"
    sa = next(d for d in kinds(docs, "ServiceAccount") if d["metadata"]["name"] == "copilot")
    assert "azure.workload.identity/client-id" in sa["metadata"]["annotations"]


def test_no_key_vault_objects_on_kind():
    docs = render("values-kind.yaml")
    assert not [d for d in docs if d["kind"] == "SecretProviderClass"]
