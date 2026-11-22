from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.rates.domain import Quote
from app.rates.errors import QuoteUnavailable
from app.rates.sources import (
    BINANCE_REQUEST_BODY,
    BcvHtmlSource,
    BinanceP2PSource,
    DolarApiSource,
)

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "rates"


class _FakeClock:
    def __init__(self, current: datetime) -> None:
        self._current = current

    def now(self) -> datetime:
        return self._current


def _mock_client(status_code: int, content: bytes | str) -> httpx.AsyncClient:
    def _handler(_request: httpx.Request) -> httpx.Response:
        if isinstance(content, bytes):
            return httpx.Response(status_code=status_code, content=content)
        return httpx.Response(status_code=status_code, text=content)

    return httpx.AsyncClient(transport=httpx.MockTransport(_handler))


async def test_bcv_html_source_parses_fixture() -> None:
    html = (FIXTURES_DIR / "bcv_valid.html").read_text(encoding="utf-8")
    async with _mock_client(200, html) as client:
        quote = await BcvHtmlSource(client).fetch()

    assert quote == Quote(value=Decimal("917.77510000"), value_date=date(2026, 11, 20))


async def test_bcv_html_source_strips_thousands_separator() -> None:
    html = (
        '<div id="dolar"><strong> 1.234,56780000 </strong></div>'
        '<span property="dc:date" content="2026-11-20T00:00:00-04:00"></span>'
    )
    async with _mock_client(200, html) as client:
        quote = await BcvHtmlSource(client).fetch()

    assert quote == Quote(value=Decimal("1234.56780000"), value_date=date(2026, 11, 20))


@pytest.mark.parametrize(
    "html",
    [
        "<html><body>no dolar div</body></html>",
        (
            '<div id="dolar"><strong>0,00</strong></div>'
            '<span property="dc:date" content="2026-11-20"></span>'
        ),
        (
            '<div id="dolar"><strong>not-a-number</strong></div>'
            '<span property="dc:date" content="2026-11-20"></span>'
        ),
        '<div id="dolar"><strong>917,77</strong></div><span property="dc:date"></span>',
        (
            '<div id="dolar"><strong>917,77</strong></div>'
            '<span property="dc:date" content="bad-date"></span>'
        ),
    ],
)
async def test_bcv_html_source_raises_quote_unavailable_on_malformed_html(html: str) -> None:
    async with _mock_client(200, html) as client:
        with pytest.raises(QuoteUnavailable):
            await BcvHtmlSource(client).fetch()


async def test_bcv_html_source_raises_quote_unavailable_on_http_error() -> None:
    async with _mock_client(503, "Service Unavailable") as client:
        with pytest.raises(QuoteUnavailable):
            await BcvHtmlSource(client).fetch()


async def test_bcv_html_source_raises_quote_unavailable_on_network_error() -> None:
    def _fail(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("TLS handshake failed")

    async with httpx.AsyncClient(transport=httpx.MockTransport(_fail)) as client:
        with pytest.raises(QuoteUnavailable):
            await BcvHtmlSource(client).fetch()


async def test_dolarapi_source_parses_fixture_in_caracas_date() -> None:
    payload = (FIXTURES_DIR / "dolarapi_valid.json").read_bytes()
    async with _mock_client(200, payload) as client:
        quote = await DolarApiSource(client).fetch()

    assert quote == Quote(value=Decimal("918.12"), value_date=date(2026, 11, 19))
    assert isinstance(quote.value, Decimal)


@pytest.mark.parametrize(
    "payload",
    [
        b'{"promedio": 0, "fechaActualizacion": "2026-11-20T12:00:00Z"}',
        b'{"promedio": -10.5, "fechaActualizacion": "2026-11-20T12:00:00Z"}',
        b'{"promedio": "bad", "fechaActualizacion": "2026-11-20T12:00:00Z"}',
        b'{"fechaActualizacion": "2026-11-20T12:00:00Z"}',
        b"not json",
    ],
)
async def test_dolarapi_source_raises_quote_unavailable_on_invalid_payload(payload: bytes) -> None:
    async with _mock_client(200, payload) as client:
        with pytest.raises(QuoteUnavailable):
            await DolarApiSource(client).fetch()


async def test_dolarapi_source_raises_quote_unavailable_on_http_error() -> None:
    async with _mock_client(502, b"Bad Gateway") as client:
        with pytest.raises(QuoteUnavailable):
            await DolarApiSource(client).fetch()


async def test_binance_source_computes_median_for_odd_ads_and_caracas_date() -> None:
    payload = (FIXTURES_DIR / "binance_odd.json").read_bytes()
    captured_body: list[bytes] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured_body.append(request.content)
        return httpx.Response(status_code=200, content=payload)

    clock = _FakeClock(datetime(2026, 11, 20, 2, 0, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
        quote = await BinanceP2PSource(client, clock).fetch()

    assert quote == Quote(value=Decimal("952.50"), value_date=date(2026, 11, 19))
    assert len(captured_body) == 1
    assert b'"asset":"USDT"' in captured_body[0]
    assert BINANCE_REQUEST_BODY["transAmount"] == "50000"


async def test_binance_source_computes_median_for_even_ads_as_decimal() -> None:
    payload = (FIXTURES_DIR / "binance_even.json").read_bytes()
    clock = _FakeClock(datetime(2026, 11, 20, 15, 0, tzinfo=UTC))
    async with _mock_client(200, payload) as client:
        quote = await BinanceP2PSource(client, clock).fetch()

    assert quote == Quote(value=Decimal("952.00"), value_date=date(2026, 11, 20))
    assert isinstance(quote.value, Decimal)


@pytest.mark.parametrize(
    "payload",
    [
        (FIXTURES_DIR / "binance_empty.json").read_bytes(),
        b'{"code": "000000", "data": null}',
        b'{"code": "000000", "data": [{"adv": {"price": "0"}}]}',
        b'{"code": "000000", "data": [{"adv": {"price": "bad"}}]}',
        b"invalid-json",
    ],
)
async def test_binance_source_raises_quote_unavailable_on_empty_or_bad_payload(
    payload: bytes,
) -> None:
    clock = _FakeClock(datetime(2026, 11, 20, 15, 0, tzinfo=UTC))
    async with _mock_client(200, payload) as client:
        with pytest.raises(QuoteUnavailable):
            await BinanceP2PSource(client, clock).fetch()


async def test_binance_source_raises_quote_unavailable_on_http_error() -> None:
    clock = _FakeClock(datetime(2026, 11, 20, 15, 0, tzinfo=UTC))
    async with _mock_client(429, b"Too Many Requests") as client:
        with pytest.raises(QuoteUnavailable):
            await BinanceP2PSource(client, clock).fetch()
