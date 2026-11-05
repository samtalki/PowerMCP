"""The file targets compile_opendss_file checks before OpenDSS runs."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_MODULE = Path(__file__).resolve().parents[1] / "OpenDSS" / "utils" / "dss_references.py"
_spec = importlib.util.spec_from_file_location("opendss_dss_references", _MODULE)
# Loaded by path: the server's ``utils`` package name is shared with test fakes.
dss_references = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = dss_references
_spec.loader.exec_module(dss_references)

CASE_13BUS = Path(__file__).resolve().parents[1] / "OpenDSS" / "13Bus"


def _missing(master: Path) -> list[tuple[str, str, str, int]]:
    return [
        (m.command, m.target, Path(m.referenced_from).name, m.line)
        for m in dss_references.missing_references(master)
    ]


def test_the_shipped_13bus_names_no_missing_file():
    for master in CASE_13BUS.glob("*.dss"):
        assert _missing(master) == [], master.name


def test_each_file_command_is_checked(tmp_path):
    master = tmp_path / "master.dss"
    master.write_text(
        "Clear\n"
        "Redirect gone_a.dss\n"
        "compile gone_b.dss\n"
        "BusCoords gone_xy.csv\n"
        "LatLongCoords gone_ll.csv\n"
        "New Line.l1 bus1=a bus2=b\n"
    )
    assert _missing(master) == [
        ("redirect", "gone_a.dss", "master.dss", 2),
        ("compile", "gone_b.dss", "master.dss", 3),
        ("buscoords", "gone_xy.csv", "master.dss", 4),
        ("latlongcoords", "gone_ll.csv", "master.dss", 5),
    ]


@pytest.mark.parametrize(
    "spelling",
    [
        "redirect codes.dss",
        "Redirect codes.dss ! trailing comment",
        "redirect codes.dss!comment",
        'redirect "codes.dss"',
        "redirect 'codes.dss'",
        "redirect (codes.dss)",
        "redirect [codes.dss]",
        "redirect {codes.dss}",
        "redirect file=codes.dss",
        "  REDIRECT\tcodes.dss",
        "redirect codes",  # the engine also tries codes.dss
    ],
)
def test_target_spellings(tmp_path, spelling):
    (tmp_path / "codes.dss").write_text("")
    master = tmp_path / "master.dss"
    master.write_text(spelling + "\n")
    assert _missing(master) == []
    (tmp_path / "codes.dss").unlink()
    assert len(_missing(master)) == 1


def test_comments_are_not_commands(tmp_path):
    master = tmp_path / "master.dss"
    master.write_text(
        "! redirect gone_a.dss\n"
        "// redirect gone_b.dss\n"
        "/* block comment\n"
        "redirect gone_c.dss\n"
        "*/\n"
        "/* one line */\n"
        "redirectory gone_d.dss\n"
    )
    assert _missing(master) == []


def test_nested_targets_resolve_against_their_own_file(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "codes.dss").write_text("redirect more.dss\nredirect gone.dss\n")
    (tmp_path / "sub" / "more.dss").write_text("")
    (tmp_path / "more.dss").write_text("")
    master = tmp_path / "master.dss"
    master.write_text("redirect sub/codes.dss\nredirect more.dss\n")

    assert _missing(master) == [("redirect", "gone.dss", "codes.dss", 2)]


def test_a_redirect_restores_the_directory_and_a_compile_does_not(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "inner.dss").write_text("")
    (tmp_path / "sub" / "only_in_sub.csv").write_text("")
    (tmp_path / "only_at_top.csv").write_text("")
    redirected = tmp_path / "redirected.dss"
    redirected.write_text(
        "redirect sub/inner.dss\nBusCoords only_at_top.csv\nBusCoords only_in_sub.csv\n"
    )
    assert _missing(redirected) == [("buscoords", "only_in_sub.csv", "redirected.dss", 3)]

    compiled = tmp_path / "compiled.dss"
    compiled.write_text(
        "compile sub/inner.dss\nBusCoords only_in_sub.csv\nBusCoords only_at_top.csv\n"
    )
    assert _missing(compiled) == [("buscoords", "only_at_top.csv", "compiled.dss", 3)]


def test_cycles_end(tmp_path):
    (tmp_path / "a.dss").write_text("redirect b.dss\n")
    (tmp_path / "b.dss").write_text("redirect a.dss\nredirect gone.dss\n")
    assert _missing(tmp_path / "a.dss") == [("redirect", "gone.dss", "b.dss", 2)]


def test_parser_variables_are_left_to_the_engine(tmp_path):
    master = tmp_path / "master.dss"
    master.write_text("var @codes=gone.dss\nredirect @codes\n")
    assert _missing(master) == []


def test_case_follows_the_filesystem(tmp_path):
    (tmp_path / "IEEELineCodes.DSS").write_text("")
    master = tmp_path / "master.dss"
    master.write_text("redirect IEEELineCodes.dss\n")
    case_insensitive = (tmp_path / "ieeelinecodes.dss").exists()
    assert (_missing(master) == []) is case_insensitive
