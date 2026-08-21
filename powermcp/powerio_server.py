"""PowerMCP's extended PowerIO MCP server.

The canonical conversion, summary, matrix, diagnostics, and display tools come
from ``powerio.mcp.server``. PowerMCP adds the workflow operations that make a
``.pio.json`` package useful across solver servers: package creation and
inspection, explicit operating-point or study materialization, and explicit
multiconductor lowering.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import powerio
from powerio.mcp import server as canonical

from .sandbox import PathNotAllowed, checked_path

mcp = canonical.mcp


def _one_input(
    path: Optional[str], text: str, *, text_name: str
) -> tuple[str | None, str | None]:
    text = text or None
    if (path is None) == (text is None):
        raise ValueError(f"provide exactly one of `path` or `{text_name}`")
    return path, text


def _read_package(path: Optional[str], package_json: str) -> "powerio.Package":
    path, package_json = _one_input(path, package_json, text_name="package_json")
    if path is not None:
        path = checked_path(path, purpose="path")
        try:
            package_json = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"cannot read package: {exc}") from exc
    try:
        return powerio.Package.from_json(package_json or "")
    except powerio.PowerIOError as exc:
        code = getattr(exc, "code", None)
        prefix = f"{code}: " if code else ""
        raise ValueError(f"{prefix}{exc}") from exc


def _source_map_summary(entries: Any) -> dict[str, Any]:
    if not isinstance(entries, list):
        return {"entries": 0, "mapping_kinds": {}, "confidence": {}}
    mapping_kinds = Counter()
    confidence = Counter()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("mapping_kind"):
            mapping_kinds[str(entry["mapping_kind"])] += 1
        if entry.get("confidence"):
            confidence[str(entry["confidence"])] += 1
    return {
        "entries": len(entries),
        "mapping_kinds": dict(sorted(mapping_kinds.items())),
        "confidence": dict(sorted(confidence.items())),
    }


def _operating_point_summary(points: Any) -> Any:
    if not isinstance(points, dict):
        return None
    axis = points.get("time_axis")
    axis = axis if isinstance(axis, dict) else {}
    rows = points.get("points")
    rows = rows if isinstance(rows, list) else []
    return {
        "periods": axis.get("periods", len(rows)),
        "duration_hours": axis.get("duration_hours"),
        "labels": axis.get("labels"),
        "points": [
            {
                "index": point.get("index"),
                "updates": len(point.get("updates", []))
                if isinstance(point, dict) and isinstance(point.get("updates"), list)
                else 0,
            }
            for point in rows
            if isinstance(point, dict)
        ],
        "metadata": points.get("metadata"),
    }


def _study_summary(study: Any) -> Any:
    if not isinstance(study, dict):
        return None
    commits = study.get("commits")
    commits = commits if isinstance(commits, list) else []
    return {
        "label": study.get("label"),
        "author": study.get("author"),
        "created_at": study.get("created_at"),
        "base_operating_point": study.get("base_operating_point"),
        "commits": [
            {
                "index": index,
                "label": commit.get("label"),
                "created_at": commit.get("created_at"),
                "edits": len(commit.get("edits", []))
                if isinstance(commit, dict) and isinstance(commit.get("edits"), list)
                else 0,
            }
            for index, commit in enumerate(commits)
            if isinstance(commit, dict)
        ],
        "app": study.get("app"),
        "metadata": study.get("metadata"),
    }


def _inspect(pkg: "powerio.Package") -> dict[str, Any]:
    text = pkg.to_json()
    document = json.loads(text)
    diagnostics = canonical.diagnostics(text)
    return {
        "schema": "powermcp.package-inspection",
        "powerio_version": document.get("powerio_version", powerio.__version__),
        "model_kind": pkg.model_kind,
        "producer": document.get("producer"),
        "origin": document.get("origin"),
        "sources": document.get("sources", []),
        "source_maps": _source_map_summary(document.get("source_maps")),
        "validation": pkg.validation(),
        "diagnostics_summary": diagnostics["summary"],
        "diagnostics": diagnostics["diagnostics"],
        "operating_points": _operating_point_summary(pkg.operating_points()),
        "study": _study_summary(pkg.study()),
        "lowering_history": document.get("lowering_history", []),
        "derived": document.get("derived"),
        "summary": document.get("summary"),
    }


def _write_package(
    text: str, out_path: Optional[str], overwrite: bool
) -> Optional[str]:
    if out_path is None:
        return None
    if not out_path.lower().endswith(".pio.json"):
        raise ValueError("`out_path` must end in `.pio.json`")
    out_path = checked_path(out_path, purpose="out_path", for_write=True)
    try:
        with open(
            out_path, "w" if overwrite else "x", encoding="utf-8", newline=""
        ) as file:
            file.write(text)
    except FileExistsError:
        raise ValueError(
            f"refusing to overwrite existing file: {out_path}; pass overwrite=true"
        ) from None
    except OSError as exc:
        raise ValueError(f"cannot write package: {exc}") from exc
    return os.path.abspath(out_path)


def _result(
    pkg: "powerio.Package",
    operation: str,
    *,
    out_path: Optional[str] = None,
    overwrite: bool = False,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    package_json = pkg.to_json()
    if pkg.model_kind == "balanced":
        transport = pkg.as_balanced().to_json()
        json_format = "model-json"
        warnings: list[str] = []
    else:
        conversion = pkg.as_multiconductor().to_format("bmopf-json")
        transport = conversion.text
        json_format = "bmopf-json"
        warnings = list(conversion.warnings)
    written = _write_package(package_json, out_path, overwrite)
    result = {
        "schema": f"powermcp.{operation}",
        "powerio_version": powerio.__version__,
        "model_kind": pkg.model_kind,
        "json_format": json_format,
        "json": transport,
        "package_json": package_json,
        "inspection": _inspect(pkg),
        "warnings": warnings,
    }
    if written is not None:
        result["path"] = written
    if extra:
        result.update(extra)
    return result


@mcp.tool(
    name="package_case",
    description="Compile a case into an auditable `.pio.json` package. The package "
    "keeps provenance, source maps, diagnostics, stable element identities, and "
    "any operating points found in the source.",
)
def package_case(
    path: Optional[str] = None,
    content: str = "",
    from_format: Optional[str] = None,
    scenario: int = 0,
    out_path: Optional[str] = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    path, content = _one_input(path, content, text_name="content")
    if path is not None:
        path = checked_path(path, purpose="path")
    try:
        parsed = canonical.parse(
            path=path,
            content=content,
            from_format=from_format,
            options={"scenario": scenario},
            transport="package",
        )
        pkg = powerio.Package.from_json(parsed["package_json"])
    except PathNotAllowed:
        raise
    except (FileNotFoundError, OSError, powerio.PowerIOError) as exc:
        raise ValueError(str(exc)) from exc
    return _result(pkg, "package", out_path=out_path, overwrite=overwrite)


@mcp.tool(
    name="inspect_package",
    description="Inspect `.pio.json` provenance, validation, diagnostics, source-map "
    "coverage, operating points, study commits, and lowering history without "
    "returning the package payload again.",
)
def inspect_package(
    path: Optional[str] = None,
    package_json: str = "",
) -> dict[str, Any]:
    return _inspect(_read_package(path, package_json))


@mcp.tool(
    name="materialize_package",
    description="Select one operating point or cumulative study commit from a "
    "`.pio.json` package and return a static package plus bare model JSON ready "
    "for a PowerMCP solver bridge.",
)
def materialize_package(
    path: Optional[str] = None,
    package_json: str = "",
    operating_point: Optional[int] = None,
    study_commit: Optional[int] = None,
    out_path: Optional[str] = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    if (operating_point is None) == (study_commit is None):
        raise ValueError("provide exactly one of `operating_point` or `study_commit`")
    pkg = _read_package(path, package_json)
    if operating_point is not None:
        materialized = pkg.materialize_operating_point(operating_point)
        selector = {"kind": "operating_point", "index": operating_point}
    else:
        index = int(study_commit)  # narrowed by the check above
        materialized = pkg.materialize_study_commit(index)
        selector = {"kind": "study_commit", "index": index}
    return _result(
        materialized,
        "package-materialization",
        out_path=out_path,
        overwrite=overwrite,
        extra={"materialized": selector},
    )


@mcp.tool(
    name="lower_package",
    description="Explicitly lower a multiconductor `.pio.json` package to a "
    "balanced transmission package. Returns PowerIO's preflight report, the "
    "lowered package, bare model JSON, and recorded lowering history.",
)
def lower_package(
    path: Optional[str] = None,
    package_json: str = "",
    base_mva: float = 100.0,
    out_path: Optional[str] = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    pkg = _read_package(path, package_json)
    if pkg.model_kind != "multiconductor":
        raise ValueError("`lower_package` requires a multiconductor package")
    preflight = pkg.multiconductor_to_balanced_preflight(base_mva)
    lowered = pkg.lower_multiconductor_to_balanced(base_mva)
    return _result(
        lowered,
        "package-lowering",
        out_path=out_path,
        overwrite=overwrite,
        extra={"preflight": preflight},
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
