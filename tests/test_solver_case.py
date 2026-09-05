"""Typed modules, explicit state selection and validation at the solver boundary."""
from __future__ import annotations

import json
import os
from pathlib import Path
import powerio
import pytest
from powermcp.sandbox import PathNotAllowed
from powermcp.solver_case import resolve_solver_case

CASE9 = Path(__file__).parent / "data" / "case9.m"


def ir(module):
    return powerio.serialize(module).text


def test_case_file_constructs_a_validated_instance():
    resolved = resolve_solver_case(file_path=str(CASE9))
    assert resolved.network.n_buses == 9
    assert isinstance(resolved.module, powerio.PioModule)
    assert resolved.package is None
    assert "mpc.bus" in resolved.emit("matpower").text


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_directory_case_refuses_a_symlinked_descendant_outside_roots(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    dataset = root / "dataset"
    powerio.emit(powerio.parse(CASE9), "pypsa-csv", dataset)
    outside = tmp_path / "outside-buses.csv"
    (dataset / "buses.csv").replace(outside)
    (dataset / "buses.csv").symlink_to(outside)
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(root))
    with pytest.raises(PathNotAllowed, match="outside its allowed MCP root"):
        resolve_solver_case(file_path=str(dataset), source_format="pypsa-csv")


def test_ir_preserves_auditable_context(tmp_path):
    source = tmp_path / "case.pio.json"
    source.write_text(ir(powerio.parse(CASE9)))
    resolved = resolve_solver_case(file_path=str(source))
    assert resolved.network.n_buses == 9
    assert resolved.package["schema"] == "pio-ir"
    assert resolved.package["generation"] == 2
    assert resolved.package["producer"]["name"] == "powerio"
    assert resolved.package["selection"] == {}


def test_invalid_identities_cannot_reuse_stale_diagnostics():
    document = json.loads(ir(powerio.parse(CASE9)))
    buses = document["value"]["data"]["buses"]
    buses[1]["id"] = buses[0]["id"]
    document["diagnostics"] = []
    with pytest.raises((ValueError, RuntimeError)):
        resolve_solver_case(network_json=json.dumps(document))


def test_nested_collections_require_explicit_selection():
    network = powerio.parse(CASE9).value
    series = powerio.TimeSeries([network, network], time_points=[powerio.TimePoint("h0"), powerio.TimePoint("h1")])
    scenarios = powerio.ScenarioSet({"base": series, "alternative": series})
    payload = ir(powerio.PioModule.from_value(scenarios))
    with pytest.raises(ValueError, match="scenario_id"):
        resolve_solver_case(network_json=payload)
    with pytest.raises(ValueError, match="time_index"):
        resolve_solver_case(network_json=payload, scenario_id="base")
    resolved = resolve_solver_case(network_json=payload, scenario_id="alternative", time_index=1)
    assert resolved.network.n_buses == 9
    assert resolved.package["selection"] == {"scenario_id":"alternative", "time_index":1}
    for index in (-1, True, 2):
        with pytest.raises((ValueError, IndexError)):
            resolve_solver_case(network_json=payload, scenario_id="base", time_index=index)
    with pytest.raises(KeyError):
        resolve_solver_case(network_json=payload, scenario_id="missing", time_index=0)


def test_calculation_instance_keeps_its_type():
    module = powerio.parse(CASE9).to_dc_opf_instance()
    resolved = resolve_solver_case(network_json=ir(module))
    assert isinstance(resolved.module.value, powerio.DcOpfInstance)
    assert resolved.network.n_buses == 9
    assert resolved.emit("matpower").text


def test_multiconductor_input_requires_explicit_lowering():
    payload = '{"meta":{"frequency":50},"bus":{"b":{"terminal_names":["a","b","c","n"]}}}'
    with pytest.raises(ValueError, match="explicitly call to_balanced"):
        resolve_solver_case(network_json=payload, source_format="bmopf-json")


def test_legacy_package_and_history_require_migration():
    with pytest.raises(ValueError, match="migration"):
        resolve_solver_case(network_json='{"model_kind":"balanced","model":{}}')
    with pytest.raises(ValueError, match="Tellegen Study"):
        resolve_solver_case(file_path=str(CASE9), study_commit=0)


def test_exactly_one_input_and_matching_selectors_are_required():
    with pytest.raises(ValueError, match="exactly one"):
        resolve_solver_case()
    with pytest.raises(ValueError, match="exactly one"):
        resolve_solver_case(file_path="case.m", network_json="{}")
    with pytest.raises(ValueError, match="does not match"):
        resolve_solver_case(file_path=str(CASE9), time_index=0)
