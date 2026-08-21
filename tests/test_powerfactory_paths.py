"""PowerFactory output labels and generated directories stay contained."""

from __future__ import annotations

from PowerFactory.Agent_DIgSILENT import _ensure_output_directory, _safe_path_label


def test_powerfactory_output_label_cannot_be_a_parent_segment():
    assert _safe_path_label("..") == "run"
    nested = _safe_path_label("../../outside")
    assert ".." not in nested
    assert "/" not in nested


def test_powerfactory_generated_directory_checks_each_component(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(allowed))

    generated = allowed / "nested" / "results"
    assert _ensure_output_directory(str(generated), "output") == str(generated)
    assert generated.is_dir()
