"""Run the API with SQLite, its local queue and no Docker services."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

if __name__ == "__main__":
    from setup_searches import configure
    configure(True, [])
    os.environ["AUTH_REQUIRED"] = "false"
    os.environ["EMBEDDED_SEARCHES"] = "true"
    # configure() has loaded settings; update the cached values too.
    from jobpilot.config import settings
    settings().auth_required = False
    settings().embedded_searches = True
    import uvicorn
    uvicorn.run("jobpilot.api:app", host="127.0.0.1", port=8000, workers=1)
