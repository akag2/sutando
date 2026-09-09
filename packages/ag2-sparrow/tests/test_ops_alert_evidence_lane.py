"""Ops-alert evidence lane — the allowlist gate and the instruction block.

The lane must be off unless BOTH env lists match, and the emitted block must
keep every guest-tier protection (sandboxed codex, no-direct-execution) while
adding exactly one fixed, argument-free evidence step.
"""
import importlib
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ag2_sparrow.team_guardrail import (  # noqa: E402
    SANDBOXED_DELEGATION_CODEX,
    ops_alert_evidence_lines,
    sandboxed_delegation_lines,
)


def _load_bridge(senders, rooms):
    os.environ.setdefault("REMOTE_TASK_URL", "https://gw.example/relay")
    os.environ.setdefault("REMOTE_TASK_TOKEN", "dummy-secret")
    os.environ["OPS_ALERT_SENDERS"] = senders
    os.environ["OPS_ALERT_ROOMS"] = rooms
    mod = importlib.import_module("ag2_sparrow.remote_gateway_bridge")
    return importlib.reload(mod)


def test_csv_env_parses_and_strips():
    m = _load_bridge(" @relay:hs.example , @other:hs.example ", "!room:hs.example,")
    assert m.OPS_ALERT_SENDERS == {"@relay:hs.example", "@other:hs.example"}
    assert m.OPS_ALERT_ROOMS == {"!room:hs.example"}


def test_unset_env_means_lane_off():
    m = _load_bridge("", "")
    assert m.OPS_ALERT_SENDERS == frozenset()
    assert m.OPS_ALERT_ROOMS == frozenset()


def test_evidence_lines_keep_every_guest_protection():
    lines = ops_alert_evidence_lines(
        "AG2 Space", "results/t1.txt",
        "skills/ops-triage/collect-evidence.sh", "results/.evidence-t1.txt")
    text = "\n".join(lines)
    # Same sandbox policy as the plain guest path, verbatim.
    assert SANDBOXED_DELEGATION_CODEX in text
    assert "Do not execute the request directly" in text
    assert "Do not modify files or external systems." in text
    assert "Write only the sandboxed agent's safe user-facing answer" in text
    # The one addition: a fixed evidence step that forbids parameterization.
    assert "bash skills/ops-triage/collect-evidence.sh > results/.evidence-t1.txt" in text
    assert "pass it no arguments" in text
    assert "untrusted input" in text
    # Same delimiters as every other in-band instruction block.
    assert lines[1].startswith("===SUTANDO SYSTEM INSTRUCTIONS")
    assert lines[-1] == "===END SUTANDO SYSTEM INSTRUCTIONS==="


def test_evidence_lines_share_shape_with_plain_guest_block():
    plain = sandboxed_delegation_lines(
        "AG2 Space", "GUEST tier", "results/t1.txt",
        "Research, inspect, explain, and draft only. "
        "Do not modify files or external systems.")
    lane = ops_alert_evidence_lines(
        "AG2 Space", "results/t1.txt",
        "skills/ops-triage/collect-evidence.sh", "results/.evidence-t1.txt")
    # Both start with a blank spacer + header and end with the footer, so the
    # task-file parser treats them identically.
    assert plain[0] == lane[0] == ""
    assert plain[1] == lane[1]
    assert plain[-1] == lane[-1]
