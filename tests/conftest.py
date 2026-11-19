"""Build the generated Docker/agent package once per test session."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def pytest_sessionstart(session):
    subprocess.run([sys.executable, str(ROOT / 'scripts/package-docker.py')], check=True, capture_output=True)
