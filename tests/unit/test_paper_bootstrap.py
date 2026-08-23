"""Phase 6 bootstrap never constructs a partially safe runtime."""

from datetime import timedelta

import pytest

from quantora_trade.application.paper_bootstrap import Phase6CompositionError, compose_phase6


def test_missing_dependencies_fail_closed_without_starting_any_work() -> None:
    with pytest.raises(Phase6CompositionError, match=r"accounting.*submissions"):
        compose_phase6(
            submissions=None,
            source=None,
            accounting=None,
            audit=None,
            alerts=None,
            database=None,
            authorization=None,
            configuration=None,
            entry_gate=None,
            clock=None,
            max_market_age=timedelta(seconds=30),
        )


def test_complete_dependencies_compose_bounded_runtime_without_starting_it() -> None:
    dependencies = {
        name: object()
        for name in (
            "submissions",
            "source",
            "accounting",
            "audit",
            "alerts",
            "database",
            "authorization",
            "configuration",
            "entry_gate",
            "clock",
        )
    }

    components = compose_phase6(
        **dependencies,  # type: ignore[arg-type]
        max_market_age=timedelta(seconds=30),
    )

    assert components.runtime is not None
    assert components.runner is not None
