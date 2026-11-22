# Base de datos

Decisión de base: ADR-003.

## Tablas de dominio

### `users`

| Columna | Tipo | Reglas |
|---|---|---|
| `id` | `UUID` | Clave primaria, generada por la aplicación |
| `email` | `VARCHAR(320)` | Único, normalizado en minúsculas |
| `password_hash` | `TEXT` | Hash Argon2 de pwdlib |
| `is_active` | `BOOLEAN` | `true` por defecto |
| `created_at` | `TIMESTAMPTZ` | UTC |
| `updated_at` | `TIMESTAMPTZ` | UTC |

### `refresh_tokens`

| Columna | Tipo | Reglas |
|---|---|---|
| `id` | `UUID` | Clave primaria |
| `user_id` | `UUID` | FK a `users.id`, `ON DELETE CASCADE`, con índice |
| `family_id` | `UUID` | Una por login, con índice |
| `token_hash` | `CHAR(64)` | SHA-256 en hexadecimal del token, único |
| `expires_at` | `TIMESTAMPTZ` | UTC |
| `revoked_at` | `TIMESTAMPTZ` | `NULL` mientras está activo |
| `created_at` | `TIMESTAMPTZ` | UTC |

### `exchange_rates`

| Columna | Tipo | Reglas |
|---|---|---|
| `id` | `UUID` | Clave primaria, generada por la aplicación |
| `kind` | `VARCHAR(16)` | `CHECK (kind IN ('bcv', 'usdt'))` |
| `value` | `NUMERIC(18, 8)` | Bolívares por 1 USD/USDT |
| `value_date` | `DATE` | Índice único parcial en `(value_date)` donde `kind = 'bcv'` |
| `origin` | `VARCHAR(32)` | `CHECK (origin IN ('bcv_html', 'dolarapi', 'binance_p2p', 'manual'))` |
| `status` | `VARCHAR(16)` | `CHECK (status IN ('confirmed', 'in_review'))` |
| `fetched_at` | `TIMESTAMPTZ` | UTC; índice compuesto `(kind, fetched_at DESC)` |

### `income_rules`

| Columna | Tipo | Reglas |
|---|---|---|
| `id` | `UUID` | Clave primaria, generada por el cliente (offline-first) |
| `name` | `VARCHAR(100)` | `CHECK (char_length(btrim(name)) BETWEEN 1 AND 100)` |
| `amount` | `NUMERIC(18, 2)` | `CHECK (amount > 0)` |
| `currency` | `VARCHAR(8)` | `CHECK (currency IN ('usd', 'ves'))` |
| `days` | `SMALLINT[]` | `CHECK (cardinality(days) > 0)` |
| `start_date` | `DATE` | Inicio inclusivo de la regla |
| `end_date` | `DATE` | Opcional; `CHECK (end_date IS NULL OR end_date >= start_date)` |
| `ended_at` | `TIMESTAMPTZ` | `NULL` mientras está activa; UTC cuando se finaliza |
| `created_at` | `TIMESTAMPTZ` | UTC |
| `updated_at` | `TIMESTAMPTZ` | UTC |

### `income_occurrences`

| Columna | Tipo | Reglas |
|---|---|---|
| `id` | `UUID` | Clave primaria, generada por la aplicación |
| `rule_id` | `UUID` | FK a `income_rules.id`, `ON DELETE RESTRICT`; único junto a `expected_date` |
| `expected_date` | `DATE` | Con índice `ix_income_occurrences_expected_date` |
| `expected_amount` | `NUMERIC(18, 2)` | `CHECK (expected_amount > 0)` |
| `currency` | `VARCHAR(8)` | `CHECK (currency IN ('usd', 'ves'))` |
| `status` | `VARCHAR(16)` | `CHECK (status IN ('expected', 'received'))` |
| `received_date` | `DATE` | `NULL` si `expected`, obligatorio si `received` (CHECK compuesto) |
| `received_amount` | `NUMERIC(18, 2)` | `NULL` si `expected`; `> 0` y obligatorio si `received` |
| `rate_value` | `NUMERIC(18, 8)` | `NULL` si `expected`; `> 0` y obligatorio si `received` |
| `rate_date` | `DATE` | `value_date` de la tasa BCV; `NULL` si `expected`, obligatorio si `received` |
| `received_at` | `TIMESTAMPTZ` | UTC; `NULL` si `expected`, obligatorio si `received` |
| `updated_at` | `TIMESTAMPTZ` | UTC |

### `expense_categories`

| Columna | Tipo | Reglas |
|---|---|---|
| `id` | `UUID` | Clave primaria, generada por el cliente (offline-first) |
| `name` | `VARCHAR(50)` | `CHECK (char_length(btrim(name)) BETWEEN 1 AND 50)`; índice único funcional `uq_expense_categories_lower_name` en `lower(name)` |
| `archived_at` | `TIMESTAMPTZ` | `NULL` mientras está activa; UTC cuando se archiva |
| `created_at` | `TIMESTAMPTZ` | UTC |
| `updated_at` | `TIMESTAMPTZ` | UTC |

### `expenses`

| Columna | Tipo | Reglas |
|---|---|---|
| `id` | `UUID` | Clave primaria, generada por el cliente (offline-first) |
| `category_id` | `UUID` | FK a `expense_categories.id`, `ON DELETE RESTRICT`; índice compuesto `(category_id, spent_on)` |
| `amount` | `NUMERIC(18, 2)` | `CHECK (amount > 0)` |
| `currency` | `VARCHAR(8)` | `CHECK (currency IN ('usd', 'ves'))` |
| `spent_on` | `DATE` | Fecha del gasto; índice `ix_expenses_spent_on` |
| `note` | `VARCHAR(500)` | Opcional; `CHECK (note IS NULL OR (char_length(btrim(note)) BETWEEN 1 AND 500))` |
| `bcv_rate_value` | `NUMERIC(18, 8)` | `CHECK (bcv_rate_value > 0)` |
| `bcv_rate_date` | `DATE` | `value_date` de la tasa BCV vigente en `spent_on` |
| `usdt_rate_value` | `NUMERIC(18, 8)` | `CHECK (usdt_rate_value > 0)` |
| `usdt_rate_date` | `DATE` | `value_date` de la tasa USDT vigente en `spent_on` |
| `deleted_at` | `TIMESTAMPTZ` | `NULL` mientras está activo; UTC en borrado lógico |
| `created_at` | `TIMESTAMPTZ` | UTC |
| `updated_at` | `TIMESTAMPTZ` | UTC |

## Reglas

- Una migración de Alembic por cambio de esquema, con un mensaje descriptivo (`alembic revision --autogenerate -m "create users"`), revisada a mano antes de aplicarla.
- La migración inicial crea `users` y `refresh_tokens`.
- Los tests de integración usan una base aparte (`app_test`) en el mismo servicio `db`, nunca la de desarrollo.
- La URL de conexión usa el driver async: `postgresql+asyncpg://...`.

## Riesgos conocidos

- `refresh_tokens` crece con cada rotación y no hay limpieza automática todavía (pendiente para más adelante, ver el roadmap).
- Cambiar `JWT_SECRET` invalida todas las sesiones de access token, pero no los refresh tokens.
