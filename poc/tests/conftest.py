import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
BUNDLES = Path(__file__).resolve().parents[2] / "customer" / "synthea" / "prepared" / "bundles"
