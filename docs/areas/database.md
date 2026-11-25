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

### `products`

| Columna | Tipo | Reglas |
|---|---|---|
| `id` | `UUID` | Clave primaria, generada por el cliente (offline-first) |
| `name` | `VARCHAR(80)` | `CHECK (char_length(btrim(name)) BETWEEN 1 AND 80)`; índice único funcional `uq_products_lower_name` en `lower(name)` |
| `unit` | `VARCHAR(8)` | `CHECK (unit IN ('unit', 'kg', 'l'))` |
| `last_price` | `NUMERIC(18, 2)` | Opcional; `CHECK (last_price IS NULL OR last_price > 0)`; par junto a `last_price_currency` |
| `last_price_currency` | `VARCHAR(8)` | Opcional; `CHECK (last_price_currency IS NULL OR last_price_currency IN ('usd', 'ves'))`; par nulo/definido con `last_price` |
| `archived_at` | `TIMESTAMPTZ` | `NULL` mientras está activo; UTC cuando se archiva |
| `created_at` | `TIMESTAMPTZ` | UTC |
| `updated_at` | `TIMESTAMPTZ` | UTC |

### `product_prices`

| Columna | Tipo | Reglas |
|---|---|---|
| `id` | `UUID` | Clave primaria, generada por la aplicación |
| `product_id` | `UUID` | FK a `products.id`, `ON DELETE RESTRICT`; índice compuesto `(product_id, recorded_on)` |
| `price` | `NUMERIC(18, 2)` | `CHECK (price > 0)` |
| `currency` | `VARCHAR(8)` | `CHECK (currency IN ('usd', 'ves'))` |
| `recorded_on` | `DATE` | Fecha del registro en `America/Caracas` |
| `source` | `VARCHAR(16)` | `CHECK (source IN ('manual', 'purchase'))` |
| `list_item_id` | `UUID` | FK opcional a `shopping_list_items.id`, `ON DELETE RESTRICT`; obligatorio si `purchase`, nulo si `manual`; índice único parcial `uq_product_prices_list_item_id` donde `list_item_id IS NOT NULL` |
| `bcv_rate_value` | `NUMERIC(18, 8)` | `CHECK (bcv_rate_value > 0)` |
| `bcv_rate_date` | `DATE` | `value_date` de la tasa BCV vigente |
| `usdt_rate_value` | `NUMERIC(18, 8)` | `CHECK (usdt_rate_value > 0)` |
| `usdt_rate_date` | `DATE` | `value_date` de la tasa USDT vigente |
| `created_at` | `TIMESTAMPTZ` | UTC |
| `updated_at` | `TIMESTAMPTZ` | UTC; backfill desde `created_at`, índice `ix_product_prices_updated_at` |

### `shopping_lists`

| Columna | Tipo | Reglas |
|---|---|---|
| `id` | `UUID` | Clave primaria, generada por el cliente (offline-first) |
| `name` | `VARCHAR(80)` | `CHECK (char_length(btrim(name)) BETWEEN 1 AND 80)` |
| `status` | `VARCHAR(16)` | `CHECK (status IN ('open', 'closed'))` |
| `closed_at` | `TIMESTAMPTZ` | UTC; `NULL` si `open`, obligatorio si `closed` (CHECK compuesto con `expense_id`) |
| `expense_id` | `UUID` | FK opcional a `expenses.id`, `ON DELETE RESTRICT`; `NULL` si `open`, obligatorio si `closed` |
| `deleted_at` | `TIMESTAMPTZ` | `NULL` mientras está activa; UTC en borrado lógico |
| `created_at` | `TIMESTAMPTZ` | UTC |
| `updated_at` | `TIMESTAMPTZ` | UTC |

### `shopping_list_items`

| Columna | Tipo | Reglas |
|---|---|---|
| `id` | `UUID` | Clave primaria, generada por el cliente (offline-first) |
| `list_id` | `UUID` | FK a `shopping_lists.id`, `ON DELETE RESTRICT`; único junto a `product_id` (`uq_shopping_list_items_list_product`) |
| `product_id` | `UUID` | FK a `products.id`, `ON DELETE RESTRICT` |
| `quantity` | `NUMERIC(18, 3)` | `CHECK (quantity > 0)` |
| `status` | `VARCHAR(16)` | `CHECK (status IN ('pending', 'bought'))` |
| `bought_price` | `NUMERIC(18, 2)` | `NULL` si `pending`; `> 0` y obligatorio si `bought` |
| `bought_currency` | `VARCHAR(8)` | `NULL` si `pending`; `'usd'` o `'ves'` y obligatorio si `bought` |
| `bought_at` | `TIMESTAMPTZ` | UTC; `NULL` si `pending`, obligatorio si `bought` |
| `created_at` | `TIMESTAMPTZ` | UTC |
| `updated_at` | `TIMESTAMPTZ` | UTC |

### `goals`

| Columna | Tipo | Reglas |
|---|---|---|
| `id` | `UUID` | Clave primaria, generada por el cliente (offline-first) |
| `name` | `VARCHAR(50)` | `CHECK (char_length(btrim(name)) BETWEEN 1 AND 50)`; índice único parcial `uq_goals_lower_name_active` en `lower(name)` donde `deleted_at IS NULL` |
| `target_usd` | `NUMERIC(18, 2)` | `CHECK (target_usd > 0)` |
| `target_date` | `DATE` | Fecha objetivo opcional |
| `note` | `VARCHAR(500)` | Opcional; `CHECK (note IS NULL OR (char_length(btrim(note)) BETWEEN 1 AND 500))` |
| `archived_at` | `TIMESTAMPTZ` | `NULL` mientras está activa; UTC cuando se archiva |
| `deleted_at` | `TIMESTAMPTZ` | `NULL` mientras está activa; UTC en borrado lógico |
| `created_at` | `TIMESTAMPTZ` | UTC |
| `updated_at` | `TIMESTAMPTZ` | UTC |

### `goal_contributions`

| Columna | Tipo | Reglas |
|---|---|---|
| `id` | `UUID` | Clave primaria, generada por el cliente (offline-first) |
| `goal_id` | `UUID` | FK a `goals.id`, `ON DELETE RESTRICT`; índice compuesto `(goal_id, contributed_on)` |
| `kind` | `VARCHAR(16)` | `CHECK (kind IN ('deposit', 'withdrawal'))` |
| `amount` | `NUMERIC(18, 2)` | `CHECK (amount > 0)` |
| `currency` | `VARCHAR(16)` | `CHECK (currency IN ('usd', 'ves'))` |
| `contributed_on` | `DATE` | Fecha del aporte |
| `note` | `VARCHAR(500)` | Opcional; `CHECK (note IS NULL OR (char_length(btrim(note)) BETWEEN 1 AND 500))` |
| `bcv_rate_value` | `NUMERIC(18, 8)` | `CHECK (bcv_rate_value > 0)` |
| `bcv_rate_date` | `DATE` | `value_date` de la tasa BCV vigente en `contributed_on` |
| `usdt_rate_value` | `NUMERIC(18, 8)` | `CHECK (usdt_rate_value > 0)` |
| `usdt_rate_date` | `DATE` | `value_date` de la tasa USDT vigente en `contributed_on` |
| `deleted_at` | `TIMESTAMPTZ` | `NULL` mientras está activo; UTC en borrado lógico |
| `created_at` | `TIMESTAMPTZ` | UTC |
| `updated_at` | `TIMESTAMPTZ` | UTC |

### `sync_tombstones`

| Columna | Tipo | Reglas |
|---|---|---|
| `entity` | `VARCHAR(32)` | Parte de PK `(entity, id)`; `CHECK (entity IN ('income_rules', 'income_occurrences', 'expense_categories', 'expenses', 'products', 'product_prices', 'shopping_lists', 'shopping_list_items', 'goals', 'goal_contributions'))` |
| `id` | `UUID` | ID de la fila eliminada físicamente; parte de PK `(entity, id)` |
| `deleted_at` | `TIMESTAMPTZ` | UTC (`now()` del trigger); índice `ix_sync_tombstones_deleted_at` |

### Índices y triggers de sincronización

- Todas las tablas de dominio sincronizables (`income_rules`, `income_occurrences`, `expense_categories`, `expenses`, `products`, `product_prices`, `shopping_lists`, `shopping_list_items`, `goals`, `goal_contributions`) cuentan con un índice `ix_<table>_updated_at` sobre `updated_at`.
- La función `record_sync_tombstone()` y los triggers `AFTER DELETE ... FOR EACH ROW` (`tr_income_occurrences_sync_tombstone`, `tr_shopping_list_items_sync_tombstone` y `tr_product_prices_sync_tombstone`) registran o actualizan (`ON CONFLICT (entity, id) DO UPDATE`) los borrados físicos en `sync_tombstones` dentro de la misma transacción del `DELETE` (ADR-010).

## Reglas

- Una migración de Alembic por cambio de esquema, con un mensaje descriptivo (`alembic revision --autogenerate -m "create users"`), revisada a mano antes de aplicarla.
- La migración inicial crea `users` y `refresh_tokens`.
- Los tests de integración usan una base aparte (`app_test`) en el mismo servicio `db`, nunca la de desarrollo.
- La URL de conexión usa el driver async: `postgresql+asyncpg://...`.

## Riesgos conocidos

- `refresh_tokens` crece con cada rotación y no hay limpieza automática todavía (pendiente para más adelante, ver el roadmap).
- Cambiar `JWT_SECRET` invalida todas las sesiones de access token, pero no los refresh tokens.
