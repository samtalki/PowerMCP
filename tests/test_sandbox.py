"""Containment for model supplied paths.

A tool argument is whatever the model was persuaded to ask for, so a tool that
takes a path and opens it reads or writes wherever the model points. These
tests hold `powermcp.sandbox` to the policy the powerio MCP server already
applies, and hold every bridge server to actually using it.
"""

from __future__ import annotations

import ast
import os
import pathlib

import pytest

from powermcp.sandbox import PathNotAllowed, allowed_roots, checked_path

REPO = pathlib.Path(__file__).resolve().parent.parent

# Every server tool that takes a path from the model, and the argument it takes.
GUARDED = {
    "pandapower/panda_mcp.py": {
        "load_network": ["file_path"],
        "load_network_from_any": ["file_path"],
    },
    "PyPSA/pypsa_mcp.py": {
        "load_network": ["file_path"],
        "import_from_csv_folder": ["folder_path"],
        "export_to_csv_folder": ["folder_path"],
        "import_case_from_any": ["file_path", "output_path"],
        "import_case_from_json": ["output_path"],
    },
    "Egret/egret_mcp.py": {
        "solve_unit_commitment_problem": ["case_file"],
        "solve_ac_opf": ["case_file"],
        "solve_dc_opf": ["case_file"],
        "load_model_from_any": ["file_path"],
    },
    "ANDES/andes_mcp.py": {
        "run_power_flow": ["file_path"],
        "run_eigenvalue_analysis": ["file_path"],
        "load_network_from_json": ["out_path"],
        "load_network_from_any": ["file_path", "out_path"],
    },
    "surge/surge_mcp.py": {
        "load_network": ["file_path"],
        "save_network": ["file_path"],
        "export_tables": ["output_dir"],
    },
    "PowerWorld/powerworld_mcp.py": {
        "open_case": ["case_path"],
    },
}


def test_unset_roots_constrain_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("POWERIO_MCP_ALLOWED_ROOTS", raising=False)
    monkeypatch.delenv("POWERIO_MCP_ALLOWED_ROOT", raising=False)
    assert allowed_roots() == ()
    assert checked_path(str(tmp_path / "anywhere.m"), purpose="p")


def test_a_path_inside_a_root_is_admitted(tmp_path, monkeypatch):
    root = tmp_path / "cases"
    root.mkdir()
    case = root / "c.m"
    case.write_text("")
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(root))
    assert checked_path(str(case), purpose="file_path") == str(case)


def test_a_path_outside_every_root_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "cases"
    root.mkdir()
    outside = tmp_path / "secret.m"
    outside.write_text("")
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(root))
    with pytest.raises(PathNotAllowed):
        checked_path(str(outside), purpose="file_path")


def test_dot_dot_does_not_climb_out(tmp_path, monkeypatch):
    root = tmp_path / "cases"
    root.mkdir()
    (tmp_path / "secret.m").write_text("")
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(root))
    with pytest.raises(PathNotAllowed):
        checked_path(str(root / ".." / "secret.m"), purpose="file_path")


@pytest.mark.skipif(os.name == "nt", reason="posix symlink semantics")
def test_a_symlink_is_judged_by_its_target(tmp_path, monkeypatch):
    root = tmp_path / "cases"
    root.mkdir()
    secret = tmp_path / "secret.m"
    secret.write_text("")
    (root / "innocent.m").symlink_to(secret)
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(root))
    with pytest.raises(PathNotAllowed):
        checked_path(str(root / "innocent.m"), purpose="file_path")


@pytest.mark.skipif(os.name == "nt", reason="posix symlink semantics")
def test_a_write_through_a_dangling_symlink_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "out"
    root.mkdir()
    (root / "new.m").symlink_to(tmp_path / "elsewhere.m")
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(root))
    with pytest.raises(PathNotAllowed):
        checked_path(str(root / "new.m"), purpose="output_path", for_write=True)


def test_a_remote_uri_is_not_a_local_path(monkeypatch):
    monkeypatch.delenv("POWERIO_MCP_ALLOWED_ROOTS", raising=False)
    with pytest.raises(PathNotAllowed):
        checked_path("https://example.invalid/case.m", purpose="file_path")


def test_a_file_uri_decodes(tmp_path, monkeypatch):
    case = tmp_path / "a b.m"
    case.write_text("")
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(tmp_path))
    assert checked_path(case.as_uri(), purpose="file_path") == str(case)


@pytest.mark.parametrize("server", sorted(GUARDED))
def test_every_path_taking_tool_checks_its_argument(server):
    """Read the server source rather than importing it.

    A bridge server pulls in the simulator it wraps, which is not installed in
    every environment, so importing to introspect would skip the check exactly
    where it matters. The guard is a syntactic property and the AST shows it.
    """
    path = REPO / server
    if not path.exists():
        pytest.skip(f"{server} not present")
    tree = ast.parse(path.read_text())
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name, args in GUARDED[server].items():
        assert name in functions, f"{server}: {name} is gone"
        checked = {
            target.id
            for node in ast.walk(functions[name])
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "checked_path"
        }
        missing = set(args) - checked
        assert not missing, f"{server}: {name} does not check {sorted(missing)}"
