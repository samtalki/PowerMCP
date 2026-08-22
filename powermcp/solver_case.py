"""Resolve PowerIO inputs into one balanced state for a solver.

PowerIO owns case parsing and the durable ``.pio.json`` package lifecycle.
PowerMCP owns the point where a concrete network enters one of its solver
servers.  Keeping that boundary here gives every solver the same validation,
state-selection, diagnostic, and multiconductor rules without copying them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import powerio

_PACKAGE_FORMATS = frozenset(
    {"package", "pio", "pio-json", "pio_json", "pio-package", "pio_package"}
)


@dataclass(frozen=True)
class SolverCase:
    """One validated balanced state ready for a PowerMCP solver."""

    network: powerio.BalancedNetwork
    warnings: tuple[str, ...] = ()
    package: dict[str, Any] | None = None


def _format_token(value: str | None) -> str | None:
    return value.strip().lower().replace("_", "-") if value is not None else None


def _package_document(text: str) -> dict[str, Any] | None:
    """Return the parsed document only when ``text`` identifies a package."""
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    if value.get("model_kind") not in ("balanced", "multiconductor"):
        return None
    return value if isinstance(value.get("model"), dict) else None


def _diagnostic_messages(items: Any) -> tuple[str, ...]:
    messages = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or item.get("severity") != "warning":
            continue
        code = item.get("code")
        message = item.get("message")
        if code and message:
            messages.append(f"{code}: {message}")
        elif message:
            messages.append(str(message))
    return tuple(messages)


def _unique_messages(*groups: Any) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(message)
            for group in groups
            for message in (group or ())
            if message
        )
    )


def _operating_point_indexes(points: Any) -> list[int]:
    if not isinstance(points, dict) or not isinstance(points.get("points"), list):
        return []
    return [
        point["index"]
        for point in points["points"]
        if isinstance(point, dict) and isinstance(point.get("index"), int)
    ]


def _study_commit_indexes(study: Any) -> list[int]:
    if not isinstance(study, dict) or not isinstance(study.get("commits"), list):
        return []
    return list(range(len(study["commits"])))


def _validated(package: powerio.Package) -> dict[str, Any]:
    validation = package.validation()
    if validation.get("status") in ("error", "fatal"):
        raise ValueError(
            "the PowerIO package fails validation; inspect it with "
            "the canonical PowerIO diagnostics tool before solving it"
        )
    return validation


def _resolve_package(
    package: powerio.Package,
    *,
    original_document: dict[str, Any] | None,
    input_warnings: tuple[str, ...],
    operating_point: int | None,
    study_commit: int | None,
) -> SolverCase:
    """Validate and reduce a package to the one state a solver can consume."""
    if operating_point is not None and study_commit is not None:
        raise ValueError("choose either operating_point or study_commit, not both")
    if operating_point is not None and operating_point < 0:
        raise ValueError("operating_point must be zero or greater")
    if study_commit is not None and study_commit < 0:
        raise ValueError("study_commit must be zero or greater")

    _validated(package)
    if package.model_kind != "balanced":
        raise ValueError(
            "this solver requires a balanced package; explicitly lower the "
            "package with PowerIO Package.lower_multiconductor_to_balanced() first"
        )

    points = package.operating_points()
    study = package.study()
    point_indexes = _operating_point_indexes(points)
    commit_indexes = _study_commit_indexes(study)
    selection: dict[str, Any] | None = None
    if operating_point is not None:
        if points is None:
            raise ValueError("the .pio.json package has no operating points")
        package = package.materialize_operating_point(operating_point)
        selection = {"kind": "operating_point", "index": operating_point}
    elif study_commit is not None:
        if study is None:
            raise ValueError("the .pio.json package has no study commits")
        package = package.materialize_study_commit(study_commit)
        selection = {"kind": "study_commit", "index": study_commit}
        if isinstance(study, dict) and study.get("base_operating_point") is not None:
            selection["base_operating_point"] = study["base_operating_point"]
    elif points is not None or study is not None:
        choices = []
        if points is not None:
            choices.append(f"operating_point from {point_indexes}")
        if study is not None:
            choices.append(f"study_commit from {commit_indexes}")
        raise ValueError(
            "the .pio.json package contains multiple solver states; select "
            + " or ".join(choices)
        )

    validation = _validated(package)
    network = package.as_balanced()
    package_context: dict[str, Any] | None = None
    if original_document is not None:
        source_maps = original_document.get("source_maps")
        package_context = {
            "powerio_version": original_document.get("powerio_version"),
            "model_kind": package.model_kind,
            "producer": original_document.get("producer"),
            "origin": original_document.get("origin"),
            "validation": validation,
            "source_map_entries": (
                len(source_maps) if isinstance(source_maps, list) else 0
            ),
        }
        if original_document.get("package_id") is not None:
            package_context["package_id"] = original_document["package_id"]
        if point_indexes:
            package_context["operating_points"] = point_indexes
        if commit_indexes:
            package_context["study_commits"] = commit_indexes
        if selection is not None:
            package_context["materialized"] = selection

    return SolverCase(
        network,
        _unique_messages(
            input_warnings,
            network.read_warnings,
            _diagnostic_messages(package.diagnostics()),
        ),
        package_context,
    )


def resolve_solver_case(
    *,
    file_path: str | None = None,
    network_json: str | None = None,
    source_format: str | None = None,
    operating_point: int | None = None,
    study_commit: int | None = None,
) -> SolverCase:
    """Resolve exactly one file or JSON input into a validated solver case.

    Ordinary case files are wrapped with :class:`powerio.Package`, so the same
    PowerIO 0.9 validation and diagnostics run for file and package inputs.
    Package metadata is returned only when the caller supplied a package.
    """
    if (file_path is None) == (network_json is None):
        raise ValueError("provide exactly one of file_path or network_json")

    original_document: dict[str, Any] | None = None
    input_warnings: tuple[str, ...] = ()
    if file_path is not None:
        candidate = Path(file_path)
        explicit_package = _format_token(source_format) in _PACKAGE_FORMATS
        if explicit_package or candidate.suffix.lower() == ".json":
            try:
                text = candidate.read_text(encoding="utf-8")
            except OSError:
                if explicit_package:
                    raise
            else:
                original_document = _package_document(text)
                if original_document is not None:
                    package = powerio.Package.from_json(text)
                elif explicit_package:
                    raise ValueError("input is not a .pio.json package")
        if original_document is None:
            package = powerio.Package.from_file(file_path, source_format)
    else:
        original_document = _package_document(network_json)
        if original_document is not None:
            package = powerio.Package.from_json(network_json)
        else:
            if operating_point is not None or study_commit is not None:
                raise ValueError("state selectors are only valid for .pio.json packages")
            network = powerio.from_json(network_json)
            input_warnings = tuple(network.read_warnings)
            package = powerio.Package.from_balanced(network)

    return _resolve_package(
        package,
        original_document=original_document,
        input_warnings=input_warnings,
        operating_point=operating_point,
        study_commit=study_commit,
    )
