"""PowerIO's package model is the single case boundary used by solver tools."""

from __future__ import annotations

import json
from pathlib import Path

import powerio
import pytest

from powermcp.solver_case import resolve_solver_case

CASE9 = Path(__file__).parent / "data" / "case9.m"


def test_case_file_uses_powerio_package_validation(monkeypatch):
    monkeypatch.setattr(
        powerio,
        "parse_file",
        lambda *args, **kwargs: pytest.fail("use Package.from_file for case inputs"),
    )
    resolved = resolve_solver_case(file_path=str(CASE9))

    assert resolved.network.n_buses == 9
    assert resolved.package is None


def test_package_file_preserves_auditable_context(tmp_path):
    package = powerio.Package.from_file(CASE9)
    document = json.loads(package.to_json())
    document["package_id"] = "dispatch-input"
    source = tmp_path / "case.pio.json"
    source.write_text(json.dumps(document))

    resolved = resolve_solver_case(file_path=str(source))

    assert resolved.network.n_buses == 9
    assert resolved.package["package_id"] == "dispatch-input"
    assert resolved.package["validation"]["status"] == "ok"
    assert resolved.package["source_map_entries"] > 0


def test_exactly_one_interchange_input_is_required():
    with pytest.raises(ValueError, match="exactly one"):
        resolve_solver_case()
    with pytest.raises(ValueError, match="exactly one"):
        resolve_solver_case(file_path="case.m", network_json="{}")


def test_temporary_integration_module_names_are_absent():
    package_dir = Path(__file__).parents[1] / "powermcp"
    assert not (package_dir / "powerio_bridge.py").exists()
    assert not (package_dir / "powerio_server.py").exists()
    assert not (package_dir / "powerio_handoff.py").exists()
