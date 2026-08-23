#!/usr/bin/env python3
"""Run one explicit, bounded PAPER soak against QuantoraTrade's control API."""

import argparse
import os
from pathlib import Path

from quantora_trade.validation.paper_soak_runner import (
    ControlPlaneClient,
    PaperSoakRunner,
    RunnerSettings,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Run a bounded PAPER-only soak on Windows or POSIX."
    )
    value.add_argument("--api-url", required=True)
    value.add_argument("--owner", required=True)
    value.add_argument("--run-id", required=True)
    value.add_argument("--duration-seconds", required=True, type=int)
    value.add_argument("--interval-seconds", required=True, type=int)
    value.add_argument("--symbols", required=True, nargs="+")
    value.add_argument("--strategy-id", required=True)
    value.add_argument("--config-version", required=True)
    value.add_argument("--config", required=True, type=Path)
    value.add_argument("--data-version", required=True)
    value.add_argument("--code-version", required=True)
    value.add_argument("--output", required=True, type=Path)
    value.add_argument("--token-env", default="QUANTORA_API_TOKEN")
    value.add_argument("--acknowledge-paper-only", action="store_true")
    return value


def main() -> int:
    args = parser().parse_args()
    if not args.acknowledge_paper_only:
        raise SystemExit("refusing: pass --acknowledge-paper-only after owner approval")
    token = os.environ.get(args.token_env, "")
    if not token:
        raise SystemExit(f"refusing: {args.token_env} is not set")
    settings = RunnerSettings(
        owner=args.owner,
        run_id=args.run_id,
        duration_seconds=args.duration_seconds,
        interval_seconds=args.interval_seconds,
        symbols=tuple(args.symbols),
        strategy_id=args.strategy_id,
        config_version=args.config_version,
        config_path=args.config,
        data_version=args.data_version,
        code_version=args.code_version,
        evidence_path=args.output,
    )
    path = PaperSoakRunner(ControlPlaneClient(args.api_url, token), settings).run()
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
