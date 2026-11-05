"""Files a DSS script asks OpenDSS to open, checked before the engine runs.

A ``Redirect``, ``Compile``, ``BusCoords`` or ``LatLongCoords`` whose file is
missing stops the compile where it fails (errors 242, 243, 275). The OpenDSS
C++ engine that py-dss-interface ships for Linux then loads nothing on every
later compile in the same process, so the server checks these targets first and
refuses the file without running the engine.

The scan follows the engine's path rules: a relative target resolves against
the directory of the file being read, a ``Redirect`` restores the directory
when it returns and a ``Compile`` does not, and a name without an extension
also matches ``<name>.dss``. Existence is the filesystem's own answer, so a
case-insensitive filesystem accepts a target in any case, as the engine does.
A target the scan cannot resolve, such as one using a parser variable, is left
to the engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_COMMAND = re.compile(
    r"^(redirect|compile|buscoords|latlongcoords)(?=[\s=(\[{\"']|$)\s*(.*)$",
    re.IGNORECASE,
)
_FILE_PARAMETER = re.compile(r"^file\s*=\s*", re.IGNORECASE)
_DELIMITERS = {'"': '"', "'": "'", "(": ")", "[": "]", "{": "}"}

# Bound the walk; a feeder that redirects through more files than this is
# left to the engine past the bound.
MAX_FILES = 2000


@dataclass(frozen=True)
class MissingReference:
    command: str
    target: str
    referenced_from: str
    line: int

    def describe(self) -> str:
        return f"{self.command} {self.target} ({Path(self.referenced_from).name}, line {self.line})"


def _first_argument(rest: str) -> str | None:
    rest = _FILE_PARAMETER.sub("", rest.strip(), count=1)
    if not rest:
        return None
    close = _DELIMITERS.get(rest[0])
    if close is not None:
        end = rest.find(close, 1)
        return rest[1:end] if end > 0 else rest[1:]
    return re.split(r"[\s,!]", rest, maxsplit=1)[0] or None


def _resolve(directory: Path, target: str) -> Path | None:
    candidate = directory / target.replace("\\", "/")
    if candidate.is_file():
        return candidate
    if not candidate.suffix:
        with_dss = candidate.with_name(candidate.name + ".dss")
        if with_dss.is_file():
            return with_dss
    return None


def _commands(path: Path):
    """``(line number, command, target)`` for each file command the engine would run."""
    in_block_comment = False
    for number, raw in enumerate(path.read_text(errors="replace").splitlines(), start=1):
        line = raw.strip()
        if not in_block_comment and line.startswith("/*"):
            in_block_comment = True
        if in_block_comment:
            if "*/" in line:
                in_block_comment = False
            continue
        match = _COMMAND.match(line)
        if match is None:
            continue
        target = _first_argument(match.group(2))
        if target:
            yield number, match.group(1).lower(), target


def missing_references(master: str | Path) -> list[MissingReference]:
    """Every file command reachable from ``master`` whose target does not exist."""
    master = Path(master)
    missing: list[MissingReference] = []
    visited: set[Path] = set()

    def walk(path: Path, directory: Path) -> Path:
        """Read ``path`` with ``directory`` current; return the directory it leaves."""
        resolved = path.resolve()
        if resolved in visited or len(visited) >= MAX_FILES:
            return directory
        visited.add(resolved)
        try:
            commands = list(_commands(path))
        except OSError:
            return directory  # unreadable: the engine reports it
        current = directory
        for number, command, target in commands:
            if "@" in target:
                continue  # parser variable: only the engine can expand it
            found = _resolve(current, target)
            if found is None:
                missing.append(MissingReference(command, target, str(path), number))
            elif command in ("redirect", "compile"):
                after = walk(found, found.parent)
                if command == "compile":
                    current = after
        return current

    walk(master, master.parent)
    return missing
