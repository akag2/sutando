#!/usr/bin/env python3
"""Discovery for skill-declared Claude Code hooks (`hooks` in a skill manifest).

One owner: the installer registers what this returns and the health probe
verifies exactly that, so a drifted second copy cannot make them disagree.
"""
from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

# Legacy shapes are joined with this in the 4th field so the installer's sweep can
# match every form an earlier revision wrote (a path may hold any byte but NUL).
LEGACY_SEP = "\x1e"


def _dq(path: str) -> str:
    """Escape for the inside of a double-quoted shell word (no word-splitting there)."""
    return path.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")


def python_runner(repo_dir: Path) -> str:
    """The interpreter a .py hook execs, resolved at EVENT time by the launcher's policy:
    SUTANDO_PY from the core's environment, else the bundled interpreter beside the engine
    (fixed at install time when present), else PATH python3. A bare `python3` reached the
    broken PATH — or Apple's CLT stub — on exactly the hosts the resolver exists for."""
    bundled = Path(repo_dir).resolve().parent / "runtime" / "python" / "bin" / "python3"
    fallback = _dq(str(bundled)) if os.access(bundled, os.X_OK) else "python3"
    return '"${SUTANDO_PY:-' + fallback + '}"'


def runner_for(target: Path, repo_dir: Path) -> str:
    return python_runner(repo_dir) if target.suffix == ".py" else "bash"


def resolve_hook_command(skill_dir: Path, command: str) -> Path | None:
    """Resolved hook path, or None when it lands outside the declaring skill.

    An absolute command needs no `..` to escape: `skill_dir / "/bin/sh"` is
    `/bin/sh`, which would let a manifest point core at any host executable.
    """
    if not command or Path(command).is_absolute():
        return None
    root = Path(skill_dir).resolve()
    target = (root / command).resolve()
    return target if root in target.parents else None


def discover(repo_dir: Path) -> list[tuple[str, str, str, str]]:
    """(event, token, command, legacy_commands) per declared, present, enabled hook.
    legacy_commands joins with LEGACY_SEP every shape an earlier revision wrote for this
    hook, emitted (not derived by splitting on `exec `) so the installer's sweep replaces them."""
    out: list[tuple[str, str, str, str]] = []
    for manifest in sorted(Path(repo_dir).glob("skills/*/manifest.json")):
        try:
            data = json.loads(manifest.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or data.get("enabled") is False:
            continue
        entries = data.get("hooks")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            event, command = entry.get("event"), entry.get("command")
            if not isinstance(event, str) or not isinstance(command, str):
                continue
            target = resolve_hook_command(manifest.parent, command)
            if target is None or not target.is_file():
                continue
            runner = runner_for(target, repo_dir)
            q = shlex.quote(str(target))
            # The path is in the working tree, so a checkout can delete it while the
            # registration survives; a hook that cannot start blocks the tool it gates.
            cmd = f"[ -f {q} ] || exit 0; exec {runner} {q}"
            bare = "python3" if target.suffix == ".py" else "bash"
            legacy = LEGACY_SEP.join((f"{bare} {q}", f"[ -f {q} ] || exit 0; exec {bare} {q}"))
            out.append((event, target.name, cmd, legacy))
    return out


if __name__ == "__main__":
    import sys
    # NUL-framed: two fields carry a repo path, and a path may contain any byte
    # except NUL — including the `|` the reader would otherwise split on.
    out = sys.stdout.buffer
    for row in discover(Path(sys.argv[1])):
        for field in row:
            out.write(field.encode() + b"\0")
    out.flush()
