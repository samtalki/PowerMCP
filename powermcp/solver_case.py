"""Select one typed PowerIO state for a balanced solver.

PowerIO owns parsing, IR validation, transformations and format emission.
The solver boundary requires explicit collection selection and lowering.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import powerio
from powermcp.sandbox import checked_path, checked_read_tree

_IR_FORMATS = frozenset({"pio-ir", "pio", "pio-json", "package"})


def diagnostic_messages(items: Any) -> tuple[str, ...]:
    """Keep stable diagnostic codes beside their user-facing descriptions."""
    return tuple(f"{item.code}: {item.message}" for item in items if item.severity != "info")


def _check_diagnostics(items: Any) -> None:
    failures = [item for item in items if item.severity in ("error", "fatal")]
    if failures:
        raise ValueError("PowerIO input fails validation: " + "; ".join(diagnostic_messages(failures)))


@dataclass(frozen=True)
class SolverCase:
    """One selected state, its typed network and its source context."""
    module: powerio.PioModule
    network: powerio.BalancedNetwork
    warnings: tuple[str, ...] = ()
    package: dict[str, Any] | None = None

    def emit(self, format: str, destination: Any = None):
        result = powerio.emit(self.module, format, destination)
        _check_diagnostics(result.diagnostics)
        return result


def _document(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    if value.get("schema") == "pio-ir":
        return value
    if "model_kind" in value and "model" in value:
        raise ValueError("legacy Package input requires migration to PowerIO IR generation 2")
    return None


def _select(module, time_index, scenario_id):
    selection = {}
    value = module.value
    while isinstance(value, (powerio.TimeSeries, powerio.ScenarioSet)):
        if isinstance(value, powerio.ScenarioSet):
            if scenario_id is None:
                raise ValueError("select scenario_id from the ScenarioSet before solving")
            value = value[scenario_id]
            selection["scenario_id"] = scenario_id
            scenario_id = None
        else:
            if time_index is None:
                raise ValueError("select time_index from the TimeSeries before solving")
            if isinstance(time_index, bool) or not isinstance(time_index, int) or time_index < 0:
                raise ValueError("time_index must be a nonnegative integer")
            value = value[time_index]
            selection["time_index"] = time_index
            time_index = None
    if time_index is not None or scenario_id is not None:
        raise ValueError("state selector does not match the input collection")
    return (powerio.PioModule.from_value(value) if selection else module), selection


def resolve_solver_case(
    *,
    file_path: str | None = None,
    network_json: str | None = None,
    source_format: str | None = None,
    time_index: int | None = None,
    scenario_id: str | None = None,
    operating_point: int | None = None,
    study_commit: int | None = None,
) -> SolverCase:
    """Resolve one file or JSON input without selecting or lowering implicitly.

    ``operating_point`` is a compatibility spelling for ``time_index``.
    Study history belongs to Tellegen; legacy package commits require migration.
    """
    if (file_path is None) == (network_json is None):
        raise ValueError("provide exactly one of file_path or network_json")
    if study_commit is not None:
        raise ValueError("study_commit requires a Tellegen Study state; export its PowerIO IR before using this solver")
    if operating_point is not None:
        if time_index is not None:
            raise ValueError("choose time_index or operating_point, not both")
        time_index = operating_point
    token = source_format.strip().lower().replace("_", "-") if source_format else None
    explicit_ir = token in _IR_FORMATS
    document = None
    if file_path is not None:
        path = Path(checked_path(file_path, purpose="file_path"))
        if path.is_dir():
            path = Path(checked_read_tree(str(path), purpose="file_path"))
        elif explicit_ir or path.suffix.lower() == ".json":
            document = _document(path.read_text(encoding="utf-8"))
        if explicit_ir and document is None:
            raise ValueError("input is not PowerIO IR")
        module = powerio.deserialize(path) if document is not None else powerio.parse(path, format=token)
    else:
        document = _document(network_json)
        if explicit_ir and document is None:
            raise ValueError("input is not PowerIO IR")
        payload = network_json.encode("utf-8")
        module = powerio.deserialize(payload) if document is not None else powerio.parse(payload, format=token, name="input.json")
    _check_diagnostics(module.diagnostics)
    diagnostics = list(module.diagnostics)
    module, selection = _select(module, time_index, scenario_id)
    _check_diagnostics(module.diagnostics)
    value = module.value
    if isinstance(value, powerio.OperatingPoint):
        projection = powerio.emit(module, "matpower")
        _check_diagnostics(projection.diagnostics)
        diagnostics.extend(projection.diagnostics)
        module = powerio.parse(projection.text.encode(), format="matpower", name="selected-state.m")
        value = module.value
    if isinstance(value, powerio.MulticonductorNetwork) or isinstance(
        value, (powerio.McAcPfInstance, powerio.McAcOpfInstance, powerio.McAcPfSolution, powerio.McAcOpfSolution)
    ):
        raise ValueError("this solver requires a balanced network; inspect PioModule.to_balanced_report() and explicitly call to_balanced() first")
    if isinstance(value, powerio.BalancedNetwork):
        # Constructing an instance checks electrical identities and reference coverage.
        checked = module.to_ac_pf_instance()
        _check_diagnostics(checked.diagnostics)
        network = value
    elif isinstance(value, (
        powerio.DcPfInstance, powerio.AcPfInstance, powerio.DcOpfInstance, powerio.AcOpfInstance,
        powerio.DcPfSolution, powerio.AcPfSolution, powerio.DcOpfSolution, powerio.AcOpfSolution,
        powerio.SocwrOpfSolution,
    )):
        network = value.network
    else:
        raise ValueError(f"unsupported solver input type: {type(value).__name__}")
    context = None
    if document is not None:
        context = {"schema": "pio-ir", "generation": document["version"],
                   "producer": document.get("producer"), "source_type": document["value"]["type"],
                   "selection": selection}
    warnings = tuple(dict.fromkeys(diagnostic_messages([*diagnostics, *module.diagnostics])))
    return SolverCase(module, network, warnings, context)
