import os
from pathlib import Path
import subprocess
import sys


def test_invalid_cloud_url_does_not_expose_credentials_in_logs():
    root = Path(__file__).resolve().parents[2]
    secret = "private-password-that-must-not-appear"
    environment = dict(os.environ, DATABASE_URL=f"postgresql+psycopg://user:{secret}invalid-host/db")
    result = subprocess.run([sys.executable, str(root / "scripts/run_cloud_searches.py")],
                            cwd=root, env=environment, capture_output=True, text=True)
    assert result.returncode == 1
    assert secret not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert "Cloud search failed:" in result.stdout
