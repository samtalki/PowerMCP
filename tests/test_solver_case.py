"""PowerIO's package model is the single case boundary used by solver tools."""

from __future__ import annotations

import json
import os
from pathlib import Path

import powerio
import pytest

from powermcp.sandbox import PathNotAllowed
from powermcp.solver_case import _available_indexes, _index_inventory, resolve_solver_case

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


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_directory_case_refuses_a_symlinked_descendant_outside_roots(
    tmp_path, monkeypatch
):
    root = tmp_path / "allowed"
    root.mkdir()
    dataset = root / "dataset"
    powerio.parse_file(CASE9).write_pypsa_csv_folder(dataset)
    outside = tmp_path / "outside-buses.csv"
    (dataset / "buses.csv").replace(outside)
    (dataset / "buses.csv").symlink_to(outside)
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(root))

    with pytest.raises(PathNotAllowed, match="outside its allowed MCP root"):
        resolve_solver_case(file_path=str(dataset), source_format="pypsa-csv")


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


def test_package_validation_is_recomputed_after_deserialization():
    document = json.loads(powerio.Package.from_file(CASE9).to_json())
    network = document["model"]["balanced_network"]
    network["buses"][1]["id"] = network["buses"][0]["id"]
    assert document["validation"]["status"] == "ok"

    with pytest.raises(ValueError, match="fails validation"):
        resolve_solver_case(network_json=json.dumps(document))


@pytest.mark.parametrize(
    ("field", "empty_value"),
    [
        (
            "operating_points",
            {"time_axis": {"periods": 0, "labels": []}, "points": []},
        ),
        ("study", {"label": "empty study", "commits": []}),
    ],
)
def test_empty_package_state_metadata_remains_a_static_case(field, empty_value):
    document = json.loads(powerio.Package.from_file(CASE9).to_json())
    document[field] = empty_value

    resolved = resolve_solver_case(network_json=json.dumps(document))

    assert resolved.network.n_buses == 9
    assert "materialized" not in resolved.package


def test_exactly_one_interchange_input_is_required():
    with pytest.raises(ValueError, match="exactly one"):
        resolve_solver_case()
    with pytest.raises(ValueError, match="exactly one"):
        resolve_solver_case(file_path="case.m", network_json="{}")


def test_large_state_inventory_is_compact():
    indexes = list(range(8760))

    assert _available_indexes(indexes) == "0..8759 (8760 available)"
    assert _index_inventory(indexes) == {
        "count": 8760,
        "first": 0,
        "last": 8759,
    }


def test_sparse_large_state_indexes_do_not_materialize_the_range():
    indexes = [*range(20), 1_000_000_000]

    assert _available_indexes(indexes) == (
        "[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, ...] "
        "(21 available; last 1000000000)"
    )


def test_temporary_integration_module_names_are_absent():
    package_dir = Path(__file__).parents[1] / "powermcp"
    assert not (package_dir / "powerio_bridge.py").exists()
    assert not (package_dir / "powerio_server.py").exists()
    assert not (package_dir / "powerio_handoff.py").exists()
