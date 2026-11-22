# Arquitectura

## Stack

- Python 3.13, gestionado con uv (`uv python pin 3.13`).
- FastAPI y Uvicorn.
- PostgreSQL 17, con SQLAlchemy 2.0 async (driver asyncpg) y Alembic para las migraciones. Ver ADR-003.
- Autenticación: PyJWT (access token) y pwdlib con Argon2 (contraseñas). Ver ADR-004.
- Configuración: pydantic-settings, leída de variables de entorno.
- Calidad: ruff, mypy `--strict` y pytest (con pytest-asyncio y httpx para los tests de la API).
- HTTP y parsing: httpx, BeautifulSoup (`beautifulsoup4`) y `certifi` / `tzdata`.
- Docker: `Dockerfile` multi-stage con uv y `supercronic`, y `docker-compose.yml` con los servicios `api`, `scheduler` y `db`.

## Capas y responsabilidades

Código organizado por dominio y no por rol técnico. Ver ADR-005.

```
pyproject.toml
uv.lock
alembic.ini
migrations/                  Alembic: env.py async y versions/
src/app/
├── main.py                  create_app(): routers, manejadores de errores, ciclo de vida
├── config.py                Settings (pydantic-settings), cargadas una vez al arrancar
├── db.py                    Engine async, fábrica de sesiones y Base declarativa
├── errors.py                BusinessRuleError, la base de todos los errores de negocio
├── clock.py                 Protocolo Clock y SystemClock (la hora se inyecta)
├── users/
│   ├── domain.py            User (dataclass congelada) y el puerto UserRepository
│   ├── errors.py            EmailAlreadyRegistered, PasswordTooShort
│   ├── models.py            UserModel (SQLAlchemy)
│   ├── repository.py        SqlAlchemyUserRepository
│   ├── service.py           Casos de uso: crear usuario, obtener por id
│   ├── schemas.py           Esquemas Pydantic de entrada y salida HTTP
│   └── api.py               Router: GET /users/me
├── auth/
│   ├── domain.py            TokenPair, RefreshToken y el puerto RefreshTokenRepository
│   ├── errors.py            InvalidCredentials, InvalidRefreshToken, RefreshTokenReused
│   ├── passwords.py         PasswordHasher (pwdlib, Argon2)
│   ├── tokens.py            AccessTokenIssuer (PyJWT) y generación de refresh tokens
│   ├── models.py            RefreshTokenModel (SQLAlchemy)
│   ├── repository.py        SqlAlchemyRefreshTokenRepository
│   ├── service.py           Casos de uso: login, refresh, logout
│   ├── dependencies.py      get_current_user para proteger endpoints
│   ├── schemas.py           Esquemas Pydantic de entrada y salida HTTP
│   └── api.py               Router: /auth/token, /auth/refresh, /auth/logout
├── rates/
│   ├── certs/               Certificado intermedio Sectigo OV R36 para bcv.org.ve
│   ├── domain.py            ExchangeRate, Quote, enums, puertos y utilidades de zona horaria
│   ├── errors.py            RateUnavailable, QuoteUnavailable, RateNotFound, RateNotInReview, InvalidRateValue
│   ├── models.py            ExchangeRateModel (SQLAlchemy)
│   ├── repository.py        SqlAlchemyExchangeRateRepository
│   ├── sources.py           BcvHtmlSource, DolarApiSource y BinanceP2PSource (httpx)
│   ├── service.py           Casos de uso: consulta, validación cruzada, carga manual y revisión
│   ├── cli.py               Wiring y helpers compartidos de los scripts CLI
│   ├── schemas.py           Esquemas Pydantic HTTP (RateOut, LatestRatesOut, RateHistoryQuery)
│   └── api.py               Router: GET /rates/latest y GET /rates
├── income/
│   ├── domain.py            IncomeRule, IncomeOccurrence, puertos y reglas puras de fechas
│   ├── errors.py            Errores de negocio de reglas, ocurrencias y tasa faltante
│   ├── models.py            IncomeRuleModel e IncomeOccurrenceModel (SQLAlchemy)
│   ├── repository.py        Repositorios PostgreSQL (SqlAlchemyIncomeRuleRepository, SqlAlchemyIncomeOccurrenceRepository)
│   ├── service.py           Casos de uso: upsert, cierre, generación de horizonte, cobro y reverso
│   ├── schemas.py           Esquemas Pydantic HTTP de reglas y ocurrencias
│   └── api.py               Router: /income/rules y /income/occurrences
└── health/
    └── api.py               Router: GET /health
scripts/
├── create_user.py                 Alta de usuario por línea de comandos
├── fetch_rates.py                 Consulta y guarda tasas BCV o USDT (--kind bcv|usdt)
├── set_rate.py                    Carga manual de una tasa confirmada
├── review_rates.py                Lista, confirma o corrige tasas en revisión
└── generate_income_occurrences.py Extiende ocurrencias activas hasta el horizonte configurado
docker/
└── scheduler/
    └── crontab              Horarios de consulta en America/Caracas (supercronic)
tests/
├── unit/app/                Espeja src/app/; sin base de datos ni red
└── integration/             API completa contra PostgreSQL real
Dockerfile
docker-compose.yml
.env.example
```

Reglas entre capas:

1. `domain.py` es Python puro: no importa FastAPI, SQLAlchemy ni Pydantic.
2. `service.py` depende de los puertos de `domain.py` (protocolos), nunca de una implementación concreta. Los colaboradores se inyectan por `__init__`.
3. `repository.py` y `models.py` implementan los puertos con SQLAlchemy. Los modelos no salen de esa capa: el repositorio devuelve dataclasses de dominio.
4. El borde HTTP (`api.py` más los manejadores registrados en `main.py` junto con `schemas.py`) traduce Pydantic a dominio, llama al servicio y convierte los errores de negocio en respuestas HTTP.
5. Un dominio puede usar el `service.py` de otro (por ejemplo, `auth` usa `users` e `income` consulta `RatesService.bcv_in_force` a través del protocolo `BcvRateLookup` y `Depends(get_rates_service)`), nunca sus modelos ni su repositorio.

## Convenciones críticas

- Los montos de dinero, cuando una app los agregue, se guardan como `NUMERIC` y se manejan con `Decimal`. Nunca `float`.
- Toda fecha y hora se guarda en UTC (`TIMESTAMP WITH TIME ZONE`) y se obtiene del `Clock` inyectado, nunca de `datetime.now()` directo en el dominio o los servicios.
- Los ids son UUID generados por la aplicación, salvo las entidades que la app móvil crea offline (`income_rules` por ahora), cuyo UUID viene del cliente.
- Cada cambio de esquema es una migración de Alembic versionada; nunca `create_all()` fuera de los tests.
- Los errores de negocio heredan de `BusinessRuleError`, uno por regla. Solo el borde HTTP (`api.py` y los manejadores en `main.py`) los convierte en códigos de estado.
- Los secretos solo vienen del entorno. `.env.example` lista todas las variables con valores vacíos y `.env` nunca se versiona.
- Los textos que devuelve la API (`detail` de los errores) van en inglés.

## Mapa de rutas

| Método | Ruta | Autenticación | Qué hace |
|---|---|---|---|
| GET | `/health` | No | Confirma que la API y la base responden |
| POST | `/auth/token` | No | Login con email y contraseña; devuelve el par de tokens |
| POST | `/auth/refresh` | No (usa el refresh token) | Rota el refresh token y devuelve un par nuevo |
| POST | `/auth/logout` | No (usa el refresh token) | Revoca la sesión del refresh token |
| GET | `/users/me` | Bearer | Devuelve el usuario autenticado |
| GET | `/rates/latest` | Bearer | Devuelve la tasa BCV vigente hoy (`value_date <= hoy` en `America/Caracas`; las tasas con fecha futura se guardan pero no se devuelven hasta su fecha) y la última USDT |
| GET | `/rates` | Bearer | Historial de tasas filtrado por `kind` y rango de `value_date` |
| PUT | `/income/rules/{rule_id}` | Bearer | Crea (201) o actualiza idempotentemente (200) una regla de ingreso recurrente y genera sus ocurrencias |
| GET | `/income/rules` | Bearer | Lista todas las reglas de ingreso (activas primero, luego finalizadas, por `name`) |
| GET | `/income/rules/{rule_id}` | Bearer | Devuelve una regla de ingreso por id |
| DELETE | `/income/rules/{rule_id}` | Bearer | Finaliza una regla (`ended_at`) y elimina sus ocurrencias `expected` desde hoy |
| GET | `/income/occurrences` | Bearer | Lista ocurrencias filtradas por rango inclusivo de `expected_date` y `status` opcional |
| POST | `/income/occurrences/{occurrence_id}/receive` | Bearer | Marca o corrige una ocurrencia como recibida asociando la tasa BCV vigente |
| POST | `/income/occurrences/{occurrence_id}/unreceive` | Bearer | Revierte una ocurrencia recibida a `expected` |

El detalle de cada ruta y sus reglas está en [areas/auth.md](areas/auth.md), [areas/rates.md](areas/rates.md) y [areas/income.md](areas/income.md).

## Decisiones

Los ADR viven en el módulo de Decisiones de FunWork, proyecto Ahorra.

- ADR-001: fuentes de la tasa BCV.
- ADR-002: fuente de la tasa USDT/Bs.
- ADR-003: PostgreSQL con SQLAlchemy 2.0 async y Alembic.
- ADR-004: JWT de vida corta y refresh token rotativo con detección de reúso.
- ADR-005: carpetas por dominio y capas con puertos.
- ADR-006: plantilla que se copia por app en lugar de una API compartida.
