"""`powermcp doctor` — check each tool's dependencies and configured paths.

Dependency checks use ``importlib.util.find_spec`` (which locates a module
without executing it) so the doctor never triggers a vendor engine's import-time
side effects (e.g. PSS/E ``psseinit``) and never crashes on a broken DLL. Vendor
engines that load from a captured directory (PSS/E, PSLF, PowerFactory) are not
import-probed at all — they are reported via their configured paths and verified
for real only at runtime.

Two checks are shared rather than per tool and print below the table: the MCP
SDK, which every server imports, and the filesystem containment policy, which
every path-taking tool applies. Both matter here because a server that fails at
launch gives its MCP client nothing at all — the diagnosis only exists in a
stderr the client does not read — so the doctor has to catch it beforehand.
"""

from __future__ import annotations

import os
import sys
from importlib.metadata import PackageNotFoundError, requires, version

from packaging.requirements import Requirement
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import config as cfg
from .registry import Tool, all_tools, get_tool, install_hint
from .runner import probe_installed
from .sandbox import ALLOWED_ROOTS_ENV, LEGACY_ROOT_ENVS, allowed_roots

# Engines imported from a captured local dir (not from PyPI). Do not import-probe.
_PATH_LOADED = {"psse", "pslf", "powerfactory"}


def _surge_supported() -> bool:
    return (3, 12) <= sys.version_info[:2] < (3, 15)


def _declared_requirement(name: str) -> Requirement | None:
    """This distribution's own declared requirement on ``name``, if it has one."""
    try:
        declared = requires("powermcp") or ()
    except PackageNotFoundError:
        return None
    for raw in declared:
        req = Requirement(raw)
        if req.name == name:
            return req
    return None


def _version_status(name: str) -> tuple[str, str] | None:
    """(style, message) when the installed version violates our declared floor.

    ``find_spec`` answers "importable", which is a different question from "new
    enough": an old powerio imports fine and then refuses tools this repo calls.
    ``None`` when there is nothing to say — no declared floor, or it is met.
    """
    req = _declared_requirement(name)
    if req is None or not req.specifier:
        return None
    try:
        installed = version(name)
    except PackageNotFoundError:
        return None
    if req.specifier.contains(installed, prereleases=True):
        return None
    return "red", f"{name} {installed} is below the required {name}{req.specifier}"


def _dep_status(t: Tool) -> tuple[str, str]:
    """Return (style, message) for the dependency column."""
    if t.windows_only and sys.platform != "win32":
        return "dim", "skipped — Windows-only"
    if t.name == "surge" and not _surge_supported():
        return "yellow", f"needs Python 3.12–3.14 (have {sys.version_info.major}.{sys.version_info.minor})"
    if t.name in _PATH_LOADED:
        return "cyan", "vendor engine — loaded from configured path"
    if t.probe:
        if not probe_installed(t.probe):
            return "red", f"missing — {install_hint(t.extra)}"
        stale = _version_status(t.probe)
        if stale:
            return stale
    return "green", "ok"


def _sdk_status() -> tuple[str, str]:
    """Every server imports the MCP SDK, so no tool row would report it."""
    if not probe_installed("mcp"):
        return "red", f"mcp: missing — every server needs it; {install_hint(None)}"
    stale = _version_status("mcp")
    if stale:
        return stale
    return "green", f"mcp: {version('mcp')}"


def _containment_status() -> tuple[str, str]:
    """Whether model supplied paths are confined, and to what.

    Unset is a legitimate configuration, not a fault, but it is worth stating:
    a tool argument is whatever the model was persuaded to ask for.
    """
    roots = allowed_roots()
    if not roots:
        return "yellow", (
            f"MCP paths: unconfined — set {ALLOWED_ROOTS_ENV} to an "
            f"{os.pathsep!r} separated list of directories to confine reads and writes"
        )
    missing = [str(r) for r in roots if not r.is_dir()]
    if missing:
        return "red", (
            "MCP paths: confined to a directory that does not exist, so every "
            "path is refused: " + ", ".join(missing)
        )
    return "green", "MCP paths: confined to " + ", ".join(str(r) for r in roots)


def _path_status(t: Tool) -> tuple[str, str]:
    """Return (style, message) for the configured-paths column."""
    required = [ck for ck in t.config_keys if ck.required]
    optional = [ck for ck in t.config_keys if not ck.required]
    if not t.config_keys:
        return "dim", "—"
    missing = []
    for ck in required:
        try:
            cfg.get_path(t.name, ck.key)
        except cfg.ConfigError:
            missing.append(f"{t.name}.{ck.key}")
    if missing:
        return "yellow", "set: " + ", ".join(missing)
    label = "configured"
    if optional:
        label += f" ({len(optional)} optional)"
    return "green", label


def run_doctor(tool: str | None = None) -> None:
    tools = [get_tool(tool)] if tool else all_tools()
    table = Table(title="PowerMCP doctor")
    table.add_column("tool")
    table.add_column("dependencies")
    table.add_column("config paths")
    solver_notes: list[str] = []
    for t in tools:
        dep_style, dep_msg = _dep_status(t)
        path_style, path_msg = _path_status(t)
        # escape(): a message carries user data such as `powermcp[andes]`, and
        # Rich reads a bracketed lowercase word as a style tag and drops it.
        table.add_row(
            t.name,
            f"[{dep_style}]{escape(dep_msg)}[/]",
            f"[{path_style}]{escape(path_msg)}[/]",
        )
        if t.external_solvers and not (t.windows_only and sys.platform != "win32"):
            solver_notes.append(f"  • {t.display}: needs {', '.join(t.external_solvers)} available at runtime")

    console = Console()
    console.print(table)
    for style, message in (_sdk_status(), _containment_status()):
        console.print(f"[{style}]{escape(message)}[/]")
    if solver_notes:
        console.print(
            "\n[dim]Note: some tools also need external solvers/runtimes on PATH "
            "(a green check above only means the Python package is present):[/]"
        )
        for note in solver_notes:
            console.print(f"[dim]{note}[/]")
    console.print(f"\n[dim]Config file: {cfg.config_path()}[/]")
