"""Read-only MT5 market-data smoke check.

Run this on the Windows host where MetaTrader 5 and the optional Python package
are installed. This script never submits or modifies an order.
"""

import argparse
import json
from datetime import UTC, datetime, timedelta

from quantora_trade.infrastructure.mt5 import MT5MarketDataAdapter, MetaTrader5Gateway
from quantora_trade.market_data import MarketDataService, MarketDataValidator
from quantora_trade.market_data.timeframes import Timeframe


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--timeframe", choices=[item.value for item in Timeframe], default="M15")
    parser.add_argument("--limit", type=int, default=100)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    gateway = MetaTrader5Gateway()
    gateway.initialize()
    try:
        adapter = MT5MarketDataAdapter(gateway)
        service = MarketDataService(adapter)
        timeframe = Timeframe(args.timeframe)
        as_of = datetime.now(UTC)
        snapshot = service.load_validated_snapshot(
            symbol=args.symbol,
            timeframe=timeframe.value,
            until=as_of,
            limit=args.limit,
            validator=MarketDataValidator(
                expected_interval=timeframe.duration,
                max_staleness=max(timeframe.duration * 2, timedelta(minutes=20)),
            ),
        )
        print(
            json.dumps(
                {
                    "symbol": snapshot.instrument.symbol,
                    "asset_class": snapshot.instrument.asset_class,
                    "timeframe": snapshot.timeframe,
                    "candles": len(snapshot.candles),
                    "latest_close_time": snapshot.candles[-1].close_time.isoformat(),
                    "quality_issues": [issue.code for issue in snapshot.quality.issues],
                    "usable": snapshot.quality.is_usable,
                },
                indent=2,
            )
        )
        return 0
    finally:
        gateway.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
