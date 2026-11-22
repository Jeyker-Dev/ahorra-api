from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.rates.domain import (
    ExchangeRate,
    Quote,
    RateKind,
    RateOrigin,
    RateSources,
    RateStatus,
)
from app.rates.errors import (
    InvalidRateValue,
    QuoteUnavailable,
    RateNotFound,
    RateNotInReview,
    RateUnavailable,
)
from app.rates.service import RatesService


class _FakeClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current


class _FakeSource:
    def __init__(self, result: Quote | Exception) -> None:
        self._result = result

    async def fetch(self) -> Quote:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _InMemoryRateRepository:
    def __init__(self, initial: list[ExchangeRate] | None = None) -> None:
        self.items: dict[UUID, ExchangeRate] = {rate.id: rate for rate in initial or []}
        self.add_calls = 0
        self.update_calls = 0

    async def add(self, rate: ExchangeRate) -> ExchangeRate:
        self.add_calls += 1
        self.items[rate.id] = rate
        return rate

    async def get_by_id(self, rate_id: UUID) -> ExchangeRate | None:
        return self.items.get(rate_id)

    async def get_bcv_for_date(self, value_date: date) -> ExchangeRate | None:
        for rate in self.items.values():
            if rate.kind == RateKind.bcv and rate.value_date == value_date:
                return rate
        return None

    async def latest_bcv_in_force(self, today: date) -> ExchangeRate | None:
        matching = [
            r for r in self.items.values() if r.kind == RateKind.bcv and r.value_date <= today
        ]
        if not matching:
            return None
        return max(matching, key=lambda r: (r.value_date, r.fetched_at))

    async def latest_usdt_in_force(self, on: date) -> ExchangeRate | None:
        matching = [
            r for r in self.items.values() if r.kind == RateKind.usdt and r.value_date <= on
        ]
        if not matching:
            return None
        return max(matching, key=lambda r: (r.value_date, r.fetched_at))

    async def latest(self, kind: RateKind) -> ExchangeRate | None:
        matching = [r for r in self.items.values() if r.kind == kind]
        if not matching:
            return None
        return max(matching, key=lambda r: (r.fetched_at, r.value_date))

    async def history(
        self,
        kind: RateKind,
        from_date: date,
        to_date: date,
    ) -> list[ExchangeRate]:
        matching = [
            r
            for r in self.items.values()
            if r.kind == kind and from_date <= r.value_date <= to_date
        ]
        return sorted(matching, key=lambda r: (r.value_date, r.fetched_at))

    async def list_in_review(self) -> list[ExchangeRate]:
        matching = [r for r in self.items.values() if r.status == RateStatus.in_review]
        return sorted(matching, key=lambda r: (r.value_date, r.fetched_at), reverse=True)

    async def update(self, rate: ExchangeRate) -> ExchangeRate:
        self.update_calls += 1
        self.items[rate.id] = rate
        return rate


def _make_service(
    repo: _InMemoryRateRepository,
    *,
    bcv_html: Quote | Exception | None = None,
    dolarapi: Quote | Exception | None = None,
    binance: Quote | Exception | None = None,
    now: datetime | None = None,
) -> RatesService:
    sources = RateSources(
        bcv_html=_FakeSource(bcv_html or QuoteUnavailable("html unused")),
        dolarapi=_FakeSource(dolarapi or QuoteUnavailable("api unused")),
        binance=_FakeSource(binance or QuoteUnavailable("binance unused")),
    )
    clock = _FakeClock(now or datetime(2026, 11, 20, 14, 0, tzinfo=UTC))
    return RatesService(repository=repo, sources=sources, clock=clock, tolerance=Decimal("0.5"))


async def test_fetch_bcv_both_ok_within_tolerance_saves_html_confirmed() -> None:
    repo = _InMemoryRateRepository()
    day = date(2026, 11, 20)
    service = _make_service(
        repo,
        bcv_html=Quote(value=Decimal("917.775100004"), value_date=day),
        dolarapi=Quote(value=Decimal("919.00"), value_date=day),
    )

    saved = await service.fetch_bcv()

    assert saved.kind == RateKind.bcv
    assert saved.value == Decimal("917.77510000")
    assert saved.value_date == day
    assert saved.origin == RateOrigin.bcv_html
    assert saved.status == RateStatus.confirmed


async def test_fetch_bcv_both_ok_outside_tolerance_saves_html_in_review() -> None:
    repo = _InMemoryRateRepository()
    day = date(2026, 11, 20)
    service = _make_service(
        repo,
        bcv_html=Quote(value=Decimal("917.7751"), value_date=day),
        dolarapi=Quote(value=Decimal("930.00"), value_date=day),
    )

    saved = await service.fetch_bcv()

    assert saved.value == Decimal("917.77510000")
    assert saved.origin == RateOrigin.bcv_html
    assert saved.status == RateStatus.in_review


async def test_fetch_bcv_only_html_ok_saves_confirmed() -> None:
    repo = _InMemoryRateRepository()
    day = date(2026, 11, 20)
    service = _make_service(
        repo,
        bcv_html=Quote(value=Decimal("917.7751"), value_date=day),
        dolarapi=QuoteUnavailable("timeout"),
    )

    saved = await service.fetch_bcv()

    assert saved.origin == RateOrigin.bcv_html
    assert saved.status == RateStatus.confirmed


async def test_fetch_bcv_date_mismatch_ignores_dolarapi_and_confirms_html() -> None:
    repo = _InMemoryRateRepository()
    service = _make_service(
        repo,
        bcv_html=Quote(value=Decimal("920.00"), value_date=date(2026, 11, 21)),
        dolarapi=Quote(value=Decimal("900.00"), value_date=date(2026, 11, 20)),
    )

    saved = await service.fetch_bcv()

    assert saved.value == Decimal("920.00000000")
    assert saved.value_date == date(2026, 11, 21)
    assert saved.origin == RateOrigin.bcv_html
    assert saved.status == RateStatus.confirmed


async def test_fetch_bcv_only_dolarapi_ok_saves_in_review() -> None:
    repo = _InMemoryRateRepository()
    day = date(2026, 11, 20)
    service = _make_service(
        repo,
        bcv_html=QuoteUnavailable("TLS error"),
        dolarapi=Quote(value=Decimal("918.12"), value_date=day),
    )

    saved = await service.fetch_bcv()

    assert saved.value == Decimal("918.12000000")
    assert saved.origin == RateOrigin.dolarapi
    assert saved.status == RateStatus.in_review


async def test_fetch_bcv_both_fail_raises_rate_unavailable_without_saving() -> None:
    repo = _InMemoryRateRepository()
    service = _make_service(
        repo,
        bcv_html=QuoteUnavailable("down"),
        dolarapi=QuoteUnavailable("down"),
    )

    with pytest.raises(RateUnavailable):
        await service.fetch_bcv()

    assert repo.add_calls == 0
    assert repo.update_calls == 0


async def test_fetch_bcv_leaves_existing_confirmed_bcv_html_or_manual_untouched() -> None:
    day = date(2026, 11, 20)
    existing = ExchangeRate(
        id=uuid4(),
        kind=RateKind.bcv,
        value=Decimal("917.00000000"),
        value_date=day,
        origin=RateOrigin.bcv_html,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 20, 13, 0, tzinfo=UTC),
    )
    repo = _InMemoryRateRepository([existing])
    service = _make_service(
        repo,
        bcv_html=Quote(value=Decimal("999.00"), value_date=day),
        dolarapi=Quote(value=Decimal("999.00"), value_date=day),
        now=datetime(2026, 11, 20, 17, 0, tzinfo=UTC),
    )

    result = await service.fetch_bcv()

    assert result == existing
    assert repo.update_calls == 0
    assert repo.add_calls == 0


async def test_fetch_bcv_upgrades_in_review_dolarapi_to_in_review_bcv_html_in_place() -> None:
    day = date(2026, 11, 20)
    existing_id = uuid4()
    existing = ExchangeRate(
        id=existing_id,
        kind=RateKind.bcv,
        value=Decimal("918.00000000"),
        value_date=day,
        origin=RateOrigin.dolarapi,
        status=RateStatus.in_review,
        fetched_at=datetime(2026, 11, 20, 13, 0, tzinfo=UTC),
    )
    repo = _InMemoryRateRepository([existing])
    service = _make_service(
        repo,
        bcv_html=Quote(value=Decimal("950.00"), value_date=day),
        dolarapi=Quote(value=Decimal("918.00"), value_date=day),
        now=datetime(2026, 11, 20, 17, 0, tzinfo=UTC),
    )

    result = await service.fetch_bcv()

    assert result.id == existing_id
    assert result.value == Decimal("950.00000000")
    assert result.origin == RateOrigin.bcv_html
    assert result.status == RateStatus.in_review
    assert repo.update_calls == 1
    assert repo.add_calls == 0


async def test_fetch_bcv_upgrades_in_review_to_confirmed_in_place() -> None:
    day = date(2026, 11, 20)
    existing_id = uuid4()
    existing = ExchangeRate(
        id=existing_id,
        kind=RateKind.bcv,
        value=Decimal("918.00000000"),
        value_date=day,
        origin=RateOrigin.bcv_html,
        status=RateStatus.in_review,
        fetched_at=datetime(2026, 11, 20, 13, 0, tzinfo=UTC),
    )
    repo = _InMemoryRateRepository([existing])
    service = _make_service(
        repo,
        bcv_html=Quote(value=Decimal("918.50"), value_date=day),
        dolarapi=Quote(value=Decimal("918.50"), value_date=day),
        now=datetime(2026, 11, 20, 17, 0, tzinfo=UTC),
    )

    result = await service.fetch_bcv()

    assert result.id == existing_id
    assert result.status == RateStatus.confirmed
    assert result.value == Decimal("918.50000000")
    assert repo.update_calls == 1


async def test_fetch_bcv_does_not_downgrade_confirmed_manual_or_dolarapi_to_in_review() -> None:
    day = date(2026, 11, 20)
    existing = ExchangeRate(
        id=uuid4(),
        kind=RateKind.bcv,
        value=Decimal("917.50000000"),
        value_date=day,
        origin=RateOrigin.manual,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 20, 13, 0, tzinfo=UTC),
    )
    repo = _InMemoryRateRepository([existing])
    service = _make_service(
        repo,
        bcv_html=QuoteUnavailable("down"),
        dolarapi=Quote(value=Decimal("920.00"), value_date=day),
    )

    result = await service.fetch_bcv()

    assert result == existing
    assert repo.update_calls == 0


async def test_fetch_usdt_saves_binance_p2p_confirmed_with_quantization() -> None:
    repo = _InMemoryRateRepository()
    day = date(2026, 11, 20)
    service = _make_service(
        repo,
        binance=Quote(value=Decimal("952.123456785"), value_date=day),
    )

    saved = await service.fetch_usdt()

    assert saved.kind == RateKind.usdt
    assert saved.value == Decimal("952.12345678")
    assert saved.origin == RateOrigin.binance_p2p
    assert saved.status == RateStatus.confirmed


async def test_fetch_usdt_raises_rate_unavailable_on_failure() -> None:
    repo = _InMemoryRateRepository()
    service = _make_service(repo, binance=QuoteUnavailable("empty"))

    with pytest.raises(RateUnavailable):
        await service.fetch_usdt()


async def test_set_manual_replaces_existing_bcv_for_date_in_place() -> None:
    day = date(2026, 11, 20)
    existing_id = uuid4()
    existing = ExchangeRate(
        id=existing_id,
        kind=RateKind.bcv,
        value=Decimal("917.00000000"),
        value_date=day,
        origin=RateOrigin.bcv_html,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 20, 13, 0, tzinfo=UTC),
    )
    repo = _InMemoryRateRepository([existing])
    service = _make_service(repo, now=datetime(2026, 11, 20, 18, 0, tzinfo=UTC))

    saved = await service.set_manual(RateKind.bcv, Decimal("919.25"), day)

    assert saved.id == existing_id
    assert saved.value == Decimal("919.25000000")
    assert saved.origin == RateOrigin.manual
    assert saved.status == RateStatus.confirmed
    assert repo.update_calls == 1
    assert repo.add_calls == 0


async def test_set_manual_adds_new_row_for_usdt() -> None:
    repo = _InMemoryRateRepository()
    service = _make_service(repo)

    saved = await service.set_manual(RateKind.usdt, Decimal("955.5"), date(2026, 11, 20))

    assert saved.kind == RateKind.usdt
    assert saved.origin == RateOrigin.manual
    assert saved.status == RateStatus.confirmed
    assert repo.add_calls == 1


@pytest.mark.parametrize("bad_value", [Decimal("0"), Decimal("-1.5")])
async def test_set_manual_rejects_non_positive_value(bad_value: Decimal) -> None:
    repo = _InMemoryRateRepository()
    service = _make_service(repo)

    with pytest.raises(InvalidRateValue):
        await service.set_manual(RateKind.bcv, bad_value, date(2026, 11, 20))


async def test_confirm_transitions_in_review_to_confirmed() -> None:
    rate = ExchangeRate(
        id=uuid4(),
        kind=RateKind.bcv,
        value=Decimal("918.00000000"),
        value_date=date(2026, 11, 20),
        origin=RateOrigin.dolarapi,
        status=RateStatus.in_review,
        fetched_at=datetime(2026, 11, 20, 13, 0, tzinfo=UTC),
    )
    repo = _InMemoryRateRepository([rate])
    service = _make_service(repo)

    confirmed = await service.confirm(rate.id)

    assert confirmed.status == RateStatus.confirmed
    assert confirmed.origin == RateOrigin.dolarapi
    assert confirmed.value == rate.value


async def test_confirm_raises_not_found_or_not_in_review() -> None:
    confirmed_rate = ExchangeRate(
        id=uuid4(),
        kind=RateKind.bcv,
        value=Decimal("918.00000000"),
        value_date=date(2026, 11, 20),
        origin=RateOrigin.bcv_html,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 20, 13, 0, tzinfo=UTC),
    )
    repo = _InMemoryRateRepository([confirmed_rate])
    service = _make_service(repo)

    with pytest.raises(RateNotFound):
        await service.confirm(uuid4())

    with pytest.raises(RateNotInReview):
        await service.confirm(confirmed_rate.id)


async def test_correct_updates_value_sets_manual_and_confirmed() -> None:
    rate = ExchangeRate(
        id=uuid4(),
        kind=RateKind.bcv,
        value=Decimal("918.00000000"),
        value_date=date(2026, 11, 20),
        origin=RateOrigin.dolarapi,
        status=RateStatus.in_review,
        fetched_at=datetime(2026, 11, 20, 13, 0, tzinfo=UTC),
    )
    repo = _InMemoryRateRepository([rate])
    service = _make_service(repo)

    corrected = await service.correct(rate.id, Decimal("917.5"))

    assert corrected.id == rate.id
    assert corrected.value == Decimal("917.50000000")
    assert corrected.origin == RateOrigin.manual
    assert corrected.status == RateStatus.confirmed


async def test_correct_validates_positive_value_not_found_and_not_in_review() -> None:
    confirmed_rate = ExchangeRate(
        id=uuid4(),
        kind=RateKind.bcv,
        value=Decimal("918.00000000"),
        value_date=date(2026, 11, 20),
        origin=RateOrigin.bcv_html,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 20, 13, 0, tzinfo=UTC),
    )
    repo = _InMemoryRateRepository([confirmed_rate])
    service = _make_service(repo)

    with pytest.raises(InvalidRateValue):
        await service.correct(confirmed_rate.id, Decimal("0"))

    with pytest.raises(RateNotFound):
        await service.correct(uuid4(), Decimal("917.0"))

    with pytest.raises(RateNotInReview):
        await service.correct(confirmed_rate.id, Decimal("917.0"))


async def test_get_latest_returns_bcv_in_force_today_ignoring_future_date() -> None:
    in_force = ExchangeRate(
        id=uuid4(),
        kind=RateKind.bcv,
        value=Decimal("917.77510000"),
        value_date=date(2026, 11, 20),
        origin=RateOrigin.bcv_html,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 20, 13, 0, tzinfo=UTC),
    )
    older = ExchangeRate(
        id=uuid4(),
        kind=RateKind.bcv,
        value=Decimal("915.00000000"),
        value_date=date(2026, 11, 19),
        origin=RateOrigin.bcv_html,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 19, 13, 0, tzinfo=UTC),
    )
    future = ExchangeRate(
        id=uuid4(),
        kind=RateKind.bcv,
        value=Decimal("925.00000000"),
        value_date=date(2026, 11, 24),
        origin=RateOrigin.bcv_html,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 20, 21, 0, tzinfo=UTC),
    )
    caracas_tomorrow = ExchangeRate(
        id=uuid4(),
        kind=RateKind.bcv,
        value=Decimal("920.00000000"),
        value_date=date(2026, 11, 21),
        origin=RateOrigin.bcv_html,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 21, 1, 0, tzinfo=UTC),
    )
    usdt_old = ExchangeRate(
        id=uuid4(),
        kind=RateKind.usdt,
        value=Decimal("950.00000000"),
        value_date=date(2026, 11, 20),
        origin=RateOrigin.binance_p2p,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 20, 14, 0, tzinfo=UTC),
    )
    usdt_latest = ExchangeRate(
        id=uuid4(),
        kind=RateKind.usdt,
        value=Decimal("955.00000000"),
        value_date=date(2026, 11, 20),
        origin=RateOrigin.binance_p2p,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 21, 1, 30, tzinfo=UTC),
    )
    repo = _InMemoryRateRepository(
        [older, in_force, future, caracas_tomorrow, usdt_old, usdt_latest]
    )
    # 2026-11-21 02:00 UTC is 2026-11-20 22:00 in America/Caracas.
    service = _make_service(repo, now=datetime(2026, 11, 21, 2, 0, tzinfo=UTC))

    bcv, usdt = await service.get_latest()

    assert bcv == in_force
    assert usdt == usdt_latest


async def test_get_latest_returns_none_for_bcv_when_only_future_rate_exists() -> None:
    future = ExchangeRate(
        id=uuid4(),
        kind=RateKind.bcv,
        value=Decimal("925.00000000"),
        value_date=date(2026, 11, 24),
        origin=RateOrigin.bcv_html,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 20, 21, 0, tzinfo=UTC),
    )
    usdt_latest = ExchangeRate(
        id=uuid4(),
        kind=RateKind.usdt,
        value=Decimal("955.00000000"),
        value_date=date(2026, 11, 20),
        origin=RateOrigin.binance_p2p,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 20, 20, 0, tzinfo=UTC),
    )
    repo = _InMemoryRateRepository([future, usdt_latest])
    service = _make_service(repo, now=datetime(2026, 11, 20, 21, 30, tzinfo=UTC))

    bcv, usdt = await service.get_latest()

    assert bcv is None
    assert usdt == usdt_latest


async def test_get_latest_prefers_higher_value_date_for_bcv_over_fetched_at() -> None:
    yesterday_fetched_later = ExchangeRate(
        id=uuid4(),
        kind=RateKind.bcv,
        value=Decimal("915.00000000"),
        value_date=date(2026, 11, 19),
        origin=RateOrigin.manual,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 20, 15, 0, tzinfo=UTC),
    )
    today_fetched_earlier = ExchangeRate(
        id=uuid4(),
        kind=RateKind.bcv,
        value=Decimal("917.77510000"),
        value_date=date(2026, 11, 20),
        origin=RateOrigin.bcv_html,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 20, 12, 0, tzinfo=UTC),
    )
    repo = _InMemoryRateRepository([yesterday_fetched_later, today_fetched_earlier])
    service = _make_service(repo, now=datetime(2026, 11, 20, 16, 0, tzinfo=UTC))

    bcv, _ = await service.get_latest()

    assert bcv == today_fetched_earlier


async def test_bcv_in_force_returns_most_recent_on_or_before_date_ignoring_status() -> None:
    older = ExchangeRate(
        id=uuid4(),
        kind=RateKind.bcv,
        value=Decimal("910.00000000"),
        value_date=date(2026, 11, 18),
        origin=RateOrigin.bcv_html,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 18, 13, 0, tzinfo=UTC),
    )
    target_in_review = ExchangeRate(
        id=uuid4(),
        kind=RateKind.bcv,
        value=Decimal("917.50000000"),
        value_date=date(2026, 11, 19),
        origin=RateOrigin.dolarapi,
        status=RateStatus.in_review,
        fetched_at=datetime(2026, 11, 19, 13, 0, tzinfo=UTC),
    )
    future = ExchangeRate(
        id=uuid4(),
        kind=RateKind.bcv,
        value=Decimal("925.00000000"),
        value_date=date(2026, 11, 22),
        origin=RateOrigin.bcv_html,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 21, 13, 0, tzinfo=UTC),
    )
    usdt_same_day = ExchangeRate(
        id=uuid4(),
        kind=RateKind.usdt,
        value=Decimal("960.00000000"),
        value_date=date(2026, 11, 20),
        origin=RateOrigin.binance_p2p,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 20, 14, 0, tzinfo=UTC),
    )
    repo = _InMemoryRateRepository([older, target_in_review, future, usdt_same_day])
    service = _make_service(repo)

    assert await service.bcv_in_force(date(2026, 11, 20)) == target_in_review
    assert await service.bcv_in_force(date(2026, 11, 19)) == target_in_review
    assert await service.bcv_in_force(date(2026, 11, 17)) is None


async def test_usdt_in_force_returns_most_recent_on_or_before_date_ignoring_status() -> None:
    older = ExchangeRate(
        id=uuid4(),
        kind=RateKind.usdt,
        value=Decimal("940.00000000"),
        value_date=date(2026, 11, 18),
        origin=RateOrigin.binance_p2p,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 18, 13, 0, tzinfo=UTC),
    )
    earlier_same_day = ExchangeRate(
        id=uuid4(),
        kind=RateKind.usdt,
        value=Decimal("950.00000000"),
        value_date=date(2026, 11, 19),
        origin=RateOrigin.binance_p2p,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 19, 10, 0, tzinfo=UTC),
    )
    later_same_day_in_review = ExchangeRate(
        id=uuid4(),
        kind=RateKind.usdt,
        value=Decimal("955.00000000"),
        value_date=date(2026, 11, 19),
        origin=RateOrigin.manual,
        status=RateStatus.in_review,
        fetched_at=datetime(2026, 11, 19, 15, 0, tzinfo=UTC),
    )
    future = ExchangeRate(
        id=uuid4(),
        kind=RateKind.usdt,
        value=Decimal("970.00000000"),
        value_date=date(2026, 11, 22),
        origin=RateOrigin.binance_p2p,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 22, 12, 0, tzinfo=UTC),
    )
    bcv_same_day = ExchangeRate(
        id=uuid4(),
        kind=RateKind.bcv,
        value=Decimal("917.00000000"),
        value_date=date(2026, 11, 20),
        origin=RateOrigin.bcv_html,
        status=RateStatus.confirmed,
        fetched_at=datetime(2026, 11, 20, 14, 0, tzinfo=UTC),
    )
    repo = _InMemoryRateRepository(
        [older, earlier_same_day, later_same_day_in_review, future, bcv_same_day]
    )
    service = _make_service(repo)

    assert await service.usdt_in_force(date(2026, 11, 20)) == later_same_day_in_review
    assert await service.usdt_in_force(date(2026, 11, 19)) == later_same_day_in_review
    assert await service.usdt_in_force(date(2026, 11, 17)) is None
