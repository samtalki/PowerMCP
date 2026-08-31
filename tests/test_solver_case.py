"""The PowerIO module is the single case boundary used by solver tools."""

from __future__ import annotations

import ast
import inspect
import json
import os
from pathlib import Path
from types import SimpleNamespace

import powerio
import pytest

from powermcp.sandbox import PathNotAllowed
from powermcp.solver_case import (
    _available_positions,
    diagnostic_record,
    resolve_solver_case,
)

CASE9 = Path(__file__).parent / "data" / "case9.m"
DSS_CASE = Path(__file__).parents[1] / "OpenDSS" / "13Bus" / "IEEE13Nodeckt.dss"


def _module_json(module: powerio.PioModule) -> str:
    return module.emit("pio-json").text


def _parse_module(text: str) -> powerio.PioModule:
    return powerio.parse_text(text, name="module.pio.json")


def _scenario_module_json() -> str:
    parsed = json.loads(_module_json(powerio.parse_file(CASE9)))
    return json.dumps(
        {
            "schema": "powerio.module",
            "version": 1,
            "producer": parsed["producer"],
            "value": {
                "kind": "balanced_network_scenario_set",
                "data": {
                    "scenarios": [
                        {
                            "id": "base",
                            "probability": 0.75,
                            "value": parsed["value"]["data"],
                        },
                        {
                            "id": "peak",
                            "probability": 0.25,
                            "value": parsed["value"]["data"],
                        },
                    ]
                },
            },
        }
    )


def _legacy_09_module_json() -> str:
    """The published 0.9 wire that PowerIO upgrades one way on read."""
    network = json.loads(powerio.parse_file(CASE9).value.to_json())
    return json.dumps(
        {
            "powerio_version": "0.9.0",
            "producer": {"tool": "powerio", "version": "0.9.0"},
            "model_kind": "balanced",
            "model": {"kind": "balanced", "balanced_network": network},
            "origin": {"kind": "in_memory"},
            "validation": {"status": "ok", "counts": {}},
        }
    )


def test_case_file_uses_parse_file_and_keeps_the_module(monkeypatch):
    original = powerio.parse_file
    calls = []

    def recording_parse_file(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(powerio, "parse_file", recording_parse_file)
    resolved = resolve_solver_case(file_path=str(CASE9))

    assert calls == [((str(CASE9),), {"format": None})]
    assert resolved.module.kind == "balanced_network"
    assert resolved.module.value.n_buses == 9
    assert resolved.network.n_buses == 9


def test_retained_module_emits_the_original_source():
    resolved = resolve_solver_case(file_path=str(CASE9))

    assert resolved.module.emit("matpower").text.encode() == CASE9.read_bytes()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_directory_case_refuses_a_symlinked_descendant_outside_roots(
    tmp_path, monkeypatch
):
    root = tmp_path / "allowed"
    root.mkdir()
    dataset = root / "dataset"
    powerio.parse_file(CASE9).emit("pypsa-csv", dataset)
    outside = tmp_path / "outside-buses.csv"
    (dataset / "buses.csv").replace(outside)
    (dataset / "buses.csv").symlink_to(outside)
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(root))

    with pytest.raises(PathNotAllowed, match="outside its allowed MCP root"):
        resolve_solver_case(file_path=str(dataset), source_format="pypsa-csv")


def test_stored_module_file_preserves_auditable_context(tmp_path):
    source = tmp_path / "case.pio.json"
    document = json.loads(_module_json(powerio.parse_file(CASE9)))
    source.write_text(json.dumps(document))

    resolved = resolve_solver_case(file_path=str(source))

    assert resolved.network.n_buses == 9
    emitted = json.loads(_module_json(resolved.module))
    assert emitted["schema"] == "powerio.module"
    assert emitted["version"] == 1
    assert resolved.module.kind == "balanced_network"
    assert emitted["sources"]
    assert document.get("source_map")
    assert any(entry.get("target") == "" for entry in document["source_map"])
    assert emitted["source_map"] == document["source_map"]


def test_explicit_pio_json_format_preserves_context_without_json_suffix(tmp_path):
    source = tmp_path / "case.data"
    source.write_text(_module_json(powerio.parse_file(CASE9)))

    resolved = resolve_solver_case(file_path=str(source), source_format="pio-json")

    assert resolved.network.n_buses == 9
    assert resolved.module.kind == "balanced_network"
    assert json.loads(_module_json(resolved.module))["schema"] == "powerio.module"


def test_stored_module_validation_runs_while_decoding():
    document = json.loads(_module_json(powerio.parse_file(CASE9)))
    network = document["value"]["data"]
    network["buses"][1]["id"] = network["buses"][0]["id"]

    with pytest.raises(ValueError, match="fails validation"):
        resolve_solver_case(network_json=json.dumps(document))


def test_module_diagnostics_remain_structured_and_ordered():
    document = json.loads(_module_json(powerio.parse_file(CASE9)))
    document["diagnostics"] = [
        {
            "id": "d0",
            "code": "READ.TEST.FIELD",
            "severity": "warning",
            "message": "test finding",
            "target": "/buses/0",
            "details": {"field": "vm"},
        }
    ]

    resolved = resolve_solver_case(network_json=json.dumps(document))

    assert resolved.diagnostics == (
        {
            "code": "READ.TEST.FIELD",
            "severity": "warning",
            "message": "test finding",
            "id": "d0",
            "target": "/buses/0",
            "details": {"field": "vm"},
        },
    )
    assert len(resolved.module.diagnostics) == 1


def test_diagnostic_record_keeps_spans_related_and_details():
    diagnostic = SimpleNamespace(
        code="READ.TEST.SPAN",
        severity="remark",
        message="located finding",
        id="d1",
        target=None,
        suggested_action="inspect the source",
        related=["d0"],
        spans=[SimpleNamespace(source="s0", byte_start=4, byte_end=9)],
        details={"column": 3},
    )

    assert diagnostic_record(diagnostic) == {
        "code": "READ.TEST.SPAN",
        "severity": "remark",
        "message": "located finding",
        "id": "d1",
        "suggested_action": "inspect the source",
        "related": ["d0"],
        "spans": [{"source": "s0", "byte_start": 4, "byte_end": 9}],
        "details": {"column": 3},
    }


def test_collection_requires_powerio_list_and_export():
    module_json = _scenario_module_json()

    with pytest.raises(ValueError) as excinfo:
        resolve_solver_case(network_json=module_json)

    message = str(excinfo.value)
    assert "list_states" in message
    assert "export_state" in message
    assert "['base', 'peak']" in message


def test_exported_collection_state_crosses_the_solver_boundary():
    collection = _parse_module(_scenario_module_json())
    exported = collection.export_state(scenario="peak")

    resolved = resolve_solver_case(network_json=_module_json(exported))

    assert resolved.module.kind == "balanced_network"
    assert resolved.network.n_buses == 9
    assert json.loads(_module_json(resolved.module))["history"]


def test_published_09_wire_upgrades_without_the_removed_package_api():
    resolved = resolve_solver_case(network_json=_legacy_09_module_json())

    assert resolved.module.kind == "balanced_network"
    emitted = json.loads(_module_json(resolved.module))
    assert emitted["schema"] == "powerio.module"
    assert emitted["version"] == 1


def test_powerio_owns_rejection_of_pre_09_stored_documents():
    legacy = json.dumps(
        {
            "schema_version": "0.2.1",
            "model_kind": "balanced",
            "model": {"kind": "balanced"},
        }
    )

    with pytest.raises(ValueError) as excinfo:
        resolve_solver_case(network_json=legacy)

    message = str(excinfo.value)
    assert "powerio_version" in message
    assert "pre 0.9 lineage" in message


def test_model_json_uses_the_same_public_parse_route():
    model_json = powerio.parse_file(CASE9).value.to_json()

    resolved = resolve_solver_case(network_json=model_json)

    assert resolved.module.kind == "balanced_network"


def test_json_inputs_are_not_classified_by_powermcp(monkeypatch):
    original = powerio.parse_text
    calls = []

    def recording_parse_text(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(powerio, "parse_text", recording_parse_text)
    inputs = (
        _module_json(powerio.parse_file(CASE9)),
        _legacy_09_module_json(),
        powerio.parse_file(CASE9).value.to_json(),
    )

    for text in inputs:
        assert resolve_solver_case(network_json=text).module.kind == "balanced_network"

    assert calls == [
        ((text,), {"format": None, "name": "solver-input.json"}) for text in inputs
    ]


def test_powerio_parse_error_type_crosses_the_solver_boundary():
    with pytest.raises(powerio.PowerIOError) as error:
        resolve_solver_case(network_json="{not JSON")

    assert error.value.code == "REQUEST.FORMAT.UNKNOWN"


def test_multiconductor_module_requires_explicit_powerio_lowering():
    module_json = _module_json(powerio.parse_file(DSS_CASE))

    with pytest.raises(ValueError, match=r"PioModule\.to_balanced\(\)"):
        resolve_solver_case(network_json=module_json)


def test_exactly_one_interchange_input_is_required():
    with pytest.raises(ValueError, match="exactly one"):
        resolve_solver_case()
    with pytest.raises(ValueError, match="exactly one"):
        resolve_solver_case(file_path="case.m", network_json="{}")


def test_solver_boundary_exposes_no_collection_selector_aliases():
    parameters = inspect.signature(resolve_solver_case).parameters

    assert "operating_point" not in parameters
    assert "study_commit" not in parameters
    assert "time_position" not in parameters
    assert "scenario" not in parameters


def test_solver_adapter_schemas_expose_no_collection_selector_aliases():
    root = Path(__file__).parents[1]
    adapters = {
        "PyPSA/pypsa_mcp.py": {"import_case_from_any", "import_case_from_json"},
        "pandapower/panda_mcp.py": {
            "load_network_from_any",
            "load_network_from_json",
        },
        "ANDES/andes_mcp.py": {"load_network_from_any", "load_network_from_json"},
        "Egret/egret_mcp.py": {"load_model_from_any", "load_model_from_json"},
    }
    retired = {"operating_point", "study_commit", "time_position", "scenario"}

    for relative, names in adapters.items():
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert names <= functions.keys()
        for name in names:
            arguments = functions[name].args
            parameters = {
                arg.arg
                for arg in [
                    *arguments.posonlyargs,
                    *arguments.args,
                    *arguments.kwonlyargs,
                ]
            }
            assert parameters.isdisjoint(retired), (relative, name, parameters)


def test_large_time_position_inventory_is_compact():
    assert _available_positions(list(range(8760))) == "0..8759 (8760 available)"


def test_sparse_large_time_positions_do_not_materialize_the_range():
    positions = [*range(20), 1_000_000_000]

    assert _available_positions(positions) == (
        "[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, ...] (21 available; last 1000000000)"
    )


def test_temporary_integration_module_names_are_absent():
    package_dir = Path(__file__).parents[1] / "powermcp"
    assert not (package_dir / "powerio_bridge.py").exists()
    assert not (package_dir / "powerio_server.py").exists()
    assert not (package_dir / "powerio_handoff.py").exists()
