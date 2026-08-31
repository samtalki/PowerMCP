"""Tellegen's three tool PowerIO module adapter."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import jsonschema
import powerio
import pytest

from powermcp import config as cfg
from powermcp.registry import TOOLS

_TELLEGEN_DIR = str(TOOLS["tellegen"].resolve_server_dir())
if _TELLEGEN_DIR not in sys.path:
    sys.path.insert(0, _TELLEGEN_DIR)

import tellegen_mcp

CASE9 = Path(__file__).with_name("data") / "case9.m"
BINARY = os.environ.get("POWERMCP_TELLEGEN_BINARY")
needs_binary = pytest.mark.skipif(not BINARY, reason="POWERMCP_TELLEGEN_BINARY not set")


def _contract() -> dict[str, Any]:
    module_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["schema", "version", "producer", "value"],
        "properties": {
            "schema": {"const": "powerio.module"},
            "version": {"const": 1},
            "producer": {"type": "object"},
            "value": {"type": "object"},
        },
        "additionalProperties": True,
    }
    spec_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["objective", "candidates", "exact_solve_budget"],
        "properties": {
            "objective": {"type": "object"},
            "candidates": {"type": "array", "items": {"type": "string"}},
            "exact_solve_budget": {"type": "integer", "minimum": 1},
        },
        "additionalProperties": True,
    }
    plan_request = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": {
            "StoredModuleV1": module_schema,
            "CapacityPlanSpec": spec_schema,
        },
        "type": "object",
        "required": ["module", "spec"],
        "properties": {
            "module": {"$ref": "#/$defs/StoredModuleV1"},
            "spec": {"$ref": "#/$defs/CapacityPlanSpec"},
        },
        "additionalProperties": False,
    }
    plan_response = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": {"StoredModuleV1": module_schema},
        "type": "object",
        "required": ["plan", "solution_module"],
        "properties": {
            "plan": {"type": "object"},
            "solution_module": {"$ref": "#/$defs/StoredModuleV1"},
        },
        "additionalProperties": False,
    }
    return {
        "contract": "tellegen.cli/1",
        "tellegen_version": "0.1.0-test",
        "powerio_version": "1.0.0-test",
        "schemas": {
            "powerio_module": module_schema,
            "capacity_plan_spec": spec_schema,
            "plan_request": plan_request,
            "plan_response": plan_response,
            "solve_response": module_schema,
            "capabilities_response": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "array",
                "items": {"type": "object"},
            },
        },
    }


@pytest.fixture(autouse=True)
def generated_contract(tmp_path, monkeypatch):
    path = tmp_path / "tellegen-contract.json"
    path.write_text(json.dumps(_contract()), encoding="utf-8")
    monkeypatch.setattr(tellegen_mcp, "_CONTRACT_PATH", path)


def _module(kind: str = "balanced_network") -> dict[str, Any]:
    return {
        "schema": "powerio.module",
        "version": 1,
        "producer": {"name": "test", "version": "1"},
        "value": {"kind": kind, "data": {}},
    }


def _spec() -> dict[str, Any]:
    return {
        "objective": {
            "kind": "weighted_lmp",
            "weights": [{"bus": 1, "weight": 1.0}],
        },
        "candidates": ["branches:0"],
        "exact_solve_budget": 1,
    }


async def _listed_tools():
    entry = tellegen_mcp.mcp.get_request_handler("tools/list")
    assert entry is not None
    return (await entry.handler(None, None)).tools


def test_registry_entry():
    tool = TOOLS["tellegen"]
    assert tool.kind == "open-source"
    assert tool.windows_only is False
    assert tool.run_kind == "script"
    assert tool.resolve_entry_script().is_file()
    assert tool.extra == "tellegen"
    assert [key.key for key in tool.config_keys] == ["binary"]
    assert tool.config_keys[0].validate == "file"


def test_env_var_resolves_binary(isolated_config, monkeypatch, tmp_path):
    binary = tmp_path / "tellegen"
    binary.write_text("", encoding="utf-8")
    monkeypatch.setenv("POWERMCP_TELLEGEN_BINARY", str(binary))
    assert cfg.get_path("tellegen", "binary") == str(binary)


def test_unconfigured_binary_raises_config_error(isolated_config):
    with pytest.raises(cfg.ConfigError, match="tellegen.binary"):
        cfg.get_path("tellegen", "binary")


def test_server_registers_only_the_three_contract_tools():
    tools = asyncio.run(_listed_tools())
    assert [tool.name for tool in tools] == [
        "capabilities",
        "solve_dc_opf",
        "plan_capacity",
    ]
    assert not {"replay_experiment", "approve_experiment"} & {
        tool.name for tool in tools
    }

    solve = next(tool for tool in tools if tool.name == "solve_dc_opf")
    assert set(solve.input_schema["properties"]) == {"module"}
    plan = next(tool for tool in tools if tool.name == "plan_capacity")
    assert plan.input_schema == _contract()["schemas"]["plan_request"]

    for tool in tools:
        assert tool.output_schema is not None
        jsonschema.Draft202012Validator(tool.output_schema).validate(
            {"status": "error", "message": "failed"}
        )


def test_contract_comparison_preserves_json_scalar_types():
    assert tellegen_mcp._canonical_json({"default": True}) != (
        tellegen_mcp._canonical_json({"default": 1})
    )
    assert tellegen_mcp._canonical_json({"default": 60}) != (
        tellegen_mcp._canonical_json({"default": 60.0})
    )


def test_contract_is_checked_again_for_the_same_binary(monkeypatch):
    calls = []

    async def fake_run(binary, args, stdin_text, **_kwargs):
        calls.append((binary, args, stdin_text))
        return json.dumps(_contract())

    monkeypatch.setattr(tellegen_mcp, "_run_process", fake_run)
    asyncio.run(tellegen_mcp._verify_contract("tellegen", _contract()))
    asyncio.run(tellegen_mcp._verify_contract("tellegen", _contract()))
    assert calls == [
        ("tellegen", ["contract"], ""),
        ("tellegen", ["contract"], ""),
    ]


def test_contract_mismatch_is_refused(monkeypatch):
    actual = _contract()
    actual["powerio_version"] = "different"

    async def fake_run(_binary, _args, _stdin, **_kwargs):
        return json.dumps(actual)

    monkeypatch.setattr(tellegen_mcp, "_run_process", fake_run)
    with pytest.raises(RuntimeError, match="does not match"):
        asyncio.run(tellegen_mcp._verify_contract("tellegen", _contract()))


def test_success_results_are_the_raw_generated_values(monkeypatch):
    solution = _module("dc_opf_solution")
    capability_rows = [{"formulation": "dcopf", "available": True}]
    planned = {"plan": {"proposal": []}, "solution_module": solution}

    async def fake_run(args, _stdin):
        if args == ["capabilities"]:
            return json.dumps(capability_rows)
        if args == ["solve-module"]:
            return json.dumps(solution)
        if args == ["plan"]:
            return json.dumps(planned)
        raise AssertionError(args)

    monkeypatch.setattr(tellegen_mcp, "_run_tellegen", fake_run)

    capabilities = asyncio.run(tellegen_mcp.capabilities())
    solved = asyncio.run(tellegen_mcp.solve_dc_opf(_module()))
    plan = asyncio.run(tellegen_mcp.plan_capacity(_module(), _spec()))

    assert capabilities["results"] == capability_rows
    assert solved["results"] == solution
    assert plan["results"] == planned
    assert "capabilities" not in capabilities["results"][0]
    assert "solution_module" not in solved["results"]


def test_cli_powerio_error_text_is_not_reclassified(monkeypatch):
    message = "tellegen: invalid stored module: STORE.VERSION_UNSUPPORTED"

    async def fake_run(_args, _stdin):
        raise RuntimeError(message)

    monkeypatch.setattr(tellegen_mcp, "_run_tellegen", fake_run)
    result = asyncio.run(tellegen_mcp.solve_dc_opf(_module()))
    assert result == {"status": "error", "message": f"solve_dc_opf failed: {message}"}


def test_cancelled_process_is_terminated(monkeypatch):
    processes = []
    create = asyncio.create_subprocess_exec

    async def recording_create(*args, **kwargs):
        process = await create(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recording_create)

    async def run():
        task = asyncio.create_task(
            tellegen_mcp._run_process(
                sys.executable,
                ["-c", "import time; time.sleep(30)"],
                "",
            )
        )
        while not processes:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert processes[0].returncode is not None

    asyncio.run(run())


def test_timed_out_process_is_terminated(monkeypatch):
    processes = []
    create = asyncio.create_subprocess_exec

    async def recording_create(*args, **kwargs):
        process = await create(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recording_create)

    async def run():
        with pytest.raises(tellegen_mcp.TellegenTimeout, match="0.05 s timeout"):
            await tellegen_mcp._run_process(
                sys.executable,
                ["-c", "import time; time.sleep(30)"],
                "",
                timeout_s=0.05,
            )
        assert processes[0].returncode is not None

    asyncio.run(run())


@needs_binary
def test_runtime_contract_matches_the_bundled_artifact(monkeypatch):
    monkeypatch.setattr(
        tellegen_mcp,
        "_CONTRACT_PATH",
        Path(tellegen_mcp.__file__).with_name("tellegen-contract.json"),
    )
    assert BINARY is not None
    asyncio.run(
        tellegen_mcp._verify_contract(
            BINARY,
            tellegen_mcp._load_contract_artifact(),
        )
    )


@needs_binary
def test_runtime_commands_return_the_generated_raw_shapes(monkeypatch):
    monkeypatch.setattr(
        tellegen_mcp,
        "_CONTRACT_PATH",
        Path(tellegen_mcp.__file__).with_name("tellegen-contract.json"),
    )
    module = json.loads(powerio.parse_file(CASE9).emit("pio-json").text)
    spec = {
        "objective": {
            "kind": "weighted_lmp",
            "weights": [{"bus": 1, "weight": 1.0}],
        },
        "candidates": ["branches:0"],
        "max_increase_per_branch_mw": 10.0,
        "budget_mw": 10.0,
        "increment_mw": 5.0,
        "max_changed_lines": 1,
        "exact_solve_budget": 1,
    }

    capability_result = asyncio.run(tellegen_mcp.capabilities())
    solve_result = asyncio.run(tellegen_mcp.solve_dc_opf(module))
    plan_result = asyncio.run(tellegen_mcp.plan_capacity(module, spec))

    assert capability_result["status"] == "success"
    assert isinstance(capability_result["results"], list)
    assert solve_result["status"] == "success", solve_result["message"]
    assert solve_result["results"]["value"]["kind"] == "dc_opf_solution"
    assert plan_result["status"] == "success", plan_result["message"]
    assert set(plan_result["results"]) == {"plan", "solution_module"}
    assert plan_result["results"]["solution_module"]["value"]["kind"] == (
        "dc_opf_solution"
    )

    _inputs, outputs = tellegen_mcp._tool_schemas()
    for name, result in (
        ("capabilities", capability_result),
        ("solve_dc_opf", solve_result),
        ("plan_capacity", plan_result),
    ):
        jsonschema.Draft202012Validator(outputs[name]).validate(result)
