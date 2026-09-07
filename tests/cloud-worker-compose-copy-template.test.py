#!/usr/bin/env python3
r"""The COMMENTED copy-me template must render an isolated second user.

`cloud-worker-compose-isolation.test.py` walks `doc["services"]`, so it can only
ever see the ACTIVE pair — a commented block is invisible to it by construction.
That leaves the one artifact an operator actually copies untested: the template
shipped with no `networks:` key rendered a worker on the project default
network, which is the exact condition that test exists to prevent.

Here the template is uncommented the way an operator would and then parsed.
"""
import pathlib
import re
import sys

try:
    import yaml
except ImportError:                                  # pragma: no cover
    print("SKIP — pyyaml not available")
    sys.exit(0)

COMPOSE = pathlib.Path(__file__).resolve().parent.parent / "deploy" / "cloud-worker" / "docker-compose.yml"

# A template line is a commented key for the alice pair, or its indented body.
# Prose comments carry a single space after `#`, so they are left commented.
NAME = re.compile(r"^(\s*)#\s((?:worker-|assistant-)?alice[\w-]*:)\s*$")
BODY = re.compile(r"^(\s*)#(\s{3,}\S.*)$")

FAILS = []


def check(ok: bool, msg: str) -> None:
    print(("  ok   " if ok else "  FAIL ") + msg)
    if not ok:
        FAILS.append(msg)


def uncomment(raw: str) -> str:
    out = []
    for line in raw.split("\n"):
        m = NAME.match(line) or BODY.match(line)
        out.append(m.group(1) + m.group(2) if m else line)
    return "\n".join(out)


def main() -> int:
    raw = COMPOSE.read_text()
    rendered = uncomment(raw)
    try:
        doc = yaml.safe_load(rendered)
    except yaml.YAMLError as exc:
        check(False, f"the uncommented template parses as YAML: {exc}")
        return 1

    svc = doc.get("services") or {}
    check(set(svc) >= {"worker-example", "assistant-example", "worker-alice", "assistant-alice"},
          f"uncommenting yields BOTH user pairs: {sorted(svc)}")

    for name in ("worker-alice", "assistant-alice"):
        nets = (svc.get(name) or {}).get("networks") or []
        check(bool(nets), f"{name}: declares a network (no key = project default = shared)")
        check(list(nets) == ["alice"], f"{name}: on its own network, not example's: {nets}")

    ex = set((svc.get("worker-example") or {}).get("networks") or [])
    al = set((svc.get("worker-alice") or {}).get("networks") or [])
    check(ex and al and not (ex & al),
          f"the two users share NO network: example={sorted(ex)} alice={sorted(al)}")

    declared = set((doc.get("networks") or {}).keys())
    check({"example", "alice"} <= declared, f"both networks are declared top-level: {sorted(declared)}")

    tok_ex = str(((svc.get("assistant-example") or {}).get("environment") or {}).get("AG2ASSISTANT_ACP_TOKEN") or "")
    tok_al = str(((svc.get("assistant-alice") or {}).get("environment") or {}).get("AG2ASSISTANT_ACP_TOKEN") or "")
    check(tok_ex and tok_al and tok_ex != tok_al,
          f"each sidecar reads its OWN token var: {tok_ex!r} vs {tok_al!r}")
    check(re.fullmatch(r"\$\{AG2ASSISTANT_ACP_TOKEN_[A-Z0-9_]+:?-?\}", tok_al) is not None,
          f"alice's sidecar token is a per-user var, not the project-global one: {tok_al!r}")

    vols = set((doc.get("volumes") or {}).keys())
    check({"worker-alice", "assistant-alice-data", "assistant-alice-workspace"} <= vols,
          f"alice's volumes are declared: {sorted(vols)}")

    print("\n" + (f"FAILED ({len(FAILS)})" if FAILS else "PASS — the copy-me template renders an isolated user"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
