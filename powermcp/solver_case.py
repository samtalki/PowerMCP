"""Resolve PowerIO inputs into one balanced module for a solver.

PowerIO owns parsing, stored ``.pio.json`` modules, diagnostics, collection
discovery, and state export. PowerMCP owns the point where one static balanced
value crosses into a solver server. Keeping the module at that boundary
preserves its source, diagnostics, and history until the target format is
emitted.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import powerio

from powermcp.sandbox import checked_path, checked_read_tree


@dataclass(frozen=True)
class SolverCase:
    """One static balanced PowerIO module ready for a solver bridge."""

    module: powerio.PioModule
    network: powerio.BalancedNetwork
    diagnostics: tuple[dict[str, Any], ...] = ()


def _source_span_record(span: Any) -> dict[str, Any]:
    if isinstance(span, dict):
        return dict(span)
    return {
        "source": str(span.source),
        "byte_start": int(span.byte_start),
        "byte_end": int(span.byte_end),
    }


def diagnostic_record(item: Any) -> dict[str, Any]:
    """Convert a PowerIO diagnostic to its JSON safe structured form."""
    if isinstance(item, dict):
        return dict(item)
    record: dict[str, Any] = {
        "code": str(item.code),
        "severity": str(item.severity),
        "message": str(item.message),
    }
    for name in ("id", "target", "suggested_action"):
        value = getattr(item, name, None)
        if value is not None:
            record[name] = str(value)
    related = list(getattr(item, "related", ()) or ())
    if related:
        record["related"] = [str(value) for value in related]
    spans = list(getattr(item, "spans", ()) or ())
    if spans:
        record["spans"] = [_source_span_record(span) for span in spans]
    details = getattr(item, "details", None)
    if details:
        record["details"] = dict(details)
    return record


def diagnostic_records(*groups: Iterable[Any]) -> tuple[dict[str, Any], ...]:
    """Combine diagnostic groups in order and remove exact duplicates."""
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            record = diagnostic_record(item)
            key = json.dumps(record, sort_keys=True, separators=(",", ":"))
            if key not in seen:
                seen.add(key)
                records.append(record)
    return tuple(records)


def bridge_diagnostic(code: str, message: str) -> dict[str, Any]:
    """Create one structured PowerMCP bridge diagnostic."""
    return {"code": code, "severity": "warning", "message": message}


def powerio_error_response(
    error: powerio.PowerIOError,
    *,
    prefix: str | None = None,
) -> dict[str, str]:
    """Keep a PowerIO error code in a PowerMCP error envelope."""
    message = str(error)
    if prefix:
        message = f"{prefix}: {message}"
    return {"status": "error", "code": str(error.code), "message": message}


def _available_positions(positions: list[int]) -> str:
    """Describe time positions without flooding an MCP error response."""
    if len(positions) <= 20:
        return str(positions)
    if all(
        position == positions[0] + offset for offset, position in enumerate(positions)
    ):
        return f"{positions[0]}..{positions[-1]} ({len(positions)} available)"
    preview = ", ".join(str(position) for position in positions[:10])
    return f"[{preview}, ...] ({len(positions)} available; last {positions[-1]})"


def _available_scenarios(scenarios: list[str]) -> str:
    if len(scenarios) <= 20:
        return repr(scenarios)
    preview = ", ".join(repr(scenario) for scenario in scenarios[:10])
    return f"[{preview}, ...] ({len(scenarios)} available)"


def _selection_required(inventory: dict[str, Any]) -> ValueError:
    keyed_by = inventory.get("keyed_by")
    if keyed_by == "time_position":
        rows = inventory.get("time_points")
        positions = []
        if isinstance(rows, list):
            positions = [
                row["position"]
                for row in rows
                if isinstance(row, dict) and isinstance(row.get("position"), int)
            ]
        return ValueError(
            "the PowerIO module contains a time series; call PowerIO list_states, "
            "then export_state with time_position from "
            f"{_available_positions(positions)} before passing it to a solver"
        )
    if keyed_by == "scenario":
        rows = inventory.get("scenarios")
        scenarios = []
        if isinstance(rows, list):
            scenarios = [
                str(row["id"])
                for row in rows
                if isinstance(row, dict) and row.get("id") is not None
            ]
        return ValueError(
            "the PowerIO module contains a scenario set; call PowerIO list_states, "
            "then export_state with scenario from "
            f"{_available_scenarios(scenarios)} before passing it to a solver"
        )
    return ValueError(
        "the PowerIO module contains a collection; call PowerIO list_states and "
        "export_state before passing it to a solver"
    )


def _resolve_module(module: powerio.PioModule) -> SolverCase:
    operations = set(module.inspect().get("operations", ()))
    if "list_states" in operations:
        raise _selection_required(module.list_states())

    if module.kind == "multiconductor_network":
        raise ValueError(
            "this solver requires a balanced network; transform the module with "
            "PowerIO PioModule.to_balanced() first"
        )
    if module.kind != "balanced_network":
        raise ValueError(
            "this solver requires a balanced network; the PowerIO module carries "
            f"{module.kind!r}"
        )
    network = module.value
    if not isinstance(network, powerio.BalancedNetwork):
        raise TypeError("PowerIO returned a non-balanced value for balanced_network")

    return SolverCase(
        module=module,
        network=network,
        diagnostics=diagnostic_records(module.diagnostics),
    )


def resolve_solver_case(
    *,
    file_path: str | None = None,
    network_json: str | None = None,
    source_format: str | None = None,
) -> SolverCase:
    """Resolve exactly one file or JSON input into a static balanced module.

    ``network_json`` accepts any JSON input recognized by PowerIO, including
    balanced model JSON and stored modules. Collection modules must be exported
    to a static module through PowerIO before they cross the solver boundary.
    PowerIO owns format detection, upgrades, validation, and typed errors.
    """
    if (file_path is None) == (network_json is None):
        raise ValueError("provide exactly one of file_path or network_json")

    if file_path is not None:
        file_path = checked_path(file_path, purpose="file_path")
        if Path(file_path).is_dir():
            file_path = checked_read_tree(file_path, purpose="file_path")
        module = powerio.parse_file(file_path, format=source_format)
    else:
        module = powerio.parse_text(
            network_json,
            format=source_format,
            name="solver-input.json",
        )

    return _resolve_module(module)
