# Tasas de cambio

Decisiones de base: FunWork ADR-001 (fuentes de la tasa BCV) y ADR-002 (fuente de la tasa USDT/Bs).

## Fuentes (`src/app/rates/sources.py`)

Todas usan `httpx.AsyncClient` inyectado con timeout de 10 s y rechazan valores `<= 0` con `QuoteUnavailable`. Cualquier fallo de red, HTTP, TLS o parseo se convierte en `QuoteUnavailable`.

- **BcvHtmlSource** (`GET https://www.bcv.org.ve/`): parsea `div#dolar strong` (separador de miles `.` y decimal `,`) y el atributo `content` de `span[property="dc:date"]`. Usa un `ssl.SSLContext` construido con `certifi.where()` más el certificado intermedio `Sectigo Public Server Authentication CA OV R36` empaquetado en `src/app/rates/certs/sectigo_ov_r36.pem` (cargado con `importlib.resources`). Nunca se desactiva la verificación TLS.
- **DolarApiSource** (`GET https://ve.dolarapi.com/v1/dolares/oficial`): parsea JSON directo a Pydantic (`model_validate_json`) para preservar `Decimal` sin pasar por `float`. Lee `promedio` y convierte `fechaActualizacion` a `America/Caracas` antes de tomar `.date()`.
- **BinanceP2PSource** (`POST https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search`): consulta anuncios de venta (`SELL`, `USDT`/`VES`, `merchant`, `transAmount: "50000"`, `rows: 10`), calcula la mediana (`statistics.median`) sobre `Decimal` y usa la fecha de hoy en `America/Caracas` tomada del `Clock` inyectado. Si `data` viene vacío o nulo, lanza `QuoteUnavailable`.

## Reglas de negocio (`RatesService`)

Todos los valores se cuantizan a 8 decimales (`ROUND_HALF_EVEN`) en el servicio antes de persistir.

### Consulta BCV (`fetch_bcv`)

1. Consulta `BcvHtmlSource` y `DolarApiSource` capturando `QuoteUnavailable` por fuente.
2. Si ambas responden y tienen la misma `value_date`, compara `abs(html - api) / html * 100` contra `BCV_TOLERANCE_PERCENT` (0.5 % por defecto):
   - Dentro de la tolerancia: guarda el valor del HTML con origen `bcv_html` y estado `confirmed`.
   - Fuera de la tolerancia: guarda el valor del HTML con origen `bcv_html` y estado `in_review` y registra un `warning`.
3. Si solo el HTML responde, o si las fechas (`value_date`) difieren, ignora `dolarapi`, guarda el valor del HTML como `confirmed` y registra un `warning`.
4. Si solo `dolarapi` responde: guarda su valor con origen `dolarapi` y estado `in_review`.
5. Si ambas fallan: no guarda nada, registra un `error` y lanza `RateUnavailable`.
6. Si ya existe una fila BCV para esa `value_date`:
   - Si está `confirmed` con origen `bcv_html` o `manual`, se mantiene sin cambios.
   - En otro caso, se reemplaza en el lugar (`repository.update`, conservando el `id`) solo si el resultado nuevo tiene mayor rango: `confirmed` > `in_review`, y a igual estado `manual` > `bcv_html` > `dolarapi`.

### Consulta USDT (`fetch_usdt`)

- Consulta `BinanceP2PSource` y guarda una nueva fila por ejecución con origen `binance_p2p` y estado `confirmed`. Si falla, registra un `error` y lanza `RateUnavailable`.

### Carga manual y revisión

- `set_manual(kind, value, value_date)`: exige `value > 0` (`InvalidRateValue`) y guarda origen `manual` y estado `confirmed` (reemplazando en el lugar la fila BCV existente para esa fecha, si hay una).
- `confirm(rate_id)`: solo válido si la tasa existe (`RateNotFound`) y está `in_review` (`RateNotInReview`); pasa su estado a `confirmed`.
- `correct(rate_id, value)`: exige `value > 0` (`InvalidRateValue`), tasa existente y estado `in_review`; actualiza `value`, cambia origen a `manual` y estado a `confirmed`.
- `bcv_in_force(on)`: devuelve la tasa BCV más reciente con `value_date <= on`, sin importar su `status` (`None` si no existe ninguna). Es el punto de consulta usado por otros dominios (como `income` al registrar un cobro).

## Endpoints HTTP (`Authorization: Bearer <access_token>`)

- `GET /rates/latest`: devuelve `{"bcv": RateOut | null, "usdt": RateOut | null}` sin importar su `status`. Para `bcv` devuelve la tasa vigente hoy: la fila más reciente con `value_date <= hoy` en `America/Caracas` (ordenada por `value_date DESC, fetched_at DESC`); si el BCV ya publicó la tasa del próximo día hábil con `value_date` futuro, se almacena en base de datos pero no se devuelve hasta que llegue su fecha. Para `usdt` devuelve la última fila registrada por `fetched_at`. `value` se serializa como string decimal (ej. `"917.77510000"`).
- `GET /rates?kind=bcv&from=YYYY-MM-DD&to=YYYY-MM-DD`: `kind` es obligatorio; `to` por defecto es hoy en `America/Caracas` y `from` es `to - 30` días. Filtra sobre `value_date` (inclusivo), ordena por `value_date` y luego `fetched_at`, y devuelve `422` si `from > to`.

## Planificación (`scheduler`)

Cron en `docker/scheduler/crontab` con `TZ=America/Caracas`:

- BCV: `0 9,13,17,21 * * *` (`09:00`, `13:00`, `17:00`, `21:00`).
- USDT: `0 * * * *` (cada hora al minuto `0`).

## Uso por CLI

```bash
uv run python scripts/fetch_rates.py --kind bcv
uv run python scripts/fetch_rates.py --kind usdt
uv run python scripts/set_rate.py --kind bcv --value 917.7751 --date 2026-11-20
uv run python scripts/review_rates.py list
uv run python scripts/review_rates.py confirm <rate-uuid>
uv run python scripts/review_rates.py correct <rate-uuid> 917.7751
```
