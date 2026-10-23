"""The compliance documents name code, tests and config as evidence. Fail if any of those references stops being true."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = sorted((ROOT / "docs" / "compliance").glob("*.md"))
PATH_RE = re.compile(r"`((?:ai|customer|deploy|docs|\.github)/[\w./\-{}]+?)(?:::(\w+))?`")


def collect():
    for doc in DOCS:
        for m in PATH_RE.finditer(doc.read_text()):
            yield doc.name, m.group(1), m.group(2)


@pytest.mark.parametrize("doc,path,test", [pytest.param(*t, id=f"{t[0]}:{t[1]}{'::' + t[2] if t[2] else ''}") for t in sorted(set(collect()), key=lambda t: (t[0], t[1], t[2] or ""))])
def test_referenced_evidence_exists(doc, path, test):
    if "{" in path:          # URL templates like /api/patients/{id}/...
        pytest.skip("not a file path")
    p = ROOT / path
    assert p.exists(), f"{doc} cites {path}, which does not exist"
    if test:
        assert re.search(rf"def {test}\b", p.read_text()), f"{doc} cites {path}::{test}, which does not exist"


def test_there_is_evidence_to_check():
    assert len(set(collect())) > 60


def test_markdown_links_between_documents_resolve():
    for doc in DOCS:
        for target in re.findall(r"\]\(([\w./\-]+\.md)\)", doc.read_text()):
            assert (doc.parent / target).resolve().exists(), f"{doc.name} links to missing {target}"


def test_kyverno_trusts_exactly_the_release_workflow_that_exists():
    import yaml
    pol = list(yaml.safe_load_all((ROOT / "deploy/platform/kyverno/policies.yaml").read_text()))
    subject = next(r for p in pol for r in p["spec"]["rules"] if r["name"] == "verify-image-signature")["verifyImages"][0]["attestors"][0]["entries"][0]["keyless"]["subject"]
    workflow = subject.split("/.github/workflows/")[1].split("@")[0]
    assert (ROOT / ".github" / "workflows" / workflow).exists()
    assert "id-token: write" in (ROOT / ".github/workflows" / workflow).read_text() and "cosign sign" in (ROOT / ".github/workflows" / workflow).read_text()


def test_prometheus_runbook_pointer_is_real():
    rules = (ROOT / "deploy/platform/monitoring/prometheus-rules.yaml").read_text()
    for ref in re.findall(r"(docs/[\w./\-]+\.md)", rules):
        assert (ROOT / ref).exists(), ref
    ir = (ROOT / "docs/compliance/incident-response-plan.md").read_text()
    assert "## 4. Detection and triage" in ir and "section 4" in rules
