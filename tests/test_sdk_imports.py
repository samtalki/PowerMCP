"""Every server has to be able to import the MCP SDK it names.

mcp 2.0 removed ``mcp.server.fastmcp`` and moved the server class to
``mcp.server.mcpserver.MCPServer``. This project requires ``mcp>=2,<3``, so a
file still importing the old module cannot start at all — and no existing test
noticed, because the suite launches only the servers whose engine is installed.

The check reads each file's AST instead of importing it: a bridge server pulls
in the simulator it wraps, which is absent in most environments, so importing to
find out would skip the check exactly where it matters.
"""

from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

SERVER_DIRS = (
    "ANDES",
    "Egret",
    "HOPE",
    "LTSpice",
    "OpenDSS",
    "PSCAD",
    "PSLF",
    "PSSE",
    "PowerWorld",
    "PyPSA",
    "pandapower",
    "surge",
)

# Files that still import the removed module, with what each needs beyond a
# rename. Listed rather than skipped so the inventory cannot rot: a file that
# gets migrated fails this test until it is removed from here.
UNMIGRATED = {
    "LTSpice/ltspice_mcp.py": "rename only",
    "OpenDSS/core/server.py": "rename only",
    "OpenDSS/opendss_tools/interactive_view.py": "rename only",
    "OpenDSS/opendss_tools/model.py": "rename only",
    "OpenDSS/opendss_tools/results.py": "rename only",
    "OpenDSS/opendss_tools/simulation.py": "rename only",
    "PSCAD/pscad_mcp/main.py": "rename only",
    "PSCAD/pscad_mcp/tools/app_tools.py": "rename only",
    "PSCAD/pscad_mcp/tools/data_tools.py": "rename only",
    "PSCAD/pscad_mcp/tools/project_tools.py": "rename only",
    "PSCAD/pscad_mcp/tools/simset_tools.py": "rename only",
    # MCPServer takes neither host/port nor transport_security, and exposes no
    # settings.transport_security, so this one is a port rather than a rename.
    "HOPE/src/hope_mcp_server/server.py": "constructor and settings differ",
}

# PowerFactory is built on the separate `fastmcp` distribution, not the SDK.
EXCLUDED = ("PowerFactory",)


def _sdk_imports(path: pathlib.Path) -> list[tuple[str, str]]:
    """(module, name) for every ``from mcp... import name`` in the file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "mcp" or node.module.startswith("mcp."):
                found.extend((node.module, alias.name) for alias in node.names)
    return found


def _server_files() -> list[pathlib.Path]:
    files = []
    for name in SERVER_DIRS:
        directory = REPO / name
        if not directory.is_dir():
            continue
        files.extend(
            p
            for p in sorted(directory.rglob("*.py"))
            if "tests" not in p.parts and "__pycache__" not in p.parts
        )
    return files


def _resolves(module: str, name: str) -> bool:
    try:
        return hasattr(importlib.import_module(module), name)
    except ImportError:
        return False


@pytest.mark.parametrize("path", _server_files(), ids=lambda p: str(p.relative_to(REPO)))
def test_the_sdk_a_server_imports_exists(path):
    rel = str(path.relative_to(REPO))
    if rel.startswith(EXCLUDED):
        pytest.skip("built on the separate fastmcp distribution")
    broken = [
        f"{module}.{name}"
        for module, name in _sdk_imports(path)
        if not _resolves(module, name)
    ]
    if rel in UNMIGRATED:
        assert broken, f"{rel} imports the SDK fine now; drop it from UNMIGRATED"
        return
    assert not broken, f"{rel} imports {broken}, which mcp>=2 does not provide"


def test_the_unmigrated_inventory_names_real_files():
    for rel in UNMIGRATED:
        assert (REPO / rel).is_file(), f"{rel} is gone; drop it from UNMIGRATED"
