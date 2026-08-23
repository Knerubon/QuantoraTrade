"""Prevent accidental application-level bypass of the submission service."""

from pathlib import Path


def test_only_submission_boundary_and_concrete_paper_adapter_reference_broker_port() -> None:
    source_root = Path(__file__).parents[2] / "src" / "quantora_trade"
    references = {
        path.relative_to(source_root).as_posix()
        for path in source_root.rglob("*.py")
        if "BrokerPort" in path.read_text(encoding="utf-8")
    }
    assert references == {
        "domain/ports.py",
        "execution/__init__.py",
        "execution/service.py",
        "risk/submission.py",
    }
