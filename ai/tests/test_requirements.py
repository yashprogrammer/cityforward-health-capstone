"""Every third-party module the service imports must be declared in ai/requirements.txt (a missing one only shows up when the container starts)."""
import ast
import re
import sys
from importlib.metadata import packages_distributions
from pathlib import Path

AI = Path(__file__).resolve().parents[1]


def declared() -> set[str]:
    names = set()
    for line in (AI / "requirements.txt").read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            names.add(re.split(r"[<>=!~\[ ;]", line)[0].lower().replace("_", "-"))
    return names


def imported(package_dir: Path) -> set[str]:
    mods = set()
    for f in package_dir.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Import):
                mods |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods.add(node.module.split(".")[0])
    return mods


OPTIONAL = {"fastembed"}                       # installed only by the image's FASTEMBED=1 build argument (lazy import)
VIA_EXTRA = {"psycopg_pool": "psycopg"}        # psycopg[pool]


def test_runtime_imports_are_declared():
    dists = packages_distributions()
    have = declared()
    missing = []
    for mod in sorted(imported(AI / "copilot")):
        if mod in sys.stdlib_module_names or mod in ("copilot",) or mod in OPTIONAL:
            continue
        if mod in VIA_EXTRA:
            assert VIA_EXTRA[mod] in have
            continue
        owners = {d.lower().replace("_", "-") for d in dists.get(mod, [])}
        # opentelemetry's distributions are split; accept any declared opentelemetry-* package
        if not owners & have and not (mod == "opentelemetry" and any(h.startswith("opentelemetry-") for h in have)):
            missing.append(mod)
    assert not missing, f"imported but not in ai/requirements.txt: {missing}"
