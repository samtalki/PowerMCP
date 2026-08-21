"""Prepare PowerIO models for solver-specific PowerMCP tools.

PowerIO owns the durable ``.pio.json`` package lifecycle.  This module only
handles the boundary where a package becomes one concrete balanced network for
a PowerMCP solver.  Materialization, validation, diagnostics, and provenance
remain PowerIO operations performed through its public v0.9 API.
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
class PreparedCase:
    """One balanced solver state plus its PowerIO handoff context."""

    network: Any
    warnings: tuple[str, ...] = ()
    package: dict[str, Any] | None = None


def _format_token(value: str | None) -> str | None:
    return value.strip().lower().replace("_", "-") if value is not None else None


def _package_document(text: str) -> dict[str, Any] | None:
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


def _validate_package(package: powerio.Package) -> dict[str, Any]:
    validation = package.validation()
    if validation.get("status") in ("error", "fatal"):
        raise ValueError(
            "the .pio.json package fails PowerIO validation; use the canonical "
            "PowerIO diagnostics tool before handing it to a solver"
        )
    return validation


def _prepare_package(
    text: str,
    document: dict[str, Any],
    *,
    operating_point: int | None,
    study_commit: int | None,
) -> PreparedCase:
    if operating_point is not None and study_commit is not None:
        raise ValueError("choose either operating_point or study_commit, not both")
    if operating_point is not None and operating_point < 0:
        raise ValueError("operating_point must be zero or greater")
    if study_commit is not None and study_commit < 0:
        raise ValueError("study_commit must be zero or greater")

    package = powerio.Package.from_json(text)
    _validate_package(package)
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

    validation = _validate_package(package)
    source_maps = document.get("source_maps")
    context: dict[str, Any] = {
        "powerio_version": document.get("powerio_version"),
        "model_kind": package.model_kind,
        "producer": document.get("producer"),
        "origin": document.get("origin"),
        "validation": validation,
        "source_map_entries": len(source_maps) if isinstance(source_maps, list) else 0,
    }
    if point_indexes:
        context["operating_points"] = point_indexes
    if commit_indexes:
        context["study_commits"] = commit_indexes
    if selection is not None:
        context["materialized"] = selection
    return PreparedCase(
        package.as_balanced(),
        _diagnostic_messages(package.diagnostics()),
        context,
    )


def prepare_balanced_json(
    text: str,
    *,
    operating_point: int | None = None,
    study_commit: int | None = None,
) -> PreparedCase:
    """Prepare bare model JSON or one selected state from a package."""

    document = _package_document(text)
    if document is not None:
        return _prepare_package(
            text,
            document,
            operating_point=operating_point,
            study_commit=study_commit,
        )
    if operating_point is not None or study_commit is not None:
        raise ValueError("state selectors are only valid for .pio.json packages")
    network = powerio.from_json(text)
    return PreparedCase(network, tuple(network.read_warnings))


def prepare_balanced_file(
    path: str,
    source_format: str | None = None,
    *,
    operating_point: int | None = None,
    study_commit: int | None = None,
) -> PreparedCase:
    """Prepare a PowerIO input file, including one selected package state."""

    candidate = Path(path)
    explicit_package = _format_token(source_format) in _PACKAGE_FORMATS
    if explicit_package or candidate.suffix.lower() == ".json":
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            if explicit_package:
                raise
        else:
            document = _package_document(text)
            if document is not None:
                return _prepare_package(
                    text,
                    document,
                    operating_point=operating_point,
                    study_commit=study_commit,
                )
            if explicit_package:
                raise ValueError("input is not a .pio.json package")
    if operating_point is not None or study_commit is not None:
        raise ValueError("state selectors are only valid for .pio.json packages")
    network = powerio.parse_file(path, source_format)
    return PreparedCase(network, tuple(network.read_warnings))
