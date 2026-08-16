"""Containment for paths that reach a server from the model.

An MCP tool argument is attacker-influenced input: whatever the model was
persuaded to ask for, the server does. A tool that takes a path and opens it
will read or write wherever the model points, so every such argument passes
through :func:`checked_path` first.

The policy is the same one the powerio MCP server applies, and reads the same
environment variable, so an operator configures containment once for the whole
installation:

``POWERIO_MCP_ALLOWED_ROOTS`` holds an ``os.pathsep`` separated list of
directories. Unset, nothing is constrained and servers behave as before — the
opt-in shape the powerio server already established. Set, a path must resolve
inside one of the roots.

Resolution happens before the check, so neither a ``..`` segment nor a symlink
pointing out of a root gets through: it is the real target that is compared,
not the spelling.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple
from urllib.parse import unquote, urlparse

__all__ = ["allowed_roots", "checked_path", "PathNotAllowed"]

_ALLOWED_ROOTS_ENV = "POWERIO_MCP_ALLOWED_ROOTS"
_LEGACY_ALLOWED_ROOT_ENV = "POWERIO_MCP_ALLOWED_ROOT"


class PathNotAllowed(ValueError):
    """A model supplied path that resolves outside the allowed roots."""


def allowed_roots() -> Tuple[Path, ...]:
    """The configured roots, or an empty tuple when containment is off."""
    raw = os.environ.get(_ALLOWED_ROOTS_ENV) or os.environ.get(_LEGACY_ALLOWED_ROOT_ENV)
    if not raw:
        return ()
    roots = []
    for entry in raw.split(os.pathsep):
        item = entry.strip()
        if item:
            roots.append(Path(item).expanduser().resolve(strict=False))
    return tuple(roots)


def _decode(value: str, *, purpose: str) -> Path:
    """A local path from a plain path or a ``file://`` URI.

    A non-local URI scheme is refused outright rather than being handed to an
    opener that might fetch it.
    """
    text = str(value)
    parsed = urlparse(text)
    windows_drive = os.name == "nt" and len(parsed.scheme) == 1
    if parsed.scheme and parsed.scheme != "file" and not windows_drive:
        raise PathNotAllowed(
            f"`{purpose}` must be a local path or a file:// URI, got scheme "
            f"`{parsed.scheme}`"
        )
    if parsed.scheme == "file":
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            raise PathNotAllowed(f"`{purpose}` must name a local file")
        text = unquote(parsed.path)
        if os.name == "nt" and len(text) > 2 and text[0] == "/" and text[2] == ":":
            text = text[1:]
    return Path(text).expanduser()


def _resolve(path: Path, *, for_write: bool) -> Path:
    try:
        if for_write and not path.exists():
            parent = path.parent if path.parent != Path("") else Path(".")
            candidate = parent.resolve(strict=True) / path.name
            # `exists()` follows symlinks, so a dangling symlink as the final
            # component lands here. Joining the name onto the resolved parent
            # would leave that link unresolved and the check would pass on the
            # link's own location while the write followed it out.
            return Path(os.path.realpath(candidate))
        return path.resolve(strict=True)
    except FileNotFoundError:
        if for_write:
            raise
        return path.resolve(strict=False)


def checked_path(value: str, *, purpose: str, for_write: bool = False) -> str:
    """Return `value` as a path string, refusing anything outside the roots.

    `purpose` names the argument in the error, so a refusal says which tool
    input was rejected rather than only that something was.
    """
    path = _decode(value, purpose=purpose)
    roots = allowed_roots()
    if not roots:
        return str(path)
    try:
        resolved = _resolve(path, for_write=for_write)
    except OSError as exc:
        raise PathNotAllowed(
            f"cannot resolve `{purpose}` against allowed MCP roots: {exc}"
        ) from exc
    for root in roots:
        if resolved == root or root in resolved.parents:
            return str(path)
    root_list = ", ".join(str(root) for root in roots)
    raise PathNotAllowed(f"`{purpose}` is outside allowed MCP roots: {root_list}")
