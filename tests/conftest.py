"""Build the generated Docker/agent package once per test session."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def pytest_sessionstart(session):
    subprocess.run([sys.executable, str(ROOT / 'scripts/package-docker.py')], check=True, capture_output=True)


@pytest.fixture
def legacy_names(monkeypatch):
    """Generate like older versions: hosts keep ``name=None`` (``backend_<id>``, ``host_<id>``, ``srv_<n>``)."""
    from backend import naming

    monkeypatch.setattr(naming, 'assign', lambda hosts, reserved=(): list(hosts))
