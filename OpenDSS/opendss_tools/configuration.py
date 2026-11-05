"""Configuration-domain MCP tools (compile, clear)."""

from pathlib import Path
from typing import Any, Dict

from mcp.server.mcpserver import MCPServer as FastMCP
from py_dss_toolkit import dss_tools

from core import state
import core.engine as engine
from powermcp.sandbox import (
    PathNotAllowed,
    allowed_roots,
    checked_path,
    checked_read_tree,
)
from utils.dss_references import missing_references
from utils.responses import _err, _ok

# OpenDSS keeps the code of the most recent command error, and reading it
# clears it; the command's own return value carries the message. (The
# interface's error_desc would too, but reading it off the main thread, where
# the MCP SDK runs synchronous tools, crashes the process.)
#
# Compile does not raise, and circuit_readiness() does not tell either: it
# checks circuit.elements_names, which a half loaded feeder fills and which
# reads ['None'] when there is no circuit at all.
#
# A Redirect, Compile, BusCoords or LatLongCoords whose file is missing
# (242, 243, 275) stops the compile where it fails, including from a nested
# file, so its error is still the latest when Compile returns. Any other
# error survives only when it comes from the file's last command, because the
# next command clears it.
#
# After a missing file error, the OpenDSS C++ engine py-dss-interface ships
# for Linux loads nothing on any later compile in the process: no reset the
# interface offers, nor a new DSS(), brings it back, and those compiles report
# no error. The tool checks the targets first (utils.dss_references) so the
# engine is not run on such a file; the error codes and the element count
# catch what that check cannot see.
#
# Code 704 is the engine failing to open a text editor for a Show command,
# which a server without a desktop cannot do. The circuit compiled, so the
# message is passed on with the result instead of refusing the file.
_MISSING_FILE_ERRORS = frozenset({242, 243, 275})
_EDITOR_ERROR = 704
_LISTED_MISSING = 20

# The first missing file error the engine reported in this process.
_missing_file_error: str | None = None


def _take_error_code(dss: Any) -> int:
    """Read, and so clear, the engine's latest error code."""
    return int(dss.errorinterface.error_code)


def _compile(dss: Any, dss_file: str) -> tuple[int, str]:
    """``dss_tools.configuration.compile_dss``, keeping what Compile returns."""
    _take_error_code(dss)  # an error left by an earlier command is not this compile's
    dss.text("ClearAll")
    reply = dss.text(f"Compile [{dss_file}]")
    return _take_error_code(dss), str(reply or "").strip()


def _forget_circuit() -> None:
    state.circuit_loaded = False
    state.solution_available = False
    state.last_compiled_dss_file = None


def _missing_files_error(dss_file: str) -> Dict[str, Any] | None:
    missing = missing_references(dss_file)
    if not missing:
        return None
    listed = "; ".join(m.describe() for m in missing[:_LISTED_MISSING])
    if len(missing) > _LISTED_MISSING:
        listed += f"; and {len(missing) - _LISTED_MISSING} more"
    return _err(
        f"{dss_file} names files that do not exist: {listed}. "
        "OpenDSS was not run, because a missing file stops the compile partway."
    )


def compile_opendss_file(dss_file: str, force_recompile: bool = False) -> Dict[str, Any]:
    """Compile a master DSS file (ClearAll + Compile).

    If the same resolved path is already loaded, skips compile unless ``force_recompile`` is True
    (e.g. user wants to recompile the model to clean changes). A different ``dss_file`` loads a new model and always compiles.
    After ``clear_all_opendss_memory``, the next call compiles as usual.

    Returns dss_file, circuit_readiness, circuit_loaded, and whether the compile was skipped.
    A Redirect, Compile, BusCoords or LatLongCoords target that does not exist is reported
    before OpenDSS runs, listing each one. An error OpenDSS reports while compiling, or a
    compile that leaves no circuit, is returned as an error with the engine's code and
    message, and the partly loaded circuit is cleared.
    """
    global _missing_file_error
    try:
        dss_file = checked_path(dss_file, purpose="dss_file")
        # A DSS model may Redirect/Compile sibling files.  Preflight the whole
        # project tree when containment is enabled; preserve the historical
        # single-file behavior when the operator has not configured roots.
        if allowed_roots():
            checked_read_tree(str(Path(dss_file).parent), purpose="DSS input tree")
    except PathNotAllowed as exc:
        return _err(str(exc))
    resolved = str(Path(dss_file).resolve())
    if (
        state.circuit_loaded
        and state.last_compiled_dss_file is not None
        and resolved == state.last_compiled_dss_file
        and not force_recompile
    ):
        readiness = dss_tools.configuration.circuit_readiness()
        return _ok(
            {
                "dss_file": dss_file,
                "resolved_dss_file": resolved,
                "skipped": True,
                "already_compiled": True,
                "circuit_readiness": readiness,
                "circuit_loaded": True,
                "solution_available": state.solution_available,
            }
        )
    try:
        refused = _missing_files_error(dss_file)
        if refused is not None:
            return refused
        dss = engine.ensure_engine()
        code, message = _compile(dss, dss_file)
        if code and code != _EDITOR_ERROR:
            if code in _MISSING_FILE_ERRORS and _missing_file_error is None:
                _missing_file_error = f"error {code}: {message}"
            _forget_circuit()
            dss.text("ClearAll")
            return _err(f"OpenDSS could not compile {dss_file}: error {code}: {message}")
        if int(dss.circuit.num_ckt_elements) == 0:
            _forget_circuit()
            refusal = f"OpenDSS loaded no circuit from {dss_file}."
            if _missing_file_error is not None:
                refusal += (
                    " An earlier compile in this server failed on a missing file "
                    f"({_missing_file_error}), after which the OpenDSS engine may load "
                    "nothing until the server restarts."
                )
            return _err(refusal)
        readiness = dss_tools.configuration.circuit_readiness()
        state.circuit_loaded = True
        state.solution_available = False
        state.last_compiled_dss_file = resolved
        payload = {
            "dss_file": dss_file,
            "resolved_dss_file": resolved,
            "skipped": False,
            "circuit_readiness": readiness,
            "circuit_loaded": True,
            "solution_available": False,
        }
        if code:
            payload["engine_message"] = f"error {code}: {message}"
        return _ok(payload)
    except Exception as e:
        _forget_circuit()
        return _err(str(e))


def clear_all_opendss_memory() -> Dict[str, Any]:
    """Clear OpenDSS engine memory (ClearAll); resets circuit_loaded, solution_available, and last compiled path."""
    try:
        engine.ensure_engine().text("ClearAll")
        _forget_circuit()
        return _ok(
            {"cleared": True, "circuit_loaded": False, "solution_available": False}
        )
    except Exception as e:
        return _err(str(e))


def register_configuration_tools(mcp: FastMCP) -> None:
    mcp.tool()(compile_opendss_file)
    mcp.tool()(clear_all_opendss_memory)
