"""Exercise PowerIO's MCP server through the real stdio transport.

The in process consumer tests bypass SDK argument handling. These tests cover
the runner, registry entry, MCP schemas, and JSON text transport together.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from packaging.version import Version

powerio = pytest.importorskip("powerio")
assert Version("1.0.0") <= Version(powerio.__version__) < Version("2.0.0")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

CASE9 = Path(__file__).resolve().parent / "data" / "case9.m"
TIMEOUT = 60.0
LOWERABLE_DSS = """Clear
Set DefaultBaseFrequency=60
New Circuit.tiny basekv=12.47 pu=1.0 phases=3 bus1=src MVAsc3=2000 MVAsc1=2100
New Transformer.t1 phases=3 windings=2 buses=(src, sec) conns=(delta, wye) kvs=(12.47, 0.416) kvas=(500, 300) %Rs=(0.5, 0.5) xhl=6
New Load.l1 bus1=sec phases=3 conn=wye kv=0.416 kw=90 pf=0.95 model=1
Set VoltageBases=[12.47, 0.416]
"""


def _run(steps):
    """Drive one initialized stdio session and return the step result."""

    async def go():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "powermcp", "run", "powerio"],
            env=dict(os.environ),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=TIMEOUT) as session:
                await asyncio.wait_for(session.initialize(), TIMEOUT)
                return await steps(session)

    return asyncio.run(go())


def _payload(result):
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def test_launch_serves_the_powerio_1_tool_surface():
    async def steps(session):
        return sorted(tool.name for tool in (await session.list_tools()).tools)

    assert set(_run(steps)) == {
        "about",
        "calc_matrix",
        "diagnostics",
        "display",
        "emit",
        "export_state",
        "inspect",
        "inspect_state",
        "list_states",
        "parse",
        "summarize",
        "to_balanced",
        "to_balanced_report",
        "to_normalized",
    }


def test_a_path_argument_survives_the_transport():
    async def steps(session):
        return _payload(await session.call_tool("summarize", {"path": str(CASE9)}))

    summary = _run(steps)
    assert summary["schema"] == "powerio.summarize"
    assert summary["elements"]["buses"] == 9
    assert isinstance(summary["diagnostics"], list)


def test_non_json_content_survives_the_transport():
    async def steps(session):
        return _payload(
            await session.call_tool(
                "emit",
                {
                    "format": "psse",
                    "content": CASE9.read_text(),
                    "from_format": "matpower",
                },
            )
        )

    result = _run(steps)
    assert result["text"]
    assert isinstance(result["diagnostics"], list)


def test_model_json_round_trips_over_the_transport():
    async def steps(session):
        parsed = _payload(await session.call_tool("parse", {"path": str(CASE9)}))
        assert parsed["json_format"] == "model-json"
        return _payload(
            await session.call_tool(
                "summarize",
                {"json": parsed["json"], "json_format": "model-json"},
            )
        )

    assert _run(steps)["elements"]["buses"] == 9


def test_json_shaped_content_survives_the_transport():
    async def steps(session):
        emitted = _payload(
            await session.call_tool(
                "emit", {"format": "pandapower-json", "path": str(CASE9)}
            )
        )
        return _payload(
            await session.call_tool(
                "parse",
                {
                    "content": emitted["text"],
                    "from_format": "pandapower-json",
                    "transport": "module",
                },
            )
        )

    parsed = _run(steps)
    assert parsed["transport"] == "module"
    assert parsed["summary"]["elements"]["buses"] == 9


def test_module_transport_reaches_module_tools_and_emit():
    async def steps(session):
        parsed = _payload(
            await session.call_tool(
                "parse", {"path": str(CASE9), "transport": "module"}
            )
        )
        module_json = parsed["module_json"]
        inspected = _payload(
            await session.call_tool("inspect", {"module_json": module_json})
        )
        diagnosed = _payload(
            await session.call_tool("diagnostics", {"module_json": module_json})
        )
        emitted = _payload(
            await session.call_tool(
                "emit", {"format": "matpower", "module_json": module_json}
            )
        )
        return parsed, inspected, diagnosed, emitted

    parsed, inspected, diagnosed, emitted = _run(steps)
    assert parsed["transport"] == "module"
    assert parsed["json_format"] == "module"
    assert inspected["kind"] == "balanced_network"
    assert diagnosed["model_kind"] == "balanced"
    assert isinstance(diagnosed["diagnostics"], list)
    assert emitted["text"]


def test_collection_discovery_and_export_round_trip_over_transport():
    async def steps(session):
        parsed = _payload(
            await session.call_tool(
                "parse", {"path": str(CASE9), "transport": "module"}
            )
        )
        base = json.loads(parsed["module_json"])
        collection_json = json.dumps(
            {
                "schema": "powerio.module",
                "version": 1,
                "producer": base["producer"],
                "value": {
                    "kind": "balanced_network_scenario_set",
                    "data": {
                        "scenarios": [
                            {
                                "id": "base",
                                "probability": 1.0,
                                "value": base["value"]["data"],
                            }
                        ]
                    },
                },
            }
        )
        listed = _payload(
            await session.call_tool(
                "list_states", {"module_json": collection_json}
            )
        )
        inspected = _payload(
            await session.call_tool(
                "inspect_state",
                {"module_json": collection_json, "scenario": "base"},
            )
        )
        exported = _payload(
            await session.call_tool(
                "export_state",
                {"module_json": collection_json, "scenario": "base"},
            )
        )
        summary = _payload(
            await session.call_tool(
                "summarize", {"module_json": exported["module_json"]}
            )
        )
        return listed, inspected, exported, summary

    listed, inspected, exported, summary = _run(steps)
    assert listed["keyed_by"] == "scenario"
    assert listed["scenarios"] == [{"id": "base", "probability": 1.0}]
    assert inspected["selected"]["item"] == "balanced_network"
    assert exported["kind"] == "balanced_network"
    assert summary["elements"]["buses"] == 9


def test_balanced_lowering_round_trips_over_transport():
    async def steps(session):
        parsed = _payload(
            await session.call_tool(
                "parse",
                {
                    "content": LOWERABLE_DSS,
                    "from_format": "dss",
                    "transport": "module",
                },
            )
        )
        module_json = parsed["module_json"]
        report = _payload(
            await session.call_tool(
                "to_balanced_report", {"module_json": module_json}
            )
        )
        lowered = _payload(
            await session.call_tool("to_balanced", {"module_json": module_json})
        )
        summary = _payload(
            await session.call_tool(
                "summarize", {"module_json": lowered["module_json"]}
            )
        )
        return report, lowered, summary

    report, lowered, summary = _run(steps)
    assert report["ready"] is True
    assert isinstance(report["diagnostics"], list)
    assert lowered["kind"] == "balanced_network"
    assert summary["domain"] == "transmission"
