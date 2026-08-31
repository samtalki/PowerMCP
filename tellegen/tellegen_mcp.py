"""Expose Tellegen's PowerIO OPF boundary through MCP."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import jsonschema
import mcp_types as types
from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server

_TIMEOUT_S = 120.0
_TERMINATE_GRACE_S = 2.0
_CONTRACT_ID = "tellegen.cli/1"
_CONTRACT_PATH = Path(__file__).with_name("tellegen-contract.json")
_SCHEMA_NAMES = (
    "powerio_module",
    "capacity_plan_spec",
    "plan_request",
    "plan_response",
    "solve_response",
    "capabilities_response",
)
_TOOL_NAMES = ("capabilities", "solve_dc_opf", "plan_capacity")


class TellegenTimeout(RuntimeError):
    """Raised when a Tellegen CLI command exceeds the configured timeout."""


def _canonical_json(value: Any) -> bytes:
    """Encode JSON without changing scalar types or depending on key order."""
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _load_contract_artifact() -> dict[str, Any]:
    try:
        artifact = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read {_CONTRACT_PATH.name}: {exc}") from exc
    if not isinstance(artifact, dict):
        raise TypeError(f"{_CONTRACT_PATH.name} must contain a JSON object")
    if artifact.get("contract") != _CONTRACT_ID:
        raise RuntimeError(f"{_CONTRACT_PATH.name} does not declare {_CONTRACT_ID}")
    for field in ("tellegen_version", "powerio_version"):
        if not isinstance(artifact.get(field), str) or not artifact[field]:
            raise TypeError(f"{_CONTRACT_PATH.name} has no {field}")
    schemas = artifact.get("schemas")
    if not isinstance(schemas, dict):
        raise TypeError(f"{_CONTRACT_PATH.name} has no schemas object")
    for name in _SCHEMA_NAMES:
        schema = schemas.get(name)
        if not isinstance(schema, dict):
            raise TypeError(f"{_CONTRACT_PATH.name} has no {name} schema")
        jsonschema.Draft202012Validator.check_schema(schema)
    return artifact


def _embed_schema(
    schema: dict[str, Any], definition_name: str
) -> tuple[dict[str, Any], dict[str, str]]:
    """Move a generated document schema under one enclosing root."""
    body = copy.deepcopy(schema)
    body.pop("$schema", None)
    definitions = body.pop("$defs", {})
    if not isinstance(definitions, dict):
        raise TypeError(f"{definition_name} schema has a non-object $defs")
    if definition_name in definitions:
        raise TypeError(f"{definition_name} collides with a generated definition")
    definitions[definition_name] = body
    return definitions, {"$ref": f"#/$defs/{definition_name}"}


def _solve_input_schema(module_schema: dict[str, Any]) -> dict[str, Any]:
    definitions, module_ref = _embed_schema(module_schema, "PowerIoModule")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": definitions,
        "type": "object",
        "required": ["module"],
        "properties": {"module": module_ref},
        "additionalProperties": False,
    }


def _output_envelope_schema(
    response_schema: dict[str, Any], definition_name: str
) -> dict[str, Any]:
    definitions, response_ref = _embed_schema(response_schema, definition_name)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": definitions,
        "type": "object",
        "oneOf": [
            {
                "type": "object",
                "required": ["status", "message", "results"],
                "properties": {
                    "status": {"const": "success"},
                    "message": {"type": "string"},
                    "results": response_ref,
                },
                "additionalProperties": False,
            },
            {
                "type": "object",
                "required": ["status", "message"],
                "properties": {
                    "status": {"const": "error"},
                    "message": {"type": "string"},
                },
                "additionalProperties": False,
            },
        ],
    }


def _tool_schemas() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    schemas = _load_contract_artifact()["schemas"]
    inputs = {
        "capabilities": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "solve_dc_opf": _solve_input_schema(schemas["powerio_module"]),
        "plan_capacity": copy.deepcopy(schemas["plan_request"]),
    }
    outputs = {
        "capabilities": _output_envelope_schema(
            schemas["capabilities_response"], "CapabilitiesResponse"
        ),
        "solve_dc_opf": _output_envelope_schema(
            schemas["solve_response"], "SolveResponse"
        ),
        "plan_capacity": _output_envelope_schema(
            schemas["plan_response"], "PlanResponse"
        ),
    }
    for schema in (*inputs.values(), *outputs.values()):
        jsonschema.Draft202012Validator.check_schema(schema)
    return inputs, outputs


def _resolve_binary() -> str:
    """Resolve the Tellegen CLI configured by PowerMCP or the environment."""
    try:
        from powermcp.config import get_path
    except ImportError:
        value = os.environ.get("POWERMCP_TELLEGEN_BINARY")
        if not value:
            raise RuntimeError(
                "tellegen is not configured; set POWERMCP_TELLEGEN_BINARY"
            ) from None
        path = Path(value).expanduser()
        if not path.exists():
            raise RuntimeError(
                f"POWERMCP_TELLEGEN_BINARY points at a path that does not exist: {path}"
            )
        return str(path)
    return get_path("tellegen", "binary")


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=_TERMINATE_GRACE_S)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            return
        await process.wait()


async def _run_process(
    binary: str,
    args: list[str],
    stdin_text: str,
    *,
    timeout_s: float = _TIMEOUT_S,
) -> str:
    """Run one CLI command and stop its child on timeout or cancellation."""
    try:
        process = await asyncio.create_subprocess_exec(
            binary,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise RuntimeError(f"cannot launch tellegen: {exc}") from exc

    command = " ".join(("tellegen", *args))
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(stdin_text.encode()),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError as exc:
        await _stop_process(process)
        raise TellegenTimeout(
            f"{command} exceeded the {timeout_s:g} s timeout"
        ) from exc
    except asyncio.CancelledError:
        await _stop_process(process)
        raise

    if process.returncode != 0:
        message = stderr.decode(errors="replace").strip()
        raise RuntimeError(
            message or f"{command} exited with code {process.returncode}"
        )
    return stdout.decode(errors="strict")


async def _verify_contract(binary: str, expected: dict[str, Any]) -> None:
    """Require the CLI to match the bundled contract on every invocation."""
    try:
        actual = json.loads(await _run_process(binary, ["contract"], ""))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"tellegen contract returned invalid JSON: {exc}") from exc
    if not isinstance(actual, dict):
        raise TypeError("tellegen contract did not return an object")
    if actual.get("contract") != _CONTRACT_ID:
        raise RuntimeError(
            "unsupported tellegen CLI contract: "
            f"{actual.get('contract')!r}; expected {_CONTRACT_ID!r}"
        )
    expected_bytes = _canonical_json(expected)
    actual_bytes = _canonical_json(actual)
    if actual_bytes != expected_bytes:
        expected_sha256 = hashlib.sha256(expected_bytes).hexdigest()
        actual_sha256 = hashlib.sha256(actual_bytes).hexdigest()
        raise RuntimeError(
            "the configured tellegen binary does not match the bundled CLI contract "
            f"(expected SHA-256 {expected_sha256}, got {actual_sha256})"
        )


async def _run_tellegen(args: list[str], stdin_text: str) -> str:
    binary = _resolve_binary()
    expected = _load_contract_artifact()
    await _verify_contract(binary, expected)
    return await _run_process(binary, args, stdin_text)


def _ok(message: str, results: Any) -> dict[str, Any]:
    return {"status": "success", "message": message, "results": results}


def _err(message: str) -> dict[str, Any]:
    return {"status": "error", "message": message}


def _validation_message(prefix: str, error: jsonschema.ValidationError) -> str:
    location = ".".join(str(part) for part in error.absolute_path)
    where = f" at {location}" if location else ""
    return f"{prefix}{where}: {error.message}"


def _decode_response(
    text: str,
    schema_name: str,
    operation: str,
) -> Any:
    value = json.loads(text)
    schema = _load_contract_artifact()["schemas"][schema_name]
    try:
        jsonschema.Draft202012Validator(schema).validate(value)
    except jsonschema.ValidationError as exc:
        raise ValueError(
            _validation_message(f"{operation} result schema mismatch", exc)
        ) from exc
    return value


async def capabilities() -> dict[str, Any]:
    """Return Tellegen's formulation and differentiation capabilities."""
    try:
        value = _decode_response(
            await _run_tellegen(["capabilities"], ""),
            "capabilities_response",
            "capabilities",
        )
        return _ok("Returned Tellegen capabilities.", value)
    except TellegenTimeout as exc:
        return _err(str(exc))
    except (json.JSONDecodeError, RuntimeError, TypeError, ValueError) as exc:
        return _err(f"capabilities failed: {exc}")


async def solve_dc_opf(module: dict[str, Any]) -> dict[str, Any]:
    """Solve a PowerIO balanced network or DC OPF instance with Tellegen."""
    try:
        value = _decode_response(
            await _run_tellegen(
                ["solve-module"],
                json.dumps(module, allow_nan=False),
            ),
            "solve_response",
            "solve_dc_opf",
        )
        return _ok("Solved the DC OPF instance.", value)
    except TellegenTimeout as exc:
        return _err(str(exc))
    except (json.JSONDecodeError, RuntimeError, TypeError, ValueError) as exc:
        return _err(f"solve_dc_opf failed: {exc}")


async def plan_capacity(module: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Use implicit gradients and exact solves to propose capacity increases.

    The bounded search is heuristic. It returns Tellegen's proposal and the
    exact solution module for the amended instance without changing the input.
    """
    try:
        request = {"module": module, "spec": spec}
        value = _decode_response(
            await _run_tellegen(
                ["plan"],
                json.dumps(request, allow_nan=False),
            ),
            "plan_response",
            "plan_capacity",
        )
        return _ok("Computed and checked a capacity proposal.", value)
    except TellegenTimeout as exc:
        return _err(str(exc))
    except (json.JSONDecodeError, RuntimeError, TypeError, ValueError) as exc:
        return _err(f"plan_capacity failed: {exc}")


_TOOL_DESCRIPTIONS = {
    "capabilities": capabilities.__doc__ or "",
    "solve_dc_opf": solve_dc_opf.__doc__ or "",
    "plan_capacity": plan_capacity.__doc__ or "",
}


async def _list_tools(
    _context: Any, _params: types.PaginatedRequestParams | None
) -> types.ListToolsResult:
    inputs, outputs = _tool_schemas()
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name=name,
                description=_TOOL_DESCRIPTIONS[name],
                inputSchema=copy.deepcopy(inputs[name]),
                outputSchema=copy.deepcopy(outputs[name]),
            )
            for name in _TOOL_NAMES
        ]
    )


def _call_result(payload: dict[str, Any]) -> types.CallToolResult:
    text = json.dumps(payload, allow_nan=False)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=payload,
    )


async def _call_tool(
    _context: Any, params: types.CallToolRequestParams
) -> types.CallToolResult:
    inputs, _outputs = _tool_schemas()
    schema = inputs.get(params.name)
    if schema is None:
        return _call_result(_err(f"unknown tool: {params.name}"))
    arguments = params.arguments or {}
    try:
        jsonschema.Draft202012Validator(schema).validate(arguments)
    except jsonschema.ValidationError as exc:
        return _call_result(
            _err(_validation_message(f"invalid {params.name} arguments", exc))
        )

    if params.name == "capabilities":
        payload = await capabilities()
    elif params.name == "solve_dc_opf":
        payload = await solve_dc_opf(arguments["module"])
    else:
        payload = await plan_capacity(arguments["module"], arguments["spec"])
    return _call_result(payload)


mcp = Server(
    "Tellegen MCP Server",
    on_list_tools=_list_tools,
    on_call_tool=_call_tool,
)


async def _serve_stdio() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await mcp.run(
            read_stream,
            write_stream,
            mcp.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(_serve_stdio())
