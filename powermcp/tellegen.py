"""Native Tellegen Study operations over the versioned CLI contract."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from powermcp.config import get, get_path
from powermcp.sandbox import checked_path

mcp = MCPServer("Tellegen Studies")
MAX_BUNDLE_BYTES = 512 * 1024 * 1024
OPERATIONS = frozenset({"inspect", "branch", "revise_goal", "compare", "propose", "record_evidence"})


def _binary() -> str:
    if os.environ.get("POWERMCP_TELLEGEN_BINARY") or get("tellegen", "binary"):
        return get_path("tellegen", "binary")
    executable = shutil.which("tellegen")
    if executable:
        return executable
    raise RuntimeError("Install the native Tellegen CLI with Study support or set POWERMCP_TELLEGEN_BINARY")


def _seconds(key: str, default: float) -> float:
    value = os.environ.get(f"POWERMCP_TELLEGEN_{key.upper()}", get("tellegen", key, default))
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"tellegen.{key} must be a finite positive duration") from None
    if isinstance(value, bool) or not math.isfinite(seconds) or not 0 < seconds <= 86400:
        raise ValueError(f"tellegen.{key} must be a duration in (0, 86400] seconds")
    return seconds


async def _call(arguments: list[str], request: Any = None) -> Any:
    data = b"" if request is None else json.dumps(request, allow_nan=False).encode()
    if len(data) > MAX_BUNDLE_BYTES:
        raise ValueError("Tellegen input exceeds the 512 MiB Study limit")
    timeout = _seconds("timeout_seconds", 1800)
    grace = _seconds("cancel_grace_seconds", 300)
    windows = sys.platform == "win32"
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if windows else {}
    process = await asyncio.create_subprocess_exec(
        _binary(), *arguments, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **options,
    )
    communication = asyncio.create_task(process.communicate(data))
    try:
        stdout, stderr = await asyncio.wait_for(asyncio.shield(communication), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError) as stopped:
        if process.returncode is None:
            if windows:
                try:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                except OSError:
                    process.terminate()
            else:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
        try:
            await asyncio.wait_for(asyncio.shield(communication), timeout=grace)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if process.returncode is None:
                process.kill()
            await process.wait()
            communication.cancel()
            await asyncio.gather(communication, return_exceptions=True)
        if isinstance(stopped, asyncio.CancelledError):
            raise
        raise RuntimeError(f"Tellegen execution timed out. Cancellation allows the current trial to finish and saves completed planning evidence. Inspect the saved Study revision before retrying; a forced stop after {grace:g} seconds may leave the previous revision. A remaining .lock requires checking that its writer exited.") from None
    if process.returncode:
        raise RuntimeError(stderr.decode(errors="replace")[:2048] or "Tellegen failed without a diagnostic")
    return json.loads(stdout)


def _path(path: str, *, write: bool = False) -> str:
    return str(checked_path(path, purpose="Study bundle", for_write=write))


def _summary(value: dict[str, Any]) -> dict[str, Any]:
    summary = dict(value.get("summary", value))
    goal = summary.get("active_goal")
    if isinstance(goal, list) and len(goal) == 2:
        summary["active_goal"] = {"id": goal[0], "request": goal[1]["request"], "anchor_state": goal[1]["anchor_state"]}
    summary["recent_experiments"] = summary.get("recent_experiments", [])[:3]
    if "experiment" in value:
        summary["experiment"] = value["experiment"]
    comparison = value.get("comparison")
    if comparison:
        summary["comparison"] = {key: comparison[key] for key in ("goal", "left", "right", "left_value", "right_value", "improvement")}
    return summary


@mcp.tool()
async def study_contract() -> dict[str, Any]:
    """Read the installed native formulation capabilities and generated Study request schemas."""
    return await _call(["contract"])


@mcp.tool()
async def study_create(path: str, request: dict[str, Any]) -> dict[str, Any]:
    """Create a durable Study from PowerIO IR and a declared goal using the native CreateStudy schema."""
    return _summary(await _call(["study", "create", _path(path, write=True)], request))


@mcp.tool()
async def study_inspect(path: str, section: str = "summary", record_id: str | None = None,
                        offset: int = 0, expected_revision: int | None = None) -> dict[str, Any]:
    """Inspect a saved Study or read bounded JSON fragments of a goal, state history, experiment or evidence."""
    checked = _path(path)
    if section == "summary":
        summary = await _call(["study", "inspect", checked])
        if expected_revision is not None and summary["revision"] != expected_revision:
            raise ValueError("Study revision changed; restart the inspection")
        return _summary(summary)
    if offset < 0:
        raise ValueError("offset must be nonnegative")
    bundle = await _call(["study", "export", checked])
    document = bundle["document"]
    if expected_revision is not None and document["revision"] != expected_revision:
        raise ValueError("Study revision changed; restart the inspection")
    if section == "goal":
        record = document["goals"][record_id or document["active_goal"]]
    elif section == "states":
        record = document["states"]
    elif section == "experiment":
        record = document["experiments"][record_id]
    elif section == "evidence":
        artifact = bundle["artifacts"][record_id]
        if artifact["kind"] != "evidence":
            raise ValueError("Requested artifact is not evidence")
        record = artifact["text"]
    else:
        raise ValueError("section must be summary, goal, states, experiment or evidence")
    encoded = json.dumps(record)
    fragment = encoded[offset:offset + 8192]
    following = offset + len(fragment)
    return {"id": document["id"], "revision": document["revision"], "encoding": "json",
            "offset": offset, "fragment": fragment, "next_offset": following if following < len(encoded) else None}


@mcp.tool()
async def study_run(path: str, expected_revision: int, operation: dict[str, Any]) -> dict[str, Any]:
    """Inspect, branch, revise a goal, compare, propose interventions or attach evidence using the native StudyOperation schema. Proposals stay unapplied; application requires an explicit native CLI user action."""
    if operation.get("kind") not in OPERATIONS:
        raise ValueError("Unsupported agent operation. Apply the reviewed proposal through an explicit native CLI user action.")
    return _summary(await _call(["study", "run", _path(path, write=True)],
                               {"expected_revision": expected_revision, "operation": operation}))


@mcp.tool()
async def study_import(source_path: str, path: str) -> dict[str, Any]:
    """Validate and import a portable Study bundle into a new destination without restoring approvals or executing imported instructions."""
    source = Path(_path(source_path))
    with source.open("rb") as stream:
        data = stream.read(MAX_BUNDLE_BYTES + 1)
    if len(data) > MAX_BUNDLE_BYTES:
        raise ValueError("Study bundle exceeds 512 MiB")
    return _summary(await _call(["study", "import", _path(path, write=True)], json.loads(data)))


@mcp.tool()
async def study_export(path: str) -> dict[str, Any]:
    """Validate the saved portable bundle and return its path and digest for transfer to another agent or browser."""
    checked = _path(path)
    bundle = await _call(["study", "export", checked])
    data = Path(checked).read_bytes()
    current = json.loads(data)
    if current != bundle:
        raise ValueError("Study changed during export; retry")
    return {"path": checked, "id": bundle["document"]["id"], "revision": bundle["document"]["revision"],
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "format": "tellegen-study"}


if __name__ == "__main__":
    mcp.run()
