"""Shared PowerIO handoff helpers for the bundled solver servers.

PowerIO has two JSON surfaces. Bare model JSON is a convenient in-memory
transport. A ``.pio.json`` package is the durable artifact: it adds provenance,
validation, diagnostics, stable row identities, operating points, study edits,
and lowering history. The helpers here let every balanced solver consume both
surfaces with the same rules.

Packages carrying operating points or study commits need an explicit
materialization through the extended PowerIO server. Using their base payload
silently would run a valid network state that may be different from the one the
caller intended.
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
class LoadedCase:
    """A balanced model plus the fidelity context that came with it."""

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
    model = value.get("model")
    if not isinstance(model, dict):
        return None
    kind = value.get("model_kind")
    if kind not in ("balanced", "multiconductor") or model.get("kind") != kind:
        return None
    return value


def _diagnostic_messages(items: list[dict[str, Any]]) -> tuple[str, ...]:
    messages = []
    for item in items:
        if item.get("severity") not in ("warning", "error", "fatal"):
            continue
        code = item.get("code")
        message = item.get("message")
        if code and message:
            messages.append(f"{code}: {message}")
        elif message:
            messages.append(str(message))
    return tuple(messages)


def _package_summary(
    pkg: "powerio.Package", document: dict[str, Any]
) -> dict[str, Any]:
    source_maps = document.get("source_maps")
    return {
        "powerio_version": document.get("powerio_version"),
        "model_kind": pkg.model_kind,
        "producer": document.get("producer"),
        "origin": document.get("origin"),
        "validation": pkg.validation(),
        "source_map_entries": len(source_maps) if isinstance(source_maps, list) else 0,
    }


def load_balanced_json(text: str) -> LoadedCase:
    """Read bare model JSON or a static balanced ``.pio.json`` package."""

    document = _package_document(text)
    if document is None:
        network = powerio.from_json(text)
        return LoadedCase(network, tuple(network.read_warnings))

    pkg = powerio.Package.from_json(text)
    if pkg.model_kind != "balanced":
        raise ValueError(
            "this solver needs a balanced package; call the PowerMCP PowerIO "
            "`lower_package` tool first"
        )
    validation = pkg.validation()
    if validation.get("status") in ("error", "fatal"):
        raise ValueError(
            "the .pio.json package fails PowerIO validation; inspect it with the "
            "PowerMCP PowerIO `inspect_package` tool before using a solver"
        )
    points = pkg.operating_points()
    study = pkg.study()
    if points is not None or study is not None:
        blocks = []
        if points is not None:
            blocks.append("operating points")
        if study is not None:
            blocks.append("study commits")
        raise ValueError(
            "the .pio.json package carries "
            + " and ".join(blocks)
            + "; call the PowerMCP PowerIO `materialize_package` tool and pass "
            "its `json` result to this tool"
        )
    diagnostics = [item for item in pkg.diagnostics() if isinstance(item, dict)]
    return LoadedCase(
        pkg.as_balanced(),
        _diagnostic_messages(diagnostics),
        _package_summary(pkg, document),
    )


def load_balanced_path(path: str, source_format: str | None = None) -> LoadedCase:
    """Read any balanced PowerIO source, including a static package path."""

    candidate = Path(path)
    explicit_package = _format_token(source_format) in _PACKAGE_FORMATS
    if explicit_package or candidate.suffix.lower() == ".json":
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            if explicit_package:
                raise
        else:
            if _package_document(text) is not None:
                return load_balanced_json(text)
            if explicit_package:
                raise ValueError("input is not a .pio.json package")
    network = powerio.parse_file(path, source_format)
    return LoadedCase(network, tuple(network.read_warnings))


def ppc_to_matpower_text(ppc: dict[str, Any]) -> str:
    """Serialize PYPOWER input tables as MATPOWER source text."""

    widths = {"bus": 13, "gen": 21, "branch": 13}
    output = [
        "function mpc = ppc_export",
        "mpc.version = '2';",
        f"mpc.baseMVA = {float(ppc['baseMVA'])!r};",
    ]
    for name in ("bus", "gen", "branch", "gencost"):
        table = ppc.get(name)
        if table is None or len(table) == 0:
            continue
        width = widths.get(name)
        rows = "\n".join(
            "\t"
            + "\t".join(repr(float(value)) for value in (row[:width] if width else row))
            + ";"
            for row in table
        )
        output.append(f"mpc.{name} = [\n{rows}\n];")
    return "\n".join(output) + "\n"
