import argparse
import asyncio
from datetime import date
from decimal import Decimal

from app.clock import Clock, SystemClock, caracas_date
from app.config import Settings, get_settings
from app.rates.cli import noop_sources, open_rates_service, parse_decimal_arg, run_cli
from app.rates.domain import ExchangeRate, RateKind


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manually set a confirmed exchange rate.")
    parser.add_argument(
        "--kind",
        required=True,
        choices=[kind.value for kind in RateKind],
        help="Rate kind (bcv or usdt).",
    )
    parser.add_argument("--value", required=True, help="Rate value (Bs per 1 USD/USDT).")
    parser.add_argument(
        "--date",
        default=None,
        help="Value date in YYYY-MM-DD (defaults to today in America/Caracas).",
    )
    return parser.parse_args(argv)


async def run(
    kind: RateKind,
    value: Decimal,
    value_date: date | None,
    *,
    settings: Settings,
    clock: Clock | None = None,
) -> ExchangeRate:
    """Persist a manually supplied confirmed exchange rate."""
    resolved_clock = clock or SystemClock()
    resolved_date = value_date if value_date is not None else caracas_date(resolved_clock.now())
    async with open_rates_service(
        settings,
        sources=noop_sources(),
        clock=resolved_clock,
    ) as service:
        return await service.set_manual(kind=kind, value=value, value_date=resolved_date)


def main() -> int:
    args = _parse_args()

    def _execute() -> int:
        value = parse_decimal_arg(args.value)
        value_date = date.fromisoformat(args.date) if args.date is not None else None
        rate = asyncio.run(
            run(
                RateKind(args.kind),
                value,
                value_date,
                settings=get_settings(),
            )
        )
        print(
            f"Saved manual {rate.kind.value}: {rate.value} "
            f"(id={rate.id}, date={rate.value_date}, status={rate.status.value})"
        )
        return 0

    return run_cli(_execute)


if __name__ == "__main__":
    raise SystemExit(main())
