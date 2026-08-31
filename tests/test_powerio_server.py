"""Tests for the PowerIO conversion server, solver integrations, and the
registry/runner wiring.

The server under test is powerio's own ``powerio.mcp.server``: this repo runs
that module and keeps no copy of it, so these tests are the consumer suite over
a dependency's surface. powerio is a core dependency, so it is normally present;
the importorskip below stays as insurance for stripped-down environments.
The public in process helpers exercise the tool implementations;
``test_transport.py`` covers what only a real MCP transport shows.
The launch test lives here rather than in test_runner.py so it skips with the
rest of the module.

tests/data/case9.m is vendored verbatim from
https://github.com/MATPOWER/matpower/tree/master/data (BSD-3).
tests/data/powerworld/ACTIVSg200.pwd is vendored from powerio's test suite
(eigenergy/powerio); ACTIVSg200 is a public Texas A&M synthetic grid.
"""

from __future__ import annotations

import asyncio
import builtins
import importlib
import json
import pickle
import sys
import types
from pathlib import Path

import pytest
from packaging.version import Version

powerio = pytest.importorskip("powerio")
assert Version("1.0.0") <= Version(powerio.__version__) < Version("2.0.0")
from powerio.mcp import server as powerio_mcp  # noqa: E402

from powermcp.registry import TOOLS  # noqa: E402

_PYPSA_DIR = str(TOOLS["pypsa"].resolve_server_dir())
if _PYPSA_DIR not in sys.path:
    sys.path.insert(0, _PYPSA_DIR)

import pypsa  # noqa: E402  (core dependency, like the server itself)
import pypsa_mcp  # noqa: E402

CASE9 = Path(__file__).resolve().parent / "data" / "case9.m"
ACTIVSG200_PWD = (
    Path(__file__).resolve().parent / "data" / "powerworld" / "ACTIVSg200.pwd"
)
OPENDSS_CASE = (
    Path(__file__).resolve().parents[1] / "OpenDSS" / "13Bus" / "IEEE13Nodeckt.dss"
)

# 3-bus case with rating 0 branches, for the overwrite_zero_s_nom tests.
ZERO_RATE_CASE = """function mpc = zero_rate
mpc.version = '2';
mpc.baseMVA = 100.0;
mpc.bus = [
\t1 3 0 0 0 0 1 1.0 0.0 230.0 1 1.1 0.9;
\t2 1 50 10 0 0 1 1.0 0.0 230.0 1 1.1 0.9;
\t3 1 30 5 0 0 1 1.0 0.0 230.0 1 1.1 0.9;
];
mpc.gen = [
\t1 80 0 50 -50 1.0 100 1 200 0 0 0 0 0 0 0 0 0 0 0 0;
];
mpc.branch = [
\t1 2 0.01 0.05 0.0 0 0 0 0 0 1 -360 360;
\t2 3 0.01 0.05 0.0 0 0 0 0 0 1 -360 360;
];
"""


def _diagnostic_codes(result):
    return {item["code"] for item in result["diagnostics"]}


def _module_json(module: powerio.PioModule) -> str:
    return module.emit("pio-json").text


def _parse_module(text: str) -> powerio.PioModule:
    return powerio.parse_text(text, name="module.pio.json")


def _diagnostic_module_json() -> str:
    document = json.loads(_module_json(powerio.parse_file(CASE9)))
    document["diagnostics"] = [
        {
            "id": "consumer-test",
            "code": "READ.TEST.FIELD",
            "severity": "warning",
            "message": "consumer test diagnostic",
            "target": "/buses/0",
        }
    ]
    return json.dumps(document)


def _assert_structured_adapter_response(result):
    assert result["status"] == "success", result
    assert "warnings" not in result
    assert "package" not in result
    assert "module" not in result
    assert result["diagnostics"]
    assert all(
        {"code", "severity", "message"} <= diagnostic.keys()
        for diagnostic in result["diagnostics"]
    )
    assert "READ.TEST.FIELD" in _diagnostic_codes(result)


def _load_egret_mcp(tmp_path, monkeypatch):
    pytest.importorskip("egret")
    monkeypatch.setenv("POWERMCP_HOME", str(tmp_path / "powermcp-home"))
    monkeypatch.syspath_prepend(str(TOOLS["egret"].resolve_server_dir()))
    return importlib.import_module("egret_mcp")


def test_parse_json_round_trips():
    r = powerio_mcp.parse(path=str(CASE9))
    assert r["schema"] == "powerio.parse"
    assert r["powerio_version"] == powerio.__version__
    assert r["domain"] == "transmission"
    assert r["model"] == "balanced"
    assert r["json_format"] == "model-json"
    assert r["source_format"] == "matpower"
    assert isinstance(r["diagnostics"], list)
    assert r["summary"]["elements"]["buses"] == 9
    assert powerio.from_json(r["json"]).n_buses == 9


def test_tool_surface_is_canonical():
    tools = {tool.name: tool for tool in asyncio.run(powerio_mcp.mcp.list_tools())}
    names = set(tools)
    preferred_names = {
        "emit",
        "summarize",
        "parse",
        "to_normalized",
        "calc_matrix",
        "diagnostics",
        "display",
        "inspect",
        "list_states",
        "inspect_state",
        "export_state",
        "to_balanced_report",
        "to_balanced",
        "about",
    }
    obsolete_names = {
        "convert",
        "save",
        "summary",
        "normalize",
        "matrix",
        "state_inventory",
        "select_state",
        "to_balanced_inspect",
        "dc_data",
    }
    assert preferred_names <= names
    assert names.isdisjoint(obsolete_names)
    for name in ("parse", "summarize", "to_normalized", "calc_matrix", "display"):
        props = tools[name].input_schema["properties"]
        assert "from_format" in props
    parse_props = tools["parse"].input_schema["properties"]
    assert "transport" in parse_props
    emit_schema = tools["emit"].input_schema
    assert emit_schema["required"] == ["format"]
    emit_props = emit_schema["properties"]
    assert "format" in emit_props and "destination" in emit_props
    assert "from_format" in emit_props and "module_json" in emit_props
    assert "to_format" not in emit_props and "out_path" not in emit_props
    for name in ("summarize", "to_normalized", "calc_matrix"):
        assert "module_json" in tools[name].input_schema["properties"]


def test_to_normalized_returns_dense_one_based_ids():
    r = powerio_mcp.to_normalized(path=str(CASE9))
    case = powerio.from_json(r["json"])
    assert [b["id"] for b in case.buses] == list(range(1, 10))


def test_parse_transport_accepted_downstream():
    r = powerio_mcp.parse(path=str(CASE9))
    assert powerio.from_json(r["json"]).n_buses == 9


def test_calc_matrix_bprime():
    m = powerio_mcp.calc_matrix("bprime", path=str(CASE9))
    assert m["schema"] == "powerio.calc_matrix"
    assert m["powerio_version"] == powerio.__version__
    assert m["domain"] == "transmission"
    assert m["model"] == "balanced"
    assert m["json_format"] == "model-json"
    assert m["source_format"] == "matpower"
    assert isinstance(m["diagnostics"], list)
    assert m["format"] == "coo"
    assert m["shape"] == [9, 9]
    assert m["nnz"] > 0
    assert isinstance(m["nnz"], int)
    # plain Python scalars, not numpy types
    assert type(m["data"][0]) is float
    assert type(m["row"][0]) is int
    assert type(m["col"][0]) is int


def test_calc_matrix_accepts_json_transport():
    transport = powerio_mcp.parse(path=str(CASE9))["json"]
    from_json = powerio_mcp.calc_matrix("bprime", json=transport)
    from_path = powerio_mcp.calc_matrix("bprime", path=str(CASE9))
    assert from_json["shape"] == from_path["shape"]
    assert from_json["nnz"] == from_path["nnz"]


def test_calc_matrix_unknown_kind():
    with pytest.raises(ValueError):
        powerio_mcp.calc_matrix("nope", path=str(CASE9))


def test_emit_powermodels():
    r = powerio_mcp.emit(format="powermodels-json", path=str(CASE9))
    assert r["schema"] == "powerio.emit"
    assert isinstance(r["diagnostics"], list)
    assert len(json.loads(r["text"])["bus"]) == 9


def test_summarize_fields():
    s = powerio_mcp.summarize(path=str(CASE9))
    assert s["schema"] == "powerio.summarize"
    assert s["powerio_version"] == powerio.__version__
    assert s["domain"] == "transmission"
    assert s["model"] == "balanced"
    assert s["json_format"] == "model-json"
    assert isinstance(s["diagnostics"], list)
    assert s["elements"]["buses"] == 9
    assert s["base_mva"] == 100.0
    assert s["source_format"] == "matpower"
    assert s["topology"]["connected_components"] == 1
    assert s["elements"]["branches"] == 9
    assert s["topology"]["connectivity_report"]


def test_exactly_one_input_enforced():
    with pytest.raises(ValueError):
        powerio_mcp.summarize()
    with pytest.raises(ValueError):
        powerio_mcp.summarize(path="x", content="y")
    with pytest.raises(ValueError):
        powerio_mcp.calc_matrix("bprime")
    with pytest.raises(ValueError):
        powerio_mcp.calc_matrix("bprime", path=str(CASE9), json="{}")


def test_inline_matpower_content_defaults_to_matpower():
    assert powerio_mcp.emit(format="psse", content=CASE9.read_text())["text"]


def test_calc_matrix_lacpf():
    m = powerio_mcp.calc_matrix("lacpf", path=str(CASE9))
    assert m["format"] == "coo"
    assert m["shape"] == [18, 18]
    assert m["nnz"] > 0
    assert type(m["data"][0]) is float
    assert type(m["row"][0]) is int


def test_emit_writes_file(tmp_path):
    out = tmp_path / "case9.json"
    r = powerio_mcp.emit(
        format="powermodels-json", destination=str(out), path=str(CASE9)
    )
    assert r["path"] == str(out)
    assert r["bytes_written"] == out.stat().st_size
    assert isinstance(r["diagnostics"], list)
    assert len(json.loads(out.read_text())["bus"]) == 9


def test_emit_refuses_overwrite(tmp_path):
    out = tmp_path / "case9.m"
    out.write_text("existing")
    with pytest.raises(ValueError, match="overwrite"):
        powerio_mcp.emit(
            format="matpower", destination=str(out), path=str(CASE9)
        )
    r = powerio_mcp.emit(
        format="matpower", destination=str(out), path=str(CASE9), overwrite=True
    )
    assert r["bytes_written"] == out.stat().st_size


def test_emit_accepts_json_transport(tmp_path):
    transport = powerio_mcp.parse(path=str(CASE9))["json"]
    out = tmp_path / "case9.m"
    powerio_mcp.emit(format="matpower", destination=str(out), json=transport)
    assert powerio.parse_file(out).value.n_buses == 9


def test_module_transport_flows_through_core_tools(tmp_path):
    parsed = powerio_mcp.parse(path=str(CASE9), transport="module")
    assert parsed["schema"] == "powerio.parse"
    assert parsed["transport"] == "module"
    assert parsed["json_format"] == "module"
    assert parsed["domain"] == "transmission"
    assert parsed["model"] == "balanced"
    assert "module_json" in parsed

    module = json.loads(parsed["module_json"])
    assert module["schema"] == "powerio.module"
    assert module["version"] == 1
    assert module["value"]["kind"] == "balanced_network"

    module_json = parsed["module_json"]
    assert powerio_mcp.summarize(module_json=module_json)["elements"]["buses"] == 9

    matrix = powerio_mcp.calc_matrix("bprime", module_json=module_json)
    assert matrix["kind"] == "bprime"
    assert matrix["shape"] == [9, 9]

    out = tmp_path / "case9.m"
    powerio_mcp.emit(
        format="matpower", destination=str(out), module_json=module_json
    )
    assert powerio.parse_file(out).value.n_buses == 9

    diag = powerio_mcp.diagnostics(module_json)
    assert diag["schema"] == "powerio.diagnostics"
    assert diag["model_kind"] == "balanced"
    assert diag["summary"]["status"] in {"ok", "info", "warning", "error", "fatal"}
    assert isinstance(diag["summary"]["text"], str)
    assert isinstance(diag["diagnostics"], list)


def test_pypsa_interchange_accepts_static_module(tmp_path):
    module_json = _diagnostic_module_json()
    source_map = json.loads(module_json).get("source_map", [])
    assert source_map
    out = tmp_path / "case9-module.nc"
    result = pypsa_mcp.import_case_from_json(module_json, str(out))

    _assert_structured_adapter_response(result)
    assert len(pypsa.Network(str(out)).buses) == 9


def test_pandapower_interchange_accepts_static_module():
    panda_dir = str(TOOLS["pandapower"].resolve_server_dir())
    if panda_dir not in sys.path:
        sys.path.insert(0, panda_dir)
    import panda_mcp  # noqa: E402

    result = panda_mcp.load_network_from_json(_diagnostic_module_json())

    _assert_structured_adapter_response(result)
    assert len(panda_mcp._current_net.bus) == 9


def test_core_solver_adapters_preserve_powerio_error_codes(tmp_path):
    panda_dir = str(TOOLS["pandapower"].resolve_server_dir())
    if panda_dir not in sys.path:
        sys.path.insert(0, panda_dir)
    import panda_mcp  # noqa: E402

    results = (
        panda_mcp.load_network_from_json("{not JSON"),
        pypsa_mcp.import_case_from_json("{not JSON", str(tmp_path / "bad.nc")),
    )

    for result in results:
        assert result["status"] == "error"
        assert result["code"] == "REQUEST.FORMAT.UNKNOWN"
        assert "REQUEST.FORMAT.UNKNOWN" in result["message"]


def test_pandapower_export_uses_powerio_ppc_and_module_emit(monkeypatch):
    panda_dir = str(TOOLS["pandapower"].resolve_server_dir())
    if panda_dir not in sys.path:
        sys.path.insert(0, panda_dir)
    import panda_mcp  # noqa: E402

    loaded = panda_mcp.load_network_from_any(str(CASE9))
    assert loaded["status"] == "success", loaded

    calls = []
    original_from_ppc = powerio.from_ppc

    def recording_from_ppc(ppc):
        calls.append(ppc)
        return original_from_ppc(ppc)

    monkeypatch.setattr(powerio, "from_ppc", recording_from_ppc)
    result = panda_mcp.export_network_to_format("matpower")

    assert result["status"] == "success", result
    assert len(calls) == 1
    assert "mpc.baseMVA" in result["text"]
    assert isinstance(result["diagnostics"], list)
    assert "warnings" not in result


def test_egret_interchange_accepts_static_module(tmp_path, monkeypatch):
    egret_mcp = _load_egret_mcp(tmp_path, monkeypatch)

    result = egret_mcp.load_model_from_json(_diagnostic_module_json())

    _assert_structured_adapter_response(result)
    assert Path(result["case_file"]).is_file()


def test_egret_preserves_powerio_error_codes(tmp_path, monkeypatch):
    egret_mcp = _load_egret_mcp(tmp_path, monkeypatch)

    result = egret_mcp.load_model_from_json("{not JSON")

    assert result["status"] == "error"
    assert result["code"] == "REQUEST.FORMAT.UNKNOWN"
    assert "REQUEST.FORMAT.UNKNOWN" in result["message"]


def test_egret_interchange_accepts_case_file(tmp_path, monkeypatch):
    egret_mcp = _load_egret_mcp(tmp_path, monkeypatch)

    result = egret_mcp.load_model_from_any(str(CASE9))

    assert result["status"] == "success", result
    assert Path(result["case_file"]).is_file()
    assert result["model_info"]["bus"] == 9
    assert isinstance(result["diagnostics"], list)
    assert "module" not in result
    assert "warnings" not in result
    assert "package" not in result


def test_solver_interchange_requires_powerio_collection_export(tmp_path):
    parsed = json.loads(_module_json(powerio.parse_file(CASE9)))
    collection_json = json.dumps(
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
                            "probability": 1.0,
                            "value": parsed["value"]["data"],
                        }
                    ]
                },
            },
        }
    )

    rejected = pypsa_mcp.import_case_from_json(
        collection_json, str(tmp_path / "unselected.nc")
    )
    assert rejected["status"] == "error"
    assert "list_states" in rejected["message"]
    assert "export_state" in rejected["message"]

    panda_dir = str(TOOLS["pandapower"].resolve_server_dir())
    if panda_dir not in sys.path:
        sys.path.insert(0, panda_dir)
    import panda_mcp  # noqa: E402

    panda_rejected = panda_mcp.load_network_from_json(collection_json)
    assert panda_rejected["status"] == "error"
    assert "list_states" in panda_rejected["message"]
    assert "export_state" in panda_rejected["message"]

    collection = _parse_module(collection_json)
    exported = collection.export_state(scenario="base")
    out = tmp_path / "selected.nc"
    selected = pypsa_mcp.import_case_from_json(_module_json(exported), str(out))
    assert selected["status"] == "success", selected
    assert "module" not in selected
    assert len(pypsa.Network(str(out)).buses) == 9


def test_egret_requires_powerio_collection_export(tmp_path, monkeypatch):
    egret_mcp = _load_egret_mcp(tmp_path, monkeypatch)
    parsed = json.loads(_module_json(powerio.parse_file(CASE9)))
    collection_json = json.dumps(
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
                            "probability": 1.0,
                            "value": parsed["value"]["data"],
                        }
                    ]
                },
            },
        }
    )

    rejected = egret_mcp.load_model_from_json(collection_json)

    assert rejected["status"] == "error"
    assert "list_states" in rejected["message"]
    assert "export_state" in rejected["message"]


def test_emit_requires_exactly_one_input(tmp_path):
    out = tmp_path / "x.m"
    with pytest.raises(ValueError):
        powerio_mcp.emit(format="matpower", destination=str(out))
    with pytest.raises(ValueError):
        powerio_mcp.emit(
            format="matpower", destination=str(out), path="a", json="{}"
        )


def test_pypsa_import_case_from_any(tmp_path):
    out = tmp_path / "case9.nc"
    r = pypsa_mcp.import_case_from_any(str(CASE9), str(out))
    assert r["status"] == "success", r
    assert out.exists()
    assert r["network_file"] == str(out)
    assert r["info"]["buses"] == 9
    assert len(pypsa.Network(str(out)).buses) == 9


def test_pypsa_import_case_from_json(tmp_path):
    transport = powerio_mcp.parse(path=str(CASE9))["json"]
    out = tmp_path / "case9.nc"
    r = pypsa_mcp.import_case_from_json(transport, str(out))
    assert r["status"] == "success", r
    assert len(pypsa.Network(str(out)).buses) == 9


def test_pypsa_import_preserves_supported_generator_costs(tmp_path):
    out = tmp_path / "costs.nc"
    result = pypsa_mcp.import_case_from_any(str(CASE9), str(out))
    assert result["status"] == "success", result
    generators = pypsa.Network(str(out)).generators
    assert (generators.marginal_cost != 0).all()
    assert generators.start_up_cost.tolist() == pytest.approx([1500, 2000, 3000])
    assert "POWERMCP.PYPSA.CONSTANT_COST_DROPPED" in _diagnostic_codes(result)


def test_pypsa_import_applies_generator_voltage_targets_to_buses(tmp_path):
    out = tmp_path / "voltage-targets.nc"
    result = pypsa_mcp.import_case_from_any(str(CASE9), str(out))
    assert result["status"] == "success", result

    network = pypsa.Network(str(out))
    assert network.buses.loc[["1", "2", "3"], "v_mag_pu_set"].tolist() == pytest.approx(
        [1.04, 1.025, 1.025]
    )


def test_pypsa_create_network_uses_default_snapshot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = pypsa_mcp.create_network("empty")

    assert result["status"] == "success", result
    network = pypsa.Network(result["network_file"])
    assert len(network.snapshots) == 1


def _write_feasible_pypsa_network(path: Path, *, extendable: bool = False) -> None:
    network = pypsa.Network()
    network.add("Bus", "bus")
    network.add("Load", "load", bus="bus", p_set=10.0)
    network.add(
        "Generator",
        "generator",
        bus="bus",
        carrier="gas",
        p_nom=0.0 if extendable else 20.0,
        p_nom_extendable=extendable,
        capital_cost=5.0,
        marginal_cost=10.0,
    )
    network.export_to_netcdf(path)


def test_pypsa_optimize_network_uses_modern_optimizer(tmp_path):
    path = tmp_path / "dispatch.nc"
    _write_feasible_pypsa_network(path)

    result = pypsa_mcp.optimize_network(str(path))

    assert result["status"] == "ok", result
    assert result["termination_condition"] == "optimal"
    assert result["objective"] == pytest.approx(100.0)
    assert result["generators"]["generator"]["p"] == pytest.approx(10.0)


def test_pypsa_optimize_investment_uses_modern_optimizer(tmp_path):
    path = tmp_path / "investment.nc"
    _write_feasible_pypsa_network(path, extendable=True)

    result = pypsa_mcp.optimize_investment(str(path), carriers=["gas"])

    assert result["status"] == "ok", result
    assert result["termination_condition"] == "optimal"
    assert result["investments"]["generators"]["generator"][
        "p_nom_opt"
    ] == pytest.approx(10.0)


def test_pypsa_legacy_optimizer_options_fail_clearly(tmp_path):
    path = tmp_path / "legacy-options.nc"
    path.write_bytes(b"")

    formulation = pypsa_mcp.optimize_network(str(path), formulation="angles")
    pyomo = pypsa_mcp.optimize_network(str(path), pyomo=True)

    assert formulation["status"] == "error"
    assert "legacy LOPF formulations" in formulation["message"]
    assert pyomo["status"] == "error"
    assert "legacy Pyomo" in pyomo["message"]


def test_pypsa_import_overwrite_zero_s_nom(tmp_path):
    src = tmp_path / "zero.m"
    src.write_text(ZERO_RATE_CASE)

    bare = pypsa_mcp.import_case_from_any(str(src), str(tmp_path / "bare.nc"))
    assert "POWERMCP.PYPSA.ZERO_BRANCH_RATING" in _diagnostic_codes(bare), bare

    out = tmp_path / "set.nc"
    r = pypsa_mcp.import_case_from_any(str(src), str(out), overwrite_zero_s_nom=100.0)
    assert "POWERMCP.PYPSA.ZERO_BRANCH_RATING_REPLACED" in _diagnostic_codes(r), r
    assert "POWERMCP.PYPSA.ZERO_BRANCH_RATING" not in _diagnostic_codes(r), r
    assert (pypsa.Network(str(out)).lines.s_nom == 100.0).all()


def test_pypsa_import_missing_file(tmp_path):
    r = pypsa_mcp.import_case_from_any("/nope/missing.m", str(tmp_path / "x.nc"))
    assert r["status"] == "error"
    assert "not found" in r["message"].lower()


def test_pypsa_network_name_is_confined(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.nc"
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(allowed))

    with pytest.raises(ValueError, match="outside allowed MCP roots"):
        pypsa_mcp.get_network_info(str(outside))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
def test_pypsa_csv_import_preflights_the_complete_tree(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    dataset = allowed / "network"
    outside = tmp_path / "outside.csv"
    dataset.mkdir(parents=True)
    outside.write_text("name\nsecret\n")
    (dataset / "buses.csv").symlink_to(outside)
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(allowed))

    result = pypsa_mcp.import_from_csv_folder(
        str(dataset), str(allowed / "network.nc")
    )
    assert result["status"] == "error"
    assert "outside its allowed MCP root" in result["message"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
def test_pypsa_network_read_preflights_a_csv_tree(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    dataset = allowed / "network"
    outside = tmp_path / "outside.csv"
    dataset.mkdir(parents=True)
    outside.write_text("name\nsecret\n")
    (dataset / "buses.csv").symlink_to(outside)
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(allowed))

    with pytest.raises(ValueError, match="outside its allowed MCP root"):
        pypsa_mcp.get_network_info(str(dataset))


def test_pypsa_csv_import_checks_an_explicit_output(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    dataset = allowed / "network"
    dataset.mkdir(parents=True)
    outside = tmp_path / "outside.nc"
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(allowed))

    result = pypsa_mcp.import_from_csv_folder(str(dataset), str(outside))

    assert result["status"] == "error"
    assert "outside allowed MCP roots" in result["message"]


def test_pypsa_csv_import_keeps_the_checked_legacy_default(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    dataset = allowed / "network"
    dataset.mkdir(parents=True)
    working = tmp_path / "working"
    working.mkdir()
    monkeypatch.chdir(working)
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(allowed))

    result = pypsa_mcp.import_from_csv_folder(str(dataset))

    assert result["status"] == "error"
    assert "outside allowed MCP roots" in result["message"]


def test_pypsa_csv_export_is_staged_and_preserves_unrelated_files(tmp_path):
    network_file = tmp_path / "network.nc"
    network = pypsa.Network()
    network.add("Bus", "bus")
    network.export_to_netcdf(network_file)

    output = tmp_path / "csv"
    output.mkdir()
    (output / "keep.txt").write_text("keep")
    result = pypsa_mcp.export_to_csv_folder(str(network_file), str(output))

    assert result["status"] == "success", result
    assert (output / "keep.txt").read_text() == "keep"
    assert (output / "buses.csv").is_file()


def test_registry_entry():
    t = TOOLS["powerio"]
    assert t.kind == "open-source"
    assert t.extra is None  # promoted to a core dependency (issue #30)
    assert t.windows_only is False
    assert t.probe == "powerio"
    # The server ships in powerio's own wheel, so there is no bundled dir here
    # and no local file enumerating powerio's tool surface.
    assert t.run_kind == "package"
    assert t.module == "powerio.mcp"
    assert t.server_dir is None
    with pytest.raises(ValueError, match="own distribution"):
        t.resolve_server_dir()
    assert not (Path(__file__).resolve().parents[1] / "powerio").exists()


@pytest.fixture()
def record_mcp_run(monkeypatch):
    calls = []

    def fake_run(self, *args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr("mcp.server.mcpserver.MCPServer.run", fake_run, raising=True)
    return calls


def test_launch_powerio_runs_once(record_mcp_run):
    from powermcp import runner

    runner.launch("powerio")
    assert len(record_mcp_run) == 1
    args, kwargs = record_mcp_run[0]
    # powerio's own entry point takes the SDK default rather than naming it.
    transport = kwargs.get("transport") or (args[0] if args else "stdio")
    assert transport == "stdio"


def test_inline_emit_stages_no_temp_files(monkeypatch):
    # Text emission stays in memory when no destination is supplied.
    import tempfile

    def boom(*args, **kwargs):
        raise AssertionError("inline conversion must not create temp files")

    monkeypatch.setattr(tempfile, "mkstemp", boom)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", boom)
    r = powerio_mcp.emit(
        format="psse", content=CASE9.read_text(), from_format="matpower"
    )
    assert r["text"]


# 3-bus case with an out-of-service branch (2-3, status 0) and an out-of-service
# generator (at bus 3, status 0), for the PyPSA/pandapower status tests.
OOS_CASE = """function mpc = oos
mpc.version = '2';
mpc.baseMVA = 100.0;
mpc.bus = [
\t1 3 0 0 0 0 1 1.0 0.0 230.0 1 1.1 0.9;
\t2 1 50 10 0 0 1 1.0 0.0 230.0 1 1.1 0.9;
\t3 1 30 5 0 0 1 1.0 0.0 230.0 1 1.1 0.9;
];
mpc.gen = [
\t1 80 0 50 -50 1.0 100 1 200 0 0 0 0 0 0 0 0 0 0 0 0;
\t3 20 0 50 -50 1.0 100 0 100 0 0 0 0 0 0 0 0 0 0 0 0;
];
mpc.branch = [
\t1 2 0.01 0.05 0.0 250 0 0 0 0 1 -360 360;
\t2 3 0.01 0.05 0.0 250 0 0 0 0 0 -360 360;
];
"""


def test_pypsa_import_preserves_out_of_service_branch(tmp_path):
    src = tmp_path / "oos.m"
    src.write_text(OOS_CASE)
    out = tmp_path / "oos.nc"
    r = pypsa_mcp.import_case_from_any(str(src), str(out))
    assert r["status"] == "success", r
    lines = pypsa.Network(str(out)).lines
    assert lines.shape[0] == 2
    assert lines.active.tolist().count(False) == 1


def test_pypsa_import_preserves_out_of_service_generator(tmp_path):
    src = tmp_path / "oos.m"
    src.write_text(OOS_CASE)
    r = pypsa_mcp.import_case_from_any(str(src), str(tmp_path / "g.nc"))
    assert r["status"] == "success", r
    generators = pypsa.Network(str(tmp_path / "g.nc")).generators
    assert generators.shape[0] == 2
    assert generators.active.tolist().count(False) == 1


def test_pandapower_bridge_honors_branch_status(tmp_path):
    # PowerIO's native pandapower writer keeps the OOS row and its status.
    panda_dir = str(TOOLS["pandapower"].resolve_server_dir())
    if panda_dir not in sys.path:
        sys.path.insert(0, panda_dir)
    import panda_mcp  # noqa: E402

    src = tmp_path / "oos.m"
    src.write_text(OOS_CASE)
    res = panda_mcp.load_network_from_any(str(src))
    assert res["status"] == "success", res
    in_service = panda_mcp._current_net.line["in_service"].tolist()
    assert len(in_service) == 2 and in_service.count(False) == 1, in_service


def test_pandapower_pickle_input_is_rejected_without_execution(tmp_path):
    panda_dir = str(TOOLS["pandapower"].resolve_server_dir())
    if panda_dir not in sys.path:
        sys.path.insert(0, panda_dir)
    import panda_mcp  # noqa: E402

    marker = tmp_path / "pickle-executed"

    class Payload:
        def __reduce__(self):
            statement = f"open({str(marker)!r}, 'w').write('executed')"
            return builtins.exec, (statement,)

    payload = tmp_path / "network.p"
    payload.write_bytes(pickle.dumps(Payload()))

    result = panda_mcp.load_network(str(payload))

    assert result["status"] == "error"
    assert "Use a .json file" in result["message"]
    assert not marker.exists()


def test_calc_matrix_laplacian():
    m = powerio_mcp.calc_matrix("laplacian", path=str(CASE9))
    assert m["format"] == "coo"
    assert m["shape"] == [9, 9]


def test_calc_matrix_bad_json_raises_valueerror():
    with pytest.raises(ValueError):
        powerio_mcp.calc_matrix("bprime", json="{not valid json")


def test_emit_unknown_format_maps_cleanly():
    with pytest.raises(ValueError):
        powerio_mcp.emit(format="no-such-format", path=str(CASE9))


def test_allowed_roots_rejects_read_outside_root(tmp_path, monkeypatch):
    # POWERIO_MCP_ALLOWED_ROOTS is unset for every other test in this file, so
    # `_check_allowed_path` is a no-op there; this is the one place the
    # containment check itself is exercised, on both the reject and admit side.
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside" / "case9.m"
    outside.parent.mkdir()
    outside.write_text(CASE9.read_text())
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(root))
    with pytest.raises(ValueError, match="outside allowed MCP roots"):
        powerio_mcp.parse(path=str(outside))


def test_allowed_roots_admits_read_inside_root(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    case = root / "case9.m"
    case.write_text(CASE9.read_text())
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(root))
    r = powerio_mcp.parse(path=str(case))
    assert r["schema"] == "powerio.parse"


def test_allowed_roots_rejects_write_outside_root(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    outside_out = tmp_path / "outside" / "case9.raw"
    outside_out.parent.mkdir()
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(root))
    with pytest.raises(ValueError, match="outside allowed MCP roots"):
        powerio_mcp.emit(
            destination=str(outside_out), content=CASE9.read_text(), format="psse"
        )


def test_allowed_roots_admits_write_inside_root(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(root))
    out = root / "case9.raw"
    r = powerio_mcp.emit(destination=str(out), content=CASE9.read_text(), format="psse")
    assert r["path"] == str(out)
    assert out.exists()


def test_unreadable_file_maps_cleanly(tmp_path):
    # PermissionError must surface as the documented ValueError shape, like
    # FileNotFoundError, not leak raw through the tool. (Ported from the
    # canonical server's suite at powerio 0.1.1.)
    import os

    if sys.platform == "win32" or os.geteuid() == 0:
        pytest.skip("permission bits are not enforceable here")
    locked = tmp_path / "locked.m"
    locked.write_text("function mpc = x\n")
    locked.chmod(0o000)
    try:
        with pytest.raises(ValueError, match="cannot read input"):
            powerio_mcp.emit(format="psse", path=str(locked))
        with pytest.raises(ValueError, match="cannot read input"):
            powerio_mcp.summarize(path=str(locked))
    finally:
        locked.chmod(0o644)


def test_wrong_schema_json_maps_cleanly():
    # Wrong schema but well formed JSON keeps the same coded ValueError shape;
    # malformed JSON is covered above.
    for bad in ("{}", "[]", "null", '{"buses": "nope"}'):
        with pytest.raises(ValueError, match=r"PARSE\.SOURCE\.MALFORMED"):
            powerio_mcp.calc_matrix("bprime", json=bad, json_format="model-json")


def test_model_json_format_token_is_explicitly_accepted():
    transport = powerio_mcp.parse(path=str(CASE9))["json"]
    m = powerio_mcp.calc_matrix(
        "bprime", json=transport, json_format="model-json"
    )
    assert m["shape"] == [9, 9]


# ---------------------------------------------------------------------------
# ANDES bridge tests
#
# The andes_mcp fixture (shared with test_andes_server.py) lives in
# conftest.py; it skips via pytest.importorskip("andes") when andes isn't
# installed.
# ---------------------------------------------------------------------------

def test_andes_load_network_from_any(tmp_path, andes_mcp):
    out = tmp_path / "case9.m"
    r = andes_mcp.load_network_from_any(str(CASE9), str(out))
    assert r["status"] == "success", r
    assert out.exists()
    assert r["case_file"] == str(out)
    assert r["info"]["buses"] == 9


def test_andes_load_network_from_json(tmp_path, andes_mcp):
    transport = powerio_mcp.parse(path=str(CASE9))["json"]
    out = tmp_path / "case9_from_json.m"
    r = andes_mcp.load_network_from_json(transport, str(out))
    assert r["status"] == "success", r
    assert out.exists()
    assert r["info"]["buses"] == 9


def test_andes_preserves_powerio_error_codes(tmp_path, andes_mcp):
    result = andes_mcp.load_network_from_json("{not JSON", str(tmp_path / "bad.m"))

    assert result["status"] == "error"
    assert result["code"] == "REQUEST.FORMAT.UNKNOWN"
    assert "REQUEST.FORMAT.UNKNOWN" in result["message"]


def test_andes_interchange_accepts_static_module(tmp_path, andes_mcp):
    out = tmp_path / "case9_module.m"

    result = andes_mcp.load_network_from_json(_diagnostic_module_json(), str(out))

    _assert_structured_adapter_response(result)
    assert out.is_file()


def test_andes_requires_powerio_collection_export(tmp_path, andes_mcp):
    parsed = json.loads(_module_json(powerio.parse_file(CASE9)))
    collection_json = json.dumps(
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
                            "probability": 1.0,
                            "value": parsed["value"]["data"],
                        }
                    ]
                },
            },
        }
    )

    rejected = andes_mcp.load_network_from_json(
        collection_json, str(tmp_path / "unselected.m")
    )

    assert rejected["status"] == "error"
    assert "list_states" in rejected["message"]
    assert "export_state" in rejected["message"]


def test_andes_load_missing_file(tmp_path, andes_mcp):
    r = andes_mcp.load_network_from_any("/nope/missing.m", str(tmp_path / "x.m"))
    assert r["status"] == "error"
    assert "not found" in r["message"].lower()


# ---------------------------------------------------------------------------
# pandapower-json plus folder and Parquet formats routed through generic verbs.
# ---------------------------------------------------------------------------

def test_emit_to_pandapower_json():
    r = powerio_mcp.emit(format="pandapower-json", path=str(CASE9))
    assert r["text"]
    assert json.loads(r["text"])  # well-formed JSON


def test_pandapower_json_round_trips_through_transport():
    # pandapower-json is a plain text format, so it flows through emit and parse.
    transport = powerio_mcp.parse(path=str(CASE9))["json"]
    out = powerio_mcp.parse(
        content=powerio_mcp.emit(format="pandapower-json", path=str(CASE9))[
            "text"
        ],
        from_format="pandapower-json",
    )
    assert json.loads(out["json"])
    assert json.loads(transport)


def test_pypsa_csv_folder_round_trip(tmp_path):
    # pypsa-csv is a directory format: emit to a folder, then parse the folder.
    out_dir = tmp_path / "pypsa_csv"
    w = powerio_mcp.emit(format="pypsa-csv", destination=str(out_dir), path=str(CASE9))
    assert w["files"], w
    assert (out_dir / "buses.csv").exists()
    r = powerio_mcp.parse(path=str(out_dir))
    assert r["summary"]["elements"]["buses"] == 9
    assert json.loads(r["json"])


def test_pypsa_csv_folder_accepts_transport(tmp_path):
    transport = powerio_mcp.parse(path=str(CASE9))["json"]
    out_dir = tmp_path / "from_json"
    w = powerio_mcp.emit(format="pypsa-csv", destination=str(out_dir), json=transport)
    assert (out_dir / "generators.csv").exists(), w


def test_read_pypsa_csv_missing_folder_maps_cleanly(tmp_path):
    with pytest.raises(ValueError):
        powerio_mcp.parse(path=str(tmp_path / "nope"))


def test_gridfm_round_trip(tmp_path):
    out_dir = tmp_path / "gfm"
    w = powerio_mcp.emit(format="gridfm", destination=str(out_dir), path=str(CASE9))
    assert w["files"], w
    r = powerio_mcp.parse(
        path=str(out_dir), from_format="gridfm", options={"scenario": 0}
    )
    assert r["summary"]["elements"]["buses"] == 9
    assert json.loads(r["json"])


def test_gridfm_missing_dir_maps_cleanly(tmp_path):
    with pytest.raises(ValueError):
        powerio_mcp.parse(path=str(tmp_path / "nope"), from_format="gridfm")


# ---------------------------------------------------------------------------
# PowerWorld .pwd display files. display is provided by the canonical
# powerio.mcp.server; these tests exercise the re-exported tool.
# ---------------------------------------------------------------------------

def test_display_decodes_pwd():
    r = powerio_mcp.display(str(ACTIVSG200_PWD))
    assert r["schema"] == "powerio.display"
    assert r["powerio_version"] == powerio.__version__
    assert r["domain"] == "display"
    assert r["model"] == "display"
    assert r["source_format"] == "powerworld-pwd"
    assert r["canvas"]["width"] > 0 and r["canvas"]["height"] > 0
    subs = r["substations"]
    assert subs, "expected at least one substation"
    assert all(set(s) == {"number", "name", "x", "y"} for s in subs)
    assert any(s["name"] for s in subs)
    assert all(
        isinstance(s["x"], (int, float)) and isinstance(s["y"], (int, float))
        for s in subs
    )


def test_read_display_missing_file_maps_cleanly(tmp_path):
    with pytest.raises(ValueError):
        powerio_mcp.display(str(tmp_path / "nope.pwd"))


def test_read_display_garbage_file_maps_cleanly(tmp_path):
    bad = tmp_path / "garbage.pwd"
    bad.write_bytes(b"not a real display file\x00\x01\x02")
    with pytest.raises(ValueError):
        powerio_mcp.display(str(bad))


# ---------------------------------------------------------------------------
# OpenDSS consumes DSS files produced by PowerIO.
# ---------------------------------------------------------------------------

def _load_opendss_configuration(monkeypatch):
    opendss_dir = Path(__file__).resolve().parents[1] / "OpenDSS"
    monkeypatch.syspath_prepend(str(opendss_dir))

    fake_config = types.SimpleNamespace(
        compile_dss=lambda _path: None,
        circuit_readiness=lambda: {"ready": True},
    )
    fake_tools = types.SimpleNamespace(
        update_dss=lambda _dss: None,
        configuration=fake_config,
    )
    fake_dss_interface = types.SimpleNamespace(DSS=lambda: object())
    monkeypatch.setitem(
        sys.modules, "py_dss_toolkit", types.SimpleNamespace(dss_tools=fake_tools)
    )
    monkeypatch.setitem(sys.modules, "py_dss_interface", fake_dss_interface)

    for name in (
        "opendss_tools.configuration",
        "core.engine",
        "core.state",
        "utils.responses",
    ):
        sys.modules.pop(name, None)
    return importlib.import_module("opendss_tools.configuration")


def test_opendss_registration_excludes_distribution_wrapper(monkeypatch):
    configuration = _load_opendss_configuration(monkeypatch)
    from mcp.server.mcpserver import MCPServer as FastMCP

    mcp = FastMCP("opendss-test")
    configuration.register_configuration_tools(mcp)
    names = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert "compile_opendss_file" in names
    assert "clear_all_opendss_memory" in names
    assert "compile_distribution" not in names


def test_powerio_to_opendss_composition(monkeypatch, tmp_path):
    configuration = _load_opendss_configuration(monkeypatch)

    dss_path = tmp_path / "feeder.dss"
    save_result = powerio_mcp.emit(
        format="dss",
        destination=str(dss_path),
        path=str(OPENDSS_CASE),
    )
    assert save_result["path"] == str(dss_path)
    assert dss_path.exists()

    result = configuration.compile_opendss_file(str(dss_path))
    assert result["success"] is True
    assert result["payload"]["dss_file"] == str(dss_path)


def test_opendss_without_containment_does_not_scan_the_parent_tree(
    monkeypatch, tmp_path
):
    configuration = _load_opendss_configuration(monkeypatch)
    dss_path = tmp_path / "feeder.dss"
    dss_path.write_text("Clear")
    monkeypatch.setattr(configuration, "allowed_roots", lambda: ())
    monkeypatch.setattr(
        configuration,
        "checked_read_tree",
        lambda *_a, **_k: pytest.fail("unconfigured OpenDSS must not scan siblings"),
    )

    result = configuration.compile_opendss_file(str(dss_path))

    assert result["success"] is True
