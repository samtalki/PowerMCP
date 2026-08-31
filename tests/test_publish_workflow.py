"""The PyPI workflow publishes only an existing immutable release tag."""

from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "publish.yml").read_text(
    encoding="utf-8"
)
TEST_WORKFLOW = (ROOT / ".github" / "workflows" / "test.yml").read_text(
    encoding="utf-8"
)
POWERIO_CANDIDATE = "e39afc9d669349df5133ff6140598010a498cdb1"


def test_publish_workflow_actions_use_full_commit_shas():
    uses = re.findall(r"^\s*-?\s*uses:\s*[^@\s]+@([^\s#]+)", WORKFLOW, re.MULTILINE)
    assert uses
    assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for revision in uses)


def test_manual_publish_requires_and_checks_out_an_existing_tag():
    assert re.search(r"workflow_dispatch:\s*\n\s*inputs:\s*\n\s*tag:", WORKFLOW)
    assert "ref: refs/tags/${{ steps.candidate.outputs.tag }}" in WORKFLOW
    assert 'git show-ref --verify --quiet "refs/tags/$TAG"' in WORKFLOW
    assert 'git rev-parse "$TAG^{commit}"' in WORKFLOW
    assert 'git ls-remote origin "refs/tags/$TAG^{}"' in WORKFLOW


def test_publish_requires_matching_version_and_published_release():
    assert '[[ "$TAG" != "v$version" ]]' in WORKFLOW
    assert "releases/tags/$TAG" in WORKFLOW
    assert "select(.draft == false and .prerelease == false)" in WORKFLOW


def test_sdist_excludes_local_virtual_environments_and_build_caches():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)
    excluded = project["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"]
    assert {".venv*", "**/.venv*", ".uv-build-cache", "**/.uv-build-cache"} <= set(
        excluded
    )


def test_ci_installs_the_exact_powerio_candidate_before_powermcp():
    direct_reference = (
        "powerio[mcp,matrix] @ git+https://github.com/eigenergy/powerio.git@"
        f"{POWERIO_CANDIDATE}"
    )
    candidate_install = TEST_WORKFLOW.index(direct_reference)
    editable_install = TEST_WORKFLOW.index("python -m pip install -e . pytest")

    assert candidate_install < editable_install
    assert "Remove this step once PowerIO 1.0.0 is published on PyPI." in TEST_WORKFLOW
