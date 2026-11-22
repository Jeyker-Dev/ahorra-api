import argparse
import asyncio
import logging

from app.clock import Clock
from app.config import Settings, get_settings
from app.rates.cli import open_rates_service, run_cli
from app.rates.domain import ExchangeRate, RateKind, RateSources


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch and store exchange rates.")
    parser.add_argument(
        "--kind",
        required=True,
        choices=[kind.value for kind in RateKind],
        help="Rate kind to fetch (bcv or usdt).",
    )
    return parser.parse_args(argv)


async def run(
    kind: RateKind,
    *,
    settings: Settings,
    sources: RateSources | None = None,
    clock: Clock | None = None,
) -> ExchangeRate:
    """Fetch and persist the requested exchange rate kind."""
    async with open_rates_service(settings, sources=sources, clock=clock) as service:
        if kind == RateKind.bcv:
            return await service.fetch_bcv()
        return await service.fetch_usdt()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _parse_args()

    def _execute() -> int:
        rate = asyncio.run(run(RateKind(args.kind), settings=get_settings()))
        print(
            f"Fetched {rate.kind.value}: {rate.value} "
            f"(date={rate.value_date}, origin={rate.origin.value}, status={rate.status.value})"
        )
        return 0

    return run_cli(_execute)


if __name__ == "__main__":
    raise SystemExit(main())
