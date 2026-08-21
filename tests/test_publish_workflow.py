"""The PyPI workflow publishes only an existing immutable release tag."""

from __future__ import annotations

import re
from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "publish.yml"
).read_text(encoding="utf-8")


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
