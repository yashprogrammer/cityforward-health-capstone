"""Container entrypoint for the customer's world: one image, the role is chosen by APP_ROLE."""
import os
import sys

ROLES = {
    "meditrack": ["uvicorn", "meditrack.backend.app:app", "--host", "0.0.0.0", "--port", "8001"],
    "payer": ["uvicorn", "mock_payer.app:app", "--host", "0.0.0.0", "--port", "8003"],
    "seed": [sys.executable, "-m", "scripts.seed_once"],
}

role = os.getenv("APP_ROLE", "")
if role not in ROLES:
    sys.exit(f"APP_ROLE must be one of {', '.join(ROLES)} (got {role!r})")
cmd = ROLES[role] + sys.argv[1:]
os.execvp(cmd[0], cmd)
