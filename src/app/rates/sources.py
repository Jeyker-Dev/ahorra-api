import ssl
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from importlib.resources import files
from statistics import median

import certifi
import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, ValidationError

from app.clock import Clock, caracas_date
from app.rates.domain import Quote
from app.rates.errors import QuoteUnavailable

REQUEST_TIMEOUT_SECONDS = 10.0
BCV_URL = "https://www.bcv.org.ve/"
DOLARAPI_URL = "https://ve.dolarapi.com/v1/dolares/oficial"
BINANCE_P2P_URL = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
BINANCE_REQUEST_BODY: dict[str, object] = {
    "asset": "USDT",
    "fiat": "VES",
    "tradeType": "SELL",
    "page": 1,
    "rows": 10,
    "publisherType": "merchant",
    "transAmount": "50000",
    "payTypes": [],
}

_BCV_CERT_PACKAGE = "app.rates"
_BCV_CERT_RESOURCE = "certs/sectigo_ov_r36.pem"


def load_bcv_intermediate_pem() -> str:
    """Read the bundled Sectigo OV R36 intermediate certificate PEM."""
    return files(_BCV_CERT_PACKAGE).joinpath(_BCV_CERT_RESOURCE).read_text(encoding="utf-8")


def build_bcv_ssl_context() -> ssl.SSLContext:
    """Build an SSLContext combining certifi CAs with the bundled Sectigo R36 intermediate."""
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(cadata=load_bcv_intermediate_pem())
    return context


def build_bcv_http_client() -> httpx.AsyncClient:
    """Create the AsyncClient configured for BCV's intermediate TLS chain."""
    return httpx.AsyncClient(verify=build_bcv_ssl_context(), timeout=REQUEST_TIMEOUT_SECONDS)


def build_default_http_client() -> httpx.AsyncClient:
    """Create a default AsyncClient with the standard rate-fetch timeout."""
    return httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)


def _require_positive_decimal(value: Decimal, source_name: str) -> Decimal:
    if not value.is_finite() or value <= Decimal("0"):
        raise QuoteUnavailable(f"{source_name} returned a non-positive quote value.")
    return value


def _parse_bcv_html(html: str) -> Quote:
    soup = BeautifulSoup(html, "html.parser")
    price_tag = soup.select_one("div#dolar strong")
    date_tag = soup.select_one('span[property="dc:date"]')
    if price_tag is None or date_tag is None:
        raise QuoteUnavailable("BCV HTML is missing expected rate or date element.")

    raw_price = price_tag.get_text().strip().replace(".", "").replace(",", ".")
    if not raw_price:
        raise QuoteUnavailable("BCV HTML returned an empty rate value.")
    value = _require_positive_decimal(Decimal(raw_price), "BCV HTML")

    raw_date = date_tag.get("content")
    if not isinstance(raw_date, str) or not raw_date.strip():
        raise QuoteUnavailable("BCV HTML is missing a valid dc:date content attribute.")
    value_date = datetime.fromisoformat(raw_date.strip()).date()
    return Quote(value=value, value_date=value_date)


class BcvHtmlSource:
    """QuoteSource scraping the official USD/Bs rate from bcv.org.ve."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def fetch(self) -> Quote:
        """Fetch and parse the official BCV USD quote."""
        try:
            response = await self._client.get(BCV_URL, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            return _parse_bcv_html(response.text)
        except (
            httpx.HTTPError,
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            InvalidOperation,
        ) as exc:
            raise QuoteUnavailable(f"Failed to fetch BCV HTML quote: {exc}") from exc


class _DolarApiPayload(BaseModel):
    promedio: Decimal
    fecha_actualizacion: datetime = Field(alias="fechaActualizacion")


class DolarApiSource:
    """QuoteSource reading the official BCV mirror from ve.dolarapi.com."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def fetch(self) -> Quote:
        """Fetch and parse the dolarapi official quote."""
        try:
            response = await self._client.get(DOLARAPI_URL, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = _DolarApiPayload.model_validate_json(response.content)
        except (httpx.HTTPError, ValidationError, ValueError, InvalidOperation) as exc:
            raise QuoteUnavailable(f"Failed to fetch dolarapi quote: {exc}") from exc

        value = _require_positive_decimal(payload.promedio, "dolarapi")
        updated_at = payload.fecha_actualizacion
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        return Quote(value=value, value_date=caracas_date(updated_at))


class _BinanceAdv(BaseModel):
    price: Decimal


class _BinanceItem(BaseModel):
    adv: _BinanceAdv


class _BinancePayload(BaseModel):
    data: list[_BinanceItem] | None = None


class BinanceP2PSource:
    """QuoteSource computing the median USDT/VES merchant sell price on Binance P2P."""

    def __init__(self, client: httpx.AsyncClient, clock: Clock) -> None:
        self._client = client
        self._clock = clock

    async def fetch(self) -> Quote:
        """Fetch Binance P2P ads and return the median price for today in Caracas."""
        try:
            response = await self._client.post(
                BINANCE_P2P_URL,
                json=BINANCE_REQUEST_BODY,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = _BinancePayload.model_validate_json(response.content)
        except (httpx.HTTPError, ValidationError, ValueError, InvalidOperation) as exc:
            raise QuoteUnavailable(f"Failed to fetch Binance P2P quote: {exc}") from exc

        if not payload.data:
            raise QuoteUnavailable("Binance P2P returned no merchant advertisements.")

        prices = [_require_positive_decimal(item.adv.price, "Binance P2P") for item in payload.data]
        median_price = median(prices)
        value_date: date = caracas_date(self._clock.now())
        return Quote(value=median_price, value_date=value_date)
