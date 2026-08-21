"""Every advertised server must import, register tools, and call MCP run."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from powermcp.registry import TOOLS

REPO = Path(__file__).resolve().parents[1]
SMOKE = Path(__file__).with_name("server_entry_smoke.py")


@pytest.mark.parametrize("tool_name", TOOLS)
def test_advertised_server_starts_without_vendor_engine(tool_name):
    result = subprocess.run(
        [sys.executable, str(SMOKE), tool_name],
        cwd=REPO,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
