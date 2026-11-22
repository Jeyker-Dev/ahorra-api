import argparse
import asyncio
from decimal import Decimal
from uuid import UUID

from app.clock import Clock
from app.config import Settings, get_settings
from app.rates.cli import noop_sources, open_rates_service, parse_decimal_arg, run_cli
from app.rates.domain import ExchangeRate


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List, confirm, or correct in-review rates.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List rates pending review.")

    confirm_parser = subparsers.add_parser("confirm", help="Confirm an in-review rate.")
    confirm_parser.add_argument("rate_id", help="UUID of the rate to confirm.")

    correct_parser = subparsers.add_parser(
        "correct",
        help="Correct an in-review rate with a manual value.",
    )
    correct_parser.add_argument("rate_id", help="UUID of the rate to correct.")
    correct_parser.add_argument("value", help="Corrected rate value.")
    return parser.parse_args(argv)


async def run_list(
    *,
    settings: Settings,
    clock: Clock | None = None,
) -> list[ExchangeRate]:
    """Return all exchange rates awaiting review."""
    async with open_rates_service(settings, sources=noop_sources(), clock=clock) as service:
        return await service.list_in_review()


async def run_confirm(
    rate_id: UUID,
    *,
    settings: Settings,
    clock: Clock | None = None,
) -> ExchangeRate:
    """Confirm an in-review exchange rate."""
    async with open_rates_service(settings, sources=noop_sources(), clock=clock) as service:
        return await service.confirm(rate_id)


async def run_correct(
    rate_id: UUID,
    value: Decimal,
    *,
    settings: Settings,
    clock: Clock | None = None,
) -> ExchangeRate:
    """Correct an in-review exchange rate with a manual value."""
    async with open_rates_service(settings, sources=noop_sources(), clock=clock) as service:
        return await service.correct(rate_id, value)


def _execute_command(args: argparse.Namespace, settings: Settings) -> int:
    if args.command == "list":
        rates = asyncio.run(run_list(settings=settings))
        if not rates:
            print("No rates pending review.")
            return 0
        for rate in rates:
            print(
                f"{rate.id} | {rate.kind.value} | {rate.value} | "
                f"{rate.value_date} | {rate.origin.value} | {rate.fetched_at.isoformat()}"
            )
        return 0

    rate_id = UUID(args.rate_id)
    if args.command == "confirm":
        rate = asyncio.run(run_confirm(rate_id, settings=settings))
        print(f"Confirmed {rate.id}: {rate.kind.value}={rate.value} ({rate.value_date})")
        return 0

    value = parse_decimal_arg(args.value)
    rate = asyncio.run(run_correct(rate_id, value, settings=settings))
    print(f"Corrected {rate.id}: {rate.kind.value}={rate.value} ({rate.value_date})")
    return 0


def main() -> int:
    args = _parse_args()
    return run_cli(lambda: _execute_command(args, get_settings()))


if __name__ == "__main__":
    raise SystemExit(main())
