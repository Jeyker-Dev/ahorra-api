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
├── clock.py                 Protocolo Clock, SystemClock y caracas_date (America/Caracas)
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
├── expenses/
│   ├── domain.py            ExpenseCategory, Expense, puertos y tipos de resumen
│   ├── errors.py            Errores de negocio de categorías, gastos y tasa faltante
│   ├── models.py            ExpenseCategoryModel y ExpenseModel (SQLAlchemy)
│   ├── repository.py        Repositorios PostgreSQL (SqlAlchemyExpenseCategoryRepository, SqlAlchemyExpenseRepository)
│   ├── service.py           Casos de uso: categorías, upsert/borrado lógico de gastos y resumen
│   ├── schemas.py           Esquemas Pydantic HTTP de categorías, gastos y resumen
│   └── api.py               Router: /expenses/categories, /expenses/summary y /expenses
├── products/
│   ├── domain.py            Product, PriceEntry, puertos y DTOs de precio
│   ├── errors.py            Errores de negocio de catálogo, historial de precios y tasa faltante
│   ├── models.py            ProductModel y ProductPriceModel (SQLAlchemy)
│   ├── repository.py        Repositorios PostgreSQL (SqlAlchemyProductRepository, SqlAlchemyProductPriceRepository)
│   ├── service.py           Casos de uso: catálogo, archivo/desarchivo, historial y sync de precios de compra
│   ├── schemas.py           Esquemas Pydantic HTTP de productos e historial de precios
│   └── api.py               Router: /products
├── shopping/
│   ├── domain.py            ShoppingList, ShoppingListItem, totales, recomendaciones y puertos entre dominios
│   ├── errors.py            Errores de negocio de listas, ítems, compras, cierre y recomendaciones
│   ├── models.py            ShoppingListModel y ShoppingListItemModel (SQLAlchemy)
│   ├── repository.py        Repositorios PostgreSQL (SqlAlchemyShoppingListRepository, SqlAlchemyShoppingListItemRepository)
│   ├── service.py           Casos de uso: listas, ítems, compra/reverso, totales, cierre y recomendaciones
│   ├── schemas.py           Esquemas Pydantic HTTP de listas, ítems, totales y recomendaciones
│   └── api.py               Router: /shopping/lists y /shopping/recommendations
├── goals/
│   ├── domain.py            Goal, GoalContribution, GoalProgress y puertos
│   ├── errors.py            Errores de negocio de metas, aportes y tasa faltante
│   ├── models.py            GoalModel y GoalContributionModel (SQLAlchemy)
│   ├── repository.py        Repositorios PostgreSQL (SqlAlchemyGoalRepository, SqlAlchemyGoalContributionRepository)
│   ├── service.py           Casos de uso: metas, archivo/borrado lógico, aportes y progreso
│   ├── schemas.py           Esquemas Pydantic HTTP de metas, progreso y aportes
│   └── api.py               Router: /goals
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
5. Un dominio puede usar el `service.py` de otro (por ejemplo, `auth` usa `users`, `income`, `expenses`, `products` y `goals` consultan `RatesService` mediante protocolos de tasa, y `shopping` consume `ProductsService`, `ExpenseService` y `RatesService` mediante protocolos en `shopping/domain.py` y adaptadores finos en `shopping/api.py`), nunca sus modelos ni su repositorio.

## Convenciones críticas

- Los montos de dinero, cuando una app los agregue, se guardan como `NUMERIC` y se manejan con `Decimal`. Nunca `float`.
- Toda fecha y hora se guarda en UTC (`TIMESTAMP WITH TIME ZONE`) y se obtiene del `Clock` inyectado, nunca de `datetime.now()` directo en el dominio o los servicios.
- Los ids son UUID generados por la aplicación, salvo las entidades que la app móvil crea offline (`income_rules`, `expense_categories`, `expenses`, `products`, `shopping_lists`, `shopping_list_items`, `goals` y `goal_contributions`), cuyo UUID viene del cliente.
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
| PUT | `/expenses/categories/{category_id}` | Bearer | Crea (201) o renombra idempotentemente (200) una categoría de gasto |
| GET | `/expenses/categories` | Bearer | Lista categorías (`include_archived=false` por defecto; activas primero) |
| POST | `/expenses/categories/{category_id}/archive` | Bearer | Archiva una categoría de gasto de forma idempotente |
| POST | `/expenses/categories/{category_id}/unarchive` | Bearer | Desarchiva una categoría de gasto de forma idempotente |
| GET | `/expenses/summary` | Bearer | Resumen por categoría en USD para un rango inclusivo de `spent_on` |
| PUT | `/expenses/{expense_id}` | Bearer | Crea (201) o actualiza idempotentemente (200) un gasto guardando tasas BCV y USDT |
| GET | `/expenses/{expense_id}` | Bearer | Devuelve un gasto no eliminado por id (`404` si falta o fue eliminado) |
| DELETE | `/expenses/{expense_id}` | Bearer | Borrado lógico idempotente (`deleted_at`) de un gasto |
| GET | `/expenses` | Bearer | Lista gastos no eliminados filtrados por rango inclusivo de `spent_on` y `category_id` opcional |
| PUT | `/products/{product_id}` | Bearer | Crea (201) o actualiza idempotentemente (200) un producto del catálogo |
| GET | `/products` | Bearer | Lista productos (`include_archived=false` por defecto; activas primero si se incluyen archivados) |
| GET | `/products/{product_id}` | Bearer | Devuelve un producto por id |
| POST | `/products/{product_id}/archive` | Bearer | Archiva un producto de forma idempotente |
| POST | `/products/{product_id}/unarchive` | Bearer | Desarchiva un producto de forma idempotente |
| GET | `/products/{product_id}/prices` | Bearer | Historial de precios de un producto filtrado por rango inclusivo de `recorded_on` |
| PUT | `/shopping/lists/{list_id}` | Bearer | Crea (201) o renombra idempotentemente (200) una lista de compras abierta |
| GET | `/shopping/lists` | Bearer | Lista listas no eliminadas, con filtro opcional `status` |
| GET | `/shopping/lists/{list_id}` | Bearer | Devuelve una lista no eliminada con sus ítems y datos del producto |
| DELETE | `/shopping/lists/{list_id}` | Bearer | Borrado lógico idempotente (`deleted_at`) de una lista |
| GET | `/shopping/lists/{list_id}/totals` | Bearer | Totales estimados y comprados de una lista con las tasas vigentes hoy |
| POST | `/shopping/lists/{list_id}/close` | Bearer | Cierra la lista y registra idempotentemente el gasto en `expenses` |
| PUT | `/shopping/lists/{list_id}/items/{item_id}` | Bearer | Agrega (201) o actualiza (200) un ítem en una lista abierta |
| DELETE | `/shopping/lists/{list_id}/items/{item_id}` | Bearer | Elimina físicamente (`204`) un ítem `pending` de una lista abierta |
| POST | `/shopping/lists/{list_id}/items/{item_id}/buy` | Bearer | Marca o corrige un ítem como comprado y registra su precio `purchase` |
| POST | `/shopping/lists/{list_id}/items/{item_id}/unbuy` | Bearer | Revierte un ítem a `pending` y restaura `last_price` del producto |
| GET | `/shopping/recommendations` | Bearer | Productos recomendados según frecuencia de compra y totales estimados |
| PUT | `/goals/{goal_id}` | Bearer | Crea (201) o actualiza idempotentemente (200) una meta de ahorro con su progreso |
| GET | `/goals` | Bearer | Lista metas no eliminadas (`include_archived=false` por defecto) con progreso agregado en lote |
| GET | `/goals/{goal_id}` | Bearer | Devuelve una meta no eliminada con su progreso (`404` si falta o fue eliminada) |
| POST | `/goals/{goal_id}/archive` | Bearer | Archiva una meta de forma idempotente |
| POST | `/goals/{goal_id}/unarchive` | Bearer | Desarchiva una meta de forma idempotente |
| DELETE | `/goals/{goal_id}` | Bearer | Borrado lógico idempotente (`deleted_at`) de una meta |
| PUT | `/goals/{goal_id}/contributions/{contribution_id}` | Bearer | Crea (201), actualiza o mueve (200) un aporte con tasas BCV y USDT |
| GET | `/goals/{goal_id}/contributions` | Bearer | Lista aportes no eliminados de una meta por fecha descendente |
| DELETE | `/goals/{goal_id}/contributions/{contribution_id}` | Bearer | Borrado lógico idempotente (`deleted_at`) de un aporte |

El detalle de cada ruta y sus reglas está en [areas/auth.md](areas/auth.md), [areas/rates.md](areas/rates.md), [areas/income.md](areas/income.md), [areas/expenses.md](areas/expenses.md), [areas/products.md](areas/products.md), [areas/shopping.md](areas/shopping.md) y [areas/goals.md](areas/goals.md).

## Decisiones

Los ADR viven en el módulo de Decisiones de FunWork, proyecto Ahorra.

- ADR-001: fuentes de la tasa BCV.
- ADR-002: fuente de la tasa USDT/Bs.
- ADR-003: PostgreSQL con SQLAlchemy 2.0 async y Alembic.
- ADR-004: JWT de vida corta y refresh token rotativo con detección de reúso.
- ADR-005: carpetas por dominio y capas con puertos.
- ADR-006: plantilla que se copia por app en lugar de una API compartida.
