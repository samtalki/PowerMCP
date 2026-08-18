"""Containment for paths that reach a server from the model.

An MCP tool argument is attacker-influenced input: whatever the model was
persuaded to ask for, the server does. A tool that takes a path and opens it
will read or write wherever the model points, so every such argument passes
through :func:`checked_path` first.

The policy itself lives in ``powerio.mcp.sandbox`` and this module only
re-exports it. powerio is a core dependency, the powerio MCP server already
applies that policy to its own ``path`` and ``out_path`` arguments, and
``powerio.mcp.sandbox`` imports nothing but the standard library, so there is
no second copy to keep in step. Operators configure containment once, with
``POWERIO_MCP_ALLOWED_ROOTS`` (an ``os.pathsep`` separated list of directories)
or one of the legacy single root spellings powerio still reads. Unset, nothing
is constrained.

Resolution happens before the check, so neither a ``..`` segment nor a symlink
pointing out of a root gets through: it is the real target that is compared,
not the spelling.
"""

from __future__ import annotations

from powerio.mcp.sandbox import (
    ALLOWED_ROOTS_ENV,
    LEGACY_ROOT_ENVS,
    allowed_roots,
    check_allowed_path,
    checked_path,
    decode_local_path,
)

# powerio signals a refusal with a plain ValueError. The alias keeps the bridge
# servers reading as intent; narrow it to a dedicated type if powerio grows one.
PathNotAllowed = ValueError

__all__ = [
    "ALLOWED_ROOTS_ENV",
    "LEGACY_ROOT_ENVS",
    "PathNotAllowed",
    "allowed_roots",
    "check_allowed_path",
    "checked_path",
    "decode_local_path",
]
