#!/usr/bin/env python3
"""Explicitly evaluate captured PAPER soak observations; never controls trading."""

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from quantora_trade.domain.enums import TradingMode
from quantora_trade.validation.paper_soak import (
    PaperSoakGates,
    PaperSoakManifest,
    SoakIncident,
    SoakSample,
    evaluate_paper_soak,
    write_report,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate a bounded, owner-approved PAPER soak observation file."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument(
        "--acknowledge-paper-only",
        action="store_true",
        help="required acknowledgement; this command never enables or controls trading",
    )
    return parser


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _load(
    path: Path,
) -> tuple[
    PaperSoakManifest,
    tuple[SoakSample, ...],
    tuple[SoakIncident, ...],
    PaperSoakGates,
]:
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    manifest_raw = raw["manifest"]
    manifest = PaperSoakManifest(
        run_id=manifest_raw["run_id"],
        mode=TradingMode(manifest_raw["mode"]),
        owner=manifest_raw["owner"],
        started_at=_time(manifest_raw["started_at"]),
        target_duration_seconds=manifest_raw["target_duration_seconds"],
        sample_interval_seconds=manifest_raw["sample_interval_seconds"],
        config_version=manifest_raw["config_version"],
        config_sha256=manifest_raw["config_sha256"],
        code_version=manifest_raw["code_version"],
        data_version=manifest_raw["data_version"],
    )
    samples = tuple(
        SoakSample(observed_at=_time(item.pop("observed_at")), **item)
        for original in raw["samples"]
        for item in [dict(original)]
    )
    incidents = tuple(
        SoakIncident(occurred_at=_time(item.pop("occurred_at")), **item)
        for original in raw.get("incidents", [])
        for item in [dict(original)]
    )
    return manifest, samples, incidents, PaperSoakGates(**raw.get("gates", {}))


def main() -> int:
    args = _parser().parse_args()
    if not args.acknowledge_paper_only:
        raise SystemExit("refusing: pass --acknowledge-paper-only after owner approval")
    manifest, samples, incidents, gates = _load(args.input)
    report = evaluate_paper_soak(
        manifest=manifest,
        samples=samples,
        incidents=incidents,
        gates=gates,
    )
    paths = write_report(report, args.output_directory)
    print(json.dumps({"artifacts": [str(path) for path in paths], "verdict": report.verdict}))
    return 0 if report.verdict == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
