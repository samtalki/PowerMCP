"""The powerio server driven the way a model drives it: over stdio, through
the MCP SDK.

Every other powerio test calls the tool functions in process, which skips the
SDK's argument handling entirely. That gap hid a real defect: the SDK rewrites
a string argument whose text parses as JSON into the parsed object before
validation, so every `json` / `content` / `package_json` argument carrying JSON
is destroyed before the tool sees it. Nothing in an in-process suite can see
that, which is why this file drives the real transport.

Launching through `python -m powermcp run powerio` also covers the runner and
registry wiring end to end, so a broken launch fails here rather than only for
a user.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("powerio", minversion="0.9.0")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

CASE9 = Path(__file__).resolve().parent / "data" / "case9.m"


def _run(steps):
    """Drive one stdio session, returning whatever ``steps`` returns.

    ``steps`` is an async callable taking the initialized session.
    """

    async def go():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "powermcp", "run", "powerio"],
            env=dict(os.environ),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await steps(session)

    return asyncio.run(go())


def _payload(result):
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def test_launch_serves_the_canonical_tool_surface():
    async def steps(session):
        return sorted(t.name for t in (await session.list_tools()).tools)

    assert _run(steps) == [
        "convert",
        "diagnostics",
        "display",
        "matrix",
        "normalize",
        "parse",
        "save",
        "summary",
    ]


def test_a_path_argument_survives_the_transport():
    async def steps(session):
        return _payload(await session.call_tool("summary", {"path": str(CASE9)}))

    summary = _run(steps)
    assert summary["schema"] == "powerio.summary"
    assert summary["elements"]["buses"] == 9


def test_non_json_content_survives_the_transport():
    # MATPOWER text does not parse as JSON, so the SDK leaves it alone. This is
    # the control for the two xfails below.
    async def steps(session):
        return _payload(
            await session.call_tool(
                "convert",
                {
                    "to_format": "psse",
                    "content": CASE9.read_text(),
                    "from_format": "matpower",
                },
            )
        )

    assert _run(steps)["text"]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "the MCP SDK parses a JSON-looking string into an object before "
        "validating any argument not annotated exactly `str`, so every "
        "Optional[str] transport argument on the powerio server is unusable "
        "over a real transport"
    ),
)
def test_the_json_transport_round_trips_over_the_transport():
    async def steps(session):
        parsed = _payload(await session.call_tool("parse", {"path": str(CASE9)}))
        assert parsed["json_format"] == "model-json"
        return _payload(
            await session.call_tool(
                "summary", {"json": parsed["json"], "json_format": "model-json"}
            )
        )

    assert _run(steps)["elements"]["buses"] == 9


@pytest.mark.xfail(strict=True, reason="same SDK argument rewriting as above")
def test_the_package_transport_reaches_summary_over_the_transport():
    # `diagnostics` takes `package_json` as a required bare `str` and does work,
    # so the same package text is accepted by one tool and refused by another.
    async def steps(session):
        parsed = _payload(
            await session.call_tool(
                "parse", {"path": str(CASE9), "transport": "package"}
            )
        )
        package = parsed["package_json"]
        assert not (
            await session.call_tool("diagnostics", {"package_json": package})
        ).is_error
        return _payload(
            await session.call_tool("summary", {"package_json": package})
        )

    assert _run(steps)["elements"]["buses"] == 9
