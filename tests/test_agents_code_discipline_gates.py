"""Raven-Code prompt gates: pre-write placement/ConsequenceMatrix and external-reviewer PR evidence."""
from pathlib import Path

CODE = Path(__file__).resolve().parents[1] / "agents" / "raven-code"
DISCIPLINE = CODE / "plugins" / "code-flow" / "prompts" / "CODE_DISCIPLINE.md"
SOUL = CODE / "soul.md"
MACHINE_LOCAL = (r"C:\Users", "C:/Users", "AppData", "test_local_setup_e2e")


def test_discipline_requires_pre_write_placement_and_consequence_matrix():
    text = DISCIPLINE.read_text(encoding="utf-8")
    for needle in ("Pre-Write Placement", "ConsequenceMatrix", "blast radius", "rollback", "drift", "/ship", "loose script"):
        assert needle.lower() in text.lower(), f"discipline lacks {needle!r}"


def test_discipline_requires_external_reviewer_pr_evidence():
    text = DISCIPLINE.read_text(encoding="utf-8")
    for needle in ("External-Reviewer PR Evidence", "repo-relative", "user.name", "user.email", "## Evidences"):
        assert needle.lower() in text.lower(), f"discipline lacks {needle!r}"


def test_soul_carries_non_repetition_rule_and_keeps_identity():
    text = SOUL.read_text(encoding="utf-8")
    assert text.startswith("# Soul\n\nI am Raven-Code, a coding agent.")
    assert "non-repetition" in text.lower() and "automated test" in text.lower()


def test_gate_text_itself_has_no_machine_local_paths():
    for path in (DISCIPLINE, SOUL):
        body = path.read_text(encoding="utf-8")
        body = "\n".join(l for l in body.splitlines() if "zero machine-local" not in l.lower() and "never cite" not in l.lower())
        for bad in MACHINE_LOCAL:
            assert bad not in body, f"{path.name} leaks {bad!r}"
