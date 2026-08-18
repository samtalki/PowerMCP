"""Containment for model supplied paths.

A tool argument is whatever the model was persuaded to ask for, so a tool that
takes a path and opens it reads or writes wherever the model points. The policy
is powerio's; these tests are the consumer suite over it, and they hold every
bridge server to actually using it.
"""

from __future__ import annotations

import ast
import os
import pathlib

import pytest

import powerio.mcp.sandbox
import powermcp.sandbox
from powermcp.sandbox import PathNotAllowed, allowed_roots, checked_path

REPO = pathlib.Path(__file__).resolve().parent.parent

# Every spelling powerio reads, so a test can clear them all before asserting
# that an unconfigured installation constrains nothing.
ROOT_ENVS = (powermcp.sandbox.ALLOWED_ROOTS_ENV,) + powermcp.sandbox.LEGACY_ROOT_ENVS

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

# Path-taking tools the policy does not reach yet, with the argument that gets
# to the filesystem unchecked. Listed rather than ignored so the inventory
# cannot rot: guarding one of these fails the test until it moves to GUARDED.
# An operator who sets POWERIO_MCP_ALLOWED_ROOTS constrains the servers above
# and none of these.
UNGUARDED = {
    "LTSpice/ltspice_mcp.py": {
        # `open(log_file_path).read()` returned to the model verbatim.
        "read_simulation_log": ["log_file_path"],
        "list_available_traces": ["raw_file_path"],
        "plot_specific_traces": ["raw_file_path"],
        "run_simulation": ["netlist_path"],
        "view_netlist_in_ltspice": ["netlist_path"],
    },
    "OpenDSS/opendss_tools/configuration.py": {
        "compile_opendss_file": ["dss_file"],
    },
    "PSSE/psse_mcp.py": {
        "open_case": ["case"],
    },
    "PSLF/pslf_mcp.py": {
        "open_case": ["case"],
    },
}


def test_the_policy_is_powerios(monkeypatch):
    """One implementation, not two that agree today.

    The drift this guards against already happened once: the two copies read
    different environment variables, so an operator could configure containment
    and get it on one server and not another.
    """
    assert powermcp.sandbox.checked_path is powerio.mcp.sandbox.checked_path
    assert powermcp.sandbox.allowed_roots is powerio.mcp.sandbox.allowed_roots


@pytest.mark.parametrize("env", ROOT_ENVS)
def test_every_root_spelling_configures_containment(tmp_path, monkeypatch, env):
    root = tmp_path / "cases"
    root.mkdir()
    (tmp_path / "secret.m").write_text("")
    for name in ROOT_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(env, str(root))
    assert allowed_roots() == (root.resolve(),)
    with pytest.raises(PathNotAllowed, match="outside allowed MCP roots"):
        checked_path(str(tmp_path / "secret.m"), purpose="file_path")


def test_unset_roots_constrain_nothing(tmp_path, monkeypatch):
    for name in ROOT_ENVS:
        monkeypatch.delenv(name, raising=False)
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
    with pytest.raises(PathNotAllowed, match="outside allowed MCP roots"):
        checked_path(str(outside), purpose="file_path")


def test_dot_dot_does_not_climb_out(tmp_path, monkeypatch):
    root = tmp_path / "cases"
    root.mkdir()
    (tmp_path / "secret.m").write_text("")
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(root))
    with pytest.raises(PathNotAllowed, match="outside allowed MCP roots"):
        checked_path(str(root / ".." / "secret.m"), purpose="file_path")


@pytest.mark.skipif(os.name == "nt", reason="posix symlink semantics")
def test_a_symlink_is_judged_by_its_target(tmp_path, monkeypatch):
    root = tmp_path / "cases"
    root.mkdir()
    secret = tmp_path / "secret.m"
    secret.write_text("")
    (root / "innocent.m").symlink_to(secret)
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(root))
    with pytest.raises(PathNotAllowed, match="outside allowed MCP roots"):
        checked_path(str(root / "innocent.m"), purpose="file_path")


@pytest.mark.skipif(os.name == "nt", reason="posix symlink semantics")
def test_a_write_through_a_dangling_symlink_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "out"
    root.mkdir()
    (root / "new.m").symlink_to(tmp_path / "elsewhere.m")
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(root))
    with pytest.raises(PathNotAllowed, match="outside allowed MCP roots"):
        checked_path(str(root / "new.m"), purpose="output_path", for_write=True)


def test_a_remote_uri_is_not_a_local_path(monkeypatch):
    for name in ROOT_ENVS:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(PathNotAllowed, match="must be a local path"):
        checked_path("https://example.invalid/case.m", purpose="file_path")


def test_a_file_uri_decodes(tmp_path, monkeypatch):
    case = tmp_path / "a b.m"
    case.write_text("")
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(tmp_path))
    assert checked_path(case.as_uri(), purpose="file_path") == str(case)


def _checked_arguments(server: str) -> dict[str, set[str]]:
    """Per tool, the arguments assigned from a ``checked_path`` call.

    Reads the server source rather than importing it: a bridge server pulls in
    the simulator it wraps, which is not installed in every environment, so
    importing to introspect would skip the check exactly where it matters. The
    guard is a syntactic property and the AST shows it.
    """
    tree = ast.parse((REPO / server).read_text())
    return {
        node.name: {
            target.id
            for inner in ast.walk(node)
            if isinstance(inner, ast.Assign)
            for target in inner.targets
            if isinstance(target, ast.Name)
            and isinstance(inner.value, ast.Call)
            and isinstance(inner.value.func, ast.Name)
            and inner.value.func.id == "checked_path"
        }
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


@pytest.mark.parametrize("server", sorted(GUARDED))
def test_every_path_taking_tool_checks_its_argument(server):
    if not (REPO / server).exists():
        pytest.skip(f"{server} not present")
    checked = _checked_arguments(server)
    for name, args in GUARDED[server].items():
        assert name in checked, f"{server}: {name} is gone"
        missing = set(args) - checked[name]
        assert not missing, f"{server}: {name} does not check {sorted(missing)}"


@pytest.mark.parametrize("server", sorted(UNGUARDED))
def test_the_unguarded_inventory_is_accurate(server):
    if not (REPO / server).exists():
        pytest.skip(f"{server} not present")
    checked = _checked_arguments(server)
    for name, args in UNGUARDED[server].items():
        assert name in checked, f"{server}: {name} is gone; drop it from UNGUARDED"
        now_checked = set(args) & checked[name]
        assert not now_checked, (
            f"{server}: {name} now checks {sorted(now_checked)}; move it to GUARDED"
        )
