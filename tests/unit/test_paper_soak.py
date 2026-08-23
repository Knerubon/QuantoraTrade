from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from quantora_trade.domain.enums import TradingMode
from quantora_trade.validation.paper_soak import (
    PaperSoakGates,
    PaperSoakManifest,
    SoakIncident,
    SoakSample,
    SoakVerdict,
    evaluate_paper_soak,
    write_report,
)

START = datetime(2026, 8, 23, tzinfo=UTC)


def manifest(mode: TradingMode = TradingMode.PAPER) -> PaperSoakManifest:
    return PaperSoakManifest(
        run_id="paper-20260823",
        mode=mode,
        owner="owner",
        started_at=START,
        target_duration_seconds=120,
        sample_interval_seconds=60,
        config_version="paper-v1",
        config_sha256="a" * 64,
        code_version="abc123",
        data_version="mt5-demo-20260823",
    )


def sample(offset: int, *, orders: int = 0, audited: int = 0, ready: bool = True) -> SoakSample:
    return SoakSample(
        observed_at=START + timedelta(seconds=offset),
        health_ready=ready,
        orders_seen=orders,
        duplicate_orders=0,
        unknown_orders=0,
        audited_orders=audited,
        critical_events=0,
    )


def test_paper_soak_pass_is_deterministic_and_writes_both_formats(tmp_path) -> None:
    samples = (sample(0), sample(60, orders=1, audited=1), sample(120, orders=2, audited=2))
    report = evaluate_paper_soak(
        manifest=manifest(), samples=samples, incidents=(), gates=PaperSoakGates()
    )
    repeated = evaluate_paper_soak(
        manifest=manifest(), samples=samples, incidents=(), gates=PaperSoakGates()
    )

    assert report.verdict is SoakVerdict.PASS
    assert report.source_sha256 == repeated.source_sha256
    json_path, markdown_path = write_report(report, tmp_path)
    assert '"verdict": "pass"' in json_path.read_text()
    assert "does not authorize LIVE" in markdown_path.read_text()
    with pytest.raises(FileExistsError):
        write_report(report, tmp_path)


def test_gates_fail_for_short_unhealthy_incomplete_run_and_incident() -> None:
    incident = SoakIncident(START, "critical", "DB_DOWN", "database unavailable")
    report = evaluate_paper_soak(
        manifest=manifest(),
        samples=(sample(0), sample(60, orders=2, audited=1, ready=False)),
        incidents=(incident,),
        gates=PaperSoakGates(),
    )

    assert report.verdict is SoakVerdict.FAIL
    failed = {gate.code for gate in report.gates if not gate.passed}
    assert {
        "duration",
        "sample_count",
        "unhealthy_samples",
        "critical_incidents",
        "audit_complete",
    } <= failed


def test_live_mode_is_hard_rejected() -> None:
    with pytest.raises(PermissionError, match="LIVE is hard rejected"):
        manifest(TradingMode.LIVE)


def test_manifest_rejects_impossible_or_ambiguous_cadence() -> None:
    values = manifest()
    with pytest.raises(ValueError, match="at least one sample interval"):
        replace(values, target_duration_seconds=30)
    with pytest.raises(ValueError, match="exact multiple"):
        replace(values, target_duration_seconds=130)


def test_samples_must_be_sorted_and_not_empty() -> None:
    with pytest.raises(ValueError, match="at least one"):
        evaluate_paper_soak(manifest=manifest(), samples=(), incidents=(), gates=PaperSoakGates())
    with pytest.raises(ValueError, match="chronologically sorted"):
        evaluate_paper_soak(
            manifest=manifest(),
            samples=(sample(60), sample(0)),
            incidents=(),
            gates=PaperSoakGates(),
        )


def test_cumulative_metrics_cannot_decrease() -> None:
    with pytest.raises(ValueError, match="must never decrease"):
        evaluate_paper_soak(
            manifest=manifest(),
            samples=(sample(0, orders=2, audited=2), sample(60, orders=1, audited=1)),
            incidents=(),
            gates=PaperSoakGates(),
        )


@pytest.mark.parametrize(
    ("samples", "failed_gate"),
    [
        ((sample(11), sample(60), sample(120)), "first_sample_at_start"),
        ((sample(0), sample(30), sample(120)), "sample_cadence"),
        ((sample(0), sample(60), sample(109)), "last_sample_at_end"),
    ],
)
def test_soak_rejects_late_clustered_gapped_or_early_evidence(
    samples: tuple[SoakSample, ...], failed_gate: str
) -> None:
    report = evaluate_paper_soak(
        manifest=manifest(), samples=samples, incidents=(), gates=PaperSoakGates()
    )

    assert report.verdict is SoakVerdict.FAIL
    assert failed_gate in {gate.code for gate in report.gates if not gate.passed}
