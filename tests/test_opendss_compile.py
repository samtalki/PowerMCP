"""compile_opendss_file reports a feeder OpenDSS cannot load.

Compile does not raise when a Redirect target is missing: OpenDSS stops the
compile there, records error 243, and leaves a circuit with no buses. The tool
used to return success for that circuit (issue #129). Worse, the OpenDSS C++
engine py-dss-interface ships for Linux then loads nothing on any later compile
in the process, so the tool checks file targets before running the engine.

The first part drives the tool against a fake engine that keeps its error state
the way OpenDSS does. The rest runs the real engine wherever py-dss-interface
ships one (Windows and Linux) and skips elsewhere; the test that makes the
engine fail on a missing file runs in its own interpreter.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

OPENDSS_DIR = Path(__file__).resolve().parents[1] / "OpenDSS"
CASE_13BUS = OPENDSS_DIR / "13Bus"

_SERVER_MODULES = (
    "core.engine", "core.state", "core.server", "core",
    "opendss_tools.configuration", "opendss_tools.interactive_view",
    "opendss_tools.model", "opendss_tools.results", "opendss_tools.simulation",
    "opendss_tools", "utils.responses", "utils",
)


class _EngineErrors:
    """OpenDSS's error state: the latest code, cleared on read."""

    def __init__(self) -> None:
        self.code = 0

    @property
    def error_code(self) -> int:
        code, self.code = self.code, 0
        return code

    @property
    def error_desc(self) -> str:
        raise AssertionError("error_desc crashes the engine off the main thread")


class _FakeDSS:
    """Compile returns the error message, as the engine's Text interface does."""

    def __init__(self) -> None:
        self.errorinterface = _EngineErrors()
        self.circuit = types.SimpleNamespace(num_ckt_elements=1)
        self.commands: list[str] = []
        self.on_compile = lambda _path: ""

    def text(self, command: str) -> str:
        self.commands.append(command)
        if command.startswith("Compile ["):
            return self.on_compile(command[len("Compile ["):-1])
        return ""


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """The configuration tools over a fake engine; ``on_compile`` sets the error."""
    monkeypatch.syspath_prepend(str(OPENDSS_DIR))
    dss = _FakeDSS()
    hooks = dss

    fake_tools = types.SimpleNamespace(
        update_dss=lambda _dss: None,
        configuration=types.SimpleNamespace(
            circuit_readiness=lambda: {"ready": True, "code": "ok", "message": ""},
        ),
    )
    monkeypatch.setitem(sys.modules, "py_dss_toolkit", types.SimpleNamespace(dss_tools=fake_tools))
    monkeypatch.setitem(sys.modules, "py_dss_interface", types.SimpleNamespace(DSS=lambda: dss))
    for name in _SERVER_MODULES:
        monkeypatch.delitem(sys.modules, name, raising=False)
    configuration = importlib.import_module("opendss_tools.configuration")
    monkeypatch.setattr(configuration, "allowed_roots", lambda: ())
    state = importlib.import_module("core.state")

    master = tmp_path / "master.dss"
    master.write_text("Clear\n")
    other = tmp_path / "other.dss"
    other.write_text("Clear\n")
    return types.SimpleNamespace(
        configuration=configuration, state=state, dss=dss, hooks=hooks,
        master=master, other=other,
    )


def _engine_reports(dss: _FakeDSS, code: int, message: str):
    def on_compile(_path):
        dss.errorinterface.code = code
        return message

    return on_compile


def test_missing_redirect_is_an_error(fake):
    message = 'Redirect File: "/feeder/IEEELineCodes.dss" Not Found.'
    fake.hooks.on_compile = _engine_reports(fake.dss, 243, message)

    result = fake.configuration.compile_opendss_file(str(fake.master))

    assert result["success"] is False
    assert "error 243" in result["error"]
    assert message in result["error"]
    assert fake.state.circuit_loaded is False
    assert fake.state.last_compiled_dss_file is None
    # The partly loaded circuit does not stay behind in the engine.
    assert fake.dss.commands[-1] == "ClearAll"


def test_an_earlier_commands_error_is_not_this_compiles(fake):
    fake.dss.errorinterface.code = 301

    result = fake.configuration.compile_opendss_file(str(fake.master))

    assert result["success"] is True, result
    assert "engine_message" not in result["payload"]
    assert fake.state.circuit_loaded is True


def test_editor_failure_from_show_does_not_refuse_the_circuit(fake):
    fake.hooks.on_compile = _engine_reports(
        fake.dss, 704, "FireOffEditor Error: command exited with nonzero status 1."
    )

    result = fake.configuration.compile_opendss_file(str(fake.master))

    assert result["success"] is True, result
    assert result["payload"]["engine_message"].startswith("error 704: FireOffEditor")
    assert fake.state.circuit_loaded is True


def test_a_failed_compile_forgets_the_previous_circuit(fake):
    first = fake.configuration.compile_opendss_file(str(fake.master))
    assert first["success"] is True and fake.state.circuit_loaded is True

    fake.hooks.on_compile = _engine_reports(fake.dss, 243, 'Redirect File: "x.dss" Not Found.')
    failed = fake.configuration.compile_opendss_file(str(fake.other))
    assert failed["success"] is False
    assert fake.state.circuit_loaded is False

    # The engine no longer holds the first circuit, so asking for it again
    # compiles it instead of reporting it as already loaded.
    fake.hooks.on_compile = lambda _path: ""
    again = fake.configuration.compile_opendss_file(str(fake.master))
    assert again["success"] is True
    assert again["payload"]["skipped"] is False


def test_missing_targets_are_refused_before_the_engine_runs(fake, tmp_path):
    (tmp_path / "codes.dss").write_text("New Linecode.a nphases=3\n")
    fake.master.write_text(
        "Clear\n"
        "redirect codes.dss\n"
        "Redirect NoSuchLineCodes.dss\n"
        "BusCoords missing_xy.csv\n"
    )

    result = fake.configuration.compile_opendss_file(str(fake.master))

    assert result["success"] is False
    assert "redirect NoSuchLineCodes.dss (master.dss, line 3)" in result["error"]
    assert "buscoords missing_xy.csv (master.dss, line 4)" in result["error"]
    assert "codes.dss" not in result["error"].replace("NoSuchLineCodes.dss", "")
    assert "OpenDSS was not run" in result["error"]
    assert not any(command.startswith("Compile") for command in fake.dss.commands)


def test_a_refused_file_leaves_the_loaded_circuit_alone(fake):
    assert fake.configuration.compile_opendss_file(str(fake.master))["success"] is True
    fake.other.write_text("redirect gone.dss\n")

    refused = fake.configuration.compile_opendss_file(str(fake.other))

    assert refused["success"] is False
    assert fake.state.circuit_loaded is True
    assert fake.state.last_compiled_dss_file == str(fake.master.resolve())


def test_a_compile_that_leaves_no_circuit_is_an_error(fake):
    # circuit_readiness() still says ready here, as py_dss_toolkit's does.
    fake.dss.circuit.num_ckt_elements = 0

    result = fake.configuration.compile_opendss_file(str(fake.master))

    assert result["success"] is False
    assert "loaded no circuit" in result["error"]
    assert "restarts" not in result["error"]
    assert fake.state.circuit_loaded is False


def test_an_empty_circuit_after_a_missing_file_error_says_to_restart(fake):
    fake.hooks.on_compile = _engine_reports(fake.dss, 243, 'Redirect File: "x.dss" Not Found.')
    assert fake.configuration.compile_opendss_file(str(fake.master))["success"] is False

    fake.hooks.on_compile = lambda _path: ""
    fake.dss.circuit.num_ckt_elements = 0
    result = fake.configuration.compile_opendss_file(str(fake.other))

    assert result["success"] is False
    assert "error 243" in result["error"]
    assert "until the server restarts" in result["error"]


# ---------------------------------------------------------------------------
# The real engine.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def opendss():
    """The OpenDSS server's tools over the real engine, loaded once per module."""
    pytest.importorskip("py_dss_toolkit", minversion="0.16")
    with pytest.MonkeyPatch.context() as mp:
        mp.syspath_prepend(str(OPENDSS_DIR))
        for name in _SERVER_MODULES:
            mp.delitem(sys.modules, name, raising=False)
        engine = importlib.import_module("core.engine")
        try:
            engine.ensure_engine()
        except engine.EngineUnavailable as exc:
            pytest.skip(str(exc))
        configuration = importlib.import_module("opendss_tools.configuration")
        mp.setattr(configuration, "allowed_roots", lambda: ())
        yield types.SimpleNamespace(
            configuration=configuration,
            simulation=importlib.import_module("opendss_tools.simulation"),
            state=importlib.import_module("core.state"),
        )
        with contextlib.suppress(Exception):
            configuration.clear_all_opendss_memory()


def _copy_13bus(tmp_path: Path) -> Path:
    feeder = tmp_path / "13Bus"
    shutil.copytree(CASE_13BUS, feeder)
    return feeder / "IEEE13Nodeckt.dss"


def _redirect_line_codes_to(master: Path, target: str) -> None:
    text = master.read_text()
    assert "redirect IEEELineCodes.DSS" in text
    master.write_text(text.replace("redirect IEEELineCodes.DSS", f"redirect {target}"))


def test_engine_compiles_and_solves_the_shipped_13bus(opendss):
    result = opendss.configuration.compile_opendss_file(
        str(CASE_13BUS / "IEEE13Nodeckt.dss"), force_recompile=True
    )
    assert result["success"] is True, result
    assert "engine_message" not in result["payload"]

    solved = opendss.simulation.solve_snapshot()
    assert solved["success"] is True, solved
    assert solved["payload"]["converged"] is True


def test_engine_feeder_with_a_missing_redirect_is_refused(opendss, tmp_path):
    opendss.configuration.clear_all_opendss_memory()
    master = _copy_13bus(tmp_path)
    _redirect_line_codes_to(master, "NoSuchLineCodes.dss")

    result = opendss.configuration.compile_opendss_file(str(master))

    assert result["success"] is False
    assert "redirect NoSuchLineCodes.dss (IEEE13Nodeckt.dss, line 46)" in result["error"]
    assert opendss.state.circuit_loaded is False
    refused = opendss.simulation.solve_snapshot()
    assert refused["success"] is False
    assert "No circuit loaded" in refused["error"]


def test_engine_feeder_with_a_missing_nested_redirect_is_refused(opendss, tmp_path):
    master = _copy_13bus(tmp_path)
    nested = master.parent / "codes"
    nested.mkdir()
    (nested / "all.dss").write_text("redirect NoSuchLineCodes.dss\n")
    _redirect_line_codes_to(master, "codes/all.dss")

    result = opendss.configuration.compile_opendss_file(str(master))

    assert result["success"] is False
    assert "redirect NoSuchLineCodes.dss (all.dss, line 1)" in result["error"]


def test_engine_issue_129_case_mismatch_is_refused(opendss, tmp_path):
    master = _copy_13bus(tmp_path)
    if (master.parent / "ieee13nodeckt.dss").exists():
        pytest.skip("case-insensitive filesystem: the engine finds the file under either case")
    _redirect_line_codes_to(master, "IEEELineCodes.dss")

    result = opendss.configuration.compile_opendss_file(str(master))

    assert result["success"] is False
    assert "redirect IEEELineCodes.dss" in result["error"]


def test_engine_still_loads_a_feeder_after_refusals(opendss, tmp_path):
    master = _copy_13bus(tmp_path)
    _redirect_line_codes_to(master, "NoSuchLineCodes.dss")
    assert opendss.configuration.compile_opendss_file(str(master))["success"] is False

    result = opendss.configuration.compile_opendss_file(str(CASE_13BUS / "IEEE13Nodeckt.dss"))
    assert result["success"] is True, result
    assert opendss.simulation.solve_snapshot()["payload"]["converged"] is True


def _node_voltages() -> dict[str, float]:
    dss = importlib.import_module("core.engine").dss
    return dict(zip(dss.circuit.nodes_names, dss.circuit.buses_vmag_pu))


def test_engine_solves_the_13bus_powerio_writes_like_the_original(opendss, tmp_path):
    """PowerIO regenerates the feeder; OpenDSS gives it the same voltages.

    ``sever_source`` drops the source text, so PowerIO writes every element
    itself instead of echoing the input files. Regulator controls are not
    carried, so both solves hold the taps where the files state them.
    """
    powerio = pytest.importorskip("powerio", minversion="0.11.5")  # sever_source
    original = CASE_13BUS / "IEEE13Nodeckt.dss"
    module = powerio.parse(original).sever_source()
    written = powerio.emit(module, "opendss", tmp_path / "written")
    assert written.fidelity == "canonical"
    master = next(a.path for a in written.artifacts if a.name.endswith(".dss"))

    voltages = []
    for path in (original, master):
        compiled = opendss.configuration.compile_opendss_file(str(path), force_recompile=True)
        assert compiled["success"] is True, compiled
        solved = opendss.simulation.solve_snapshot(control_mode="Off")
        assert solved["success"] is True and solved["payload"]["converged"] is True, solved
        voltages.append(_node_voltages())

    expected, actual = voltages
    assert set(actual) == set(expected)
    worst = max(abs(actual[node] - expected[node]) for node in expected)
    assert worst < 1e-3, worst


_MISSING_FILE_IN_THE_ENGINE = textwrap.dedent(
    """
    import importlib, json, sys
    sys.path.insert(0, sys.argv[1])
    configuration = importlib.import_module("opendss_tools.configuration")
    configuration.allowed_roots = lambda: ()
    engine = importlib.import_module("core.engine")
    try:
        engine.ensure_engine()
    except engine.EngineUnavailable as exc:
        print("REPORT " + json.dumps({"skip": str(exc)}), flush=True)
        raise SystemExit(0)
    first = configuration.compile_opendss_file(sys.argv[2])
    second = configuration.compile_opendss_file(sys.argv[3])
    loaded = engine.dss.circuit.num_ckt_elements > 0
    print("REPORT " + json.dumps({"first": first, "second": second, "loaded": bool(loaded)}), flush=True)
    """
)


def test_engine_missing_file_the_check_cannot_see(tmp_path):
    """A target named through a parser variable reaches the engine.

    It runs in its own interpreter because, on Linux, the engine loads nothing
    after it. Whatever the engine does next, the tool never reports success
    for an empty circuit.
    """
    pytest.importorskip("py_dss_toolkit", minversion="0.16")
    master = tmp_path / "variable.dss"
    master.write_text(
        "Clear\n"
        "New Circuit.variable basekv=4.16\n"
        "var @codes=NoSuchLineCodes.dss\n"
        "redirect @codes\n"
    )
    script = tmp_path / "run.py"
    script.write_text(_MISSING_FILE_IN_THE_ENGINE)
    proc = subprocess.run(
        [sys.executable, str(script), str(OPENDSS_DIR), str(master),
         str(CASE_13BUS / "IEEE13Nodeckt.dss")],
        capture_output=True, text=True, timeout=300, cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    # The engine writes its own messages to stdout around the report, and its
    # prompt (">>") has no newline after it.
    reports = [line.partition("REPORT ")[2] for line in proc.stdout.splitlines() if "REPORT " in line]
    assert reports, proc.stdout + proc.stderr
    report = json.loads(reports[-1])
    if "skip" in report:
        pytest.skip(report["skip"])

    first = report["first"]
    assert first["success"] is False
    assert "error 243" in first["error"]
    second = report["second"]
    if report["loaded"]:
        assert second["success"] is True, second
    else:
        assert second["success"] is False
        assert "error 243" in second["error"] and "restarts" in second["error"]


def test_engine_over_stdio(tmp_path):
    """The tools as an MCP client reaches them, on the SDK's worker threads.

    The OpenDSS interface crashes the process on some calls made off the main
    thread, which only a real session exercises.
    """
    pytest.importorskip("py_dss_toolkit", minversion="0.16")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    shipped = _copy_13bus(tmp_path / "shipped")
    broken = _copy_13bus(tmp_path / "broken")
    _redirect_line_codes_to(broken, "NoSuchLineCodes.dss")
    variable = tmp_path / "variable.dss"
    variable.write_text(
        "Clear\nNew Circuit.variable basekv=4.16\nvar @codes=NoSuchLineCodes.dss\nredirect @codes\n"
    )
    env = dict(os.environ, POWERIO_MCP_ALLOWED_ROOTS=str(tmp_path))
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "powermcp", "run", "opendss"],
        env=env, cwd=str(tmp_path),
    )

    async def session_results():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=120) as session:
                await asyncio.wait_for(session.initialize(), 120)

                async def call(name, **arguments):
                    result = await asyncio.wait_for(session.call_tool(name, arguments), 120)
                    return json.loads(result.content[0].text)

                return [
                    await call("compile_opendss_file", dss_file=str(broken)),
                    await call("compile_opendss_file", dss_file=str(shipped)),
                    await call("solve_snapshot"),
                    # Last: on Linux the engine loads nothing after this one.
                    await call("compile_opendss_file", dss_file=str(variable)),
                ]

    refused, compiled, solved, engine_error = asyncio.run(session_results())
    if "initialization failed" in compiled.get("error", ""):
        pytest.skip(compiled["error"])

    assert refused["success"] is False
    assert "redirect NoSuchLineCodes.dss (IEEE13Nodeckt.dss, line 46)" in refused["error"]
    assert compiled["success"] is True, compiled
    assert solved["success"] is True and solved["payload"]["converged"] is True, solved
    assert engine_error["success"] is False
    assert "error 243" in engine_error["error"]
    assert "NoSuchLineCodes.dss" in engine_error["error"]
