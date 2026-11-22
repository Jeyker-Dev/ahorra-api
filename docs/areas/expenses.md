# Gastos por categoría

## Conceptos

- **Categoría de gasto (`ExpenseCategory`)**: agrupa gastos con `id` (UUID generado por el cliente), `name` (1–50 caracteres sin espacios extremos ni en blanco, único sin distinguir mayúsculas/minúsculas), `archived_at` (`TIMESTAMPTZ` UTC cuando se archiva), `created_at` y `updated_at`. Las categorías nunca se borran físicamente (`ON DELETE RESTRICT` en `expenses`).
- **Gasto (`Expense`)**: registra un egreso puntual con `id` (UUID generado por el cliente), `category_id`, `amount` (`Decimal` > 0, cuantizado a 2 decimales con `ROUND_HALF_EVEN`), `currency` (`usd` | `ves`), `spent_on` (`DATE`), `note` opcional (sin espacios extremos, máximo 500 caracteres; vacía o solo espacios pasa a `None`), las dos tasas vigentes en `spent_on` (`bcv_rate_value`, `bcv_rate_date`, `usdt_rate_value`, `usdt_rate_date`), `deleted_at` (`TIMESTAMPTZ` UTC en borrado lógico), `created_at` y `updated_at`.

## Las tres lecturas de un monto

Cada gasto guarda en el momento de crearse (o al cambiar `spent_on`) tanto la tasa BCV (`RatesService.bcv_in_force`) como la tasa USDT (`RatesService.usdt_in_force`) vigentes para `spent_on`. Así todo monto conserva sus tres lecturas históricas (USD, bolívares a BCV y bolívares a USDT) tal como eran ese día, y el historial nunca se recalcula al llegar tasas nuevas.

Si falta cualquiera de las dos tasas para `spent_on`, la API responde `409` (`RateNotAvailableForDate`). La tasa faltante puede cargarse manualmente con `scripts/set_rate.py --kind bcv|usdt` y reintentar la operación.

## Borrado lógico y sincronización offline-first

- La app móvil genera los UUID localmente y envía categorías y gastos mediante `PUT` idempotentes.
- Los gastos no se eliminan físicamente: `DELETE /expenses/{expense_id}` marca `deleted_at` y `updated_at` para que los dispositivos puedan sincronizar eliminaciones sin conflictos.
- Un `PUT` posterior sobre un gasto ya eliminado responde `409` (`ExpenseDeleted`).
- Los listados (`GET /expenses`) y el resumen (`GET /expenses/summary`) excluyen siempre los gastos con `deleted_at IS NOT NULL`.
- Todas las respuestas incluyen `updated_at` y `deleted_at` (en gastos) junto con `category_name`.

## Reglas de negocio (`ExpenseService`)

### Categorías

1. **Crear o renombrar (`upsert_category`)**, idempotente por `id`:
   - Si el `id` es nuevo (incluyendo inserciones concurrentes por el mismo `id`), inserta la categoría.
   - Si ya existe y el nombre normalizado es idéntico, devuelve la categoría sin cambios.
   - Si el nombre cambió (incluyendo cambios solo de mayúsculas/minúsculas sobre el mismo `id`), actualiza `name` y `updated_at`. Permite renombrar categorías archivadas.
   - Si otro `id` ya usa ese nombre sin distinguir mayúsculas/minúsculas, lanza `CategoryNameTaken` (`409`).
2. **Archivar (`archive_category`)** y **desarchivar (`unarchive_category`)**:
   - Operaciones idempotentes que actualizan `archived_at` y `updated_at` solo cuando cambia el estado; si la categoría no existe, lanzan `CategoryNotFound` (`404`).

### Gastos

3. **Crear o actualizar (`upsert_expense`)**, idempotente por `id`:
   - Valida y normaliza `amount` (> 0, 2 decimales) y `note` (vacía/espacios → `None`, máximo 500 caracteres).
   - Si el gasto ya existe con `deleted_at` definido, lanza `ExpenseDeleted` (`409`).
   - Verifica que la categoría exista (`CategoryNotFound`, `404`).
   - Si el gasto existe y sus campos normalizados son idénticos, lo devuelve sin cambios (incluso si su categoría fue archivada después).
   - Crear un gasto nuevo o mover uno existente a una categoría archivada lanza `CategoryArchived` (`409`); editar otros campos dentro de la misma categoría archivada está permitido.
   - Al crear o cuando cambia `spent_on`, consulta ambas tasas vigentes (`bcv_in_force` y `usdt_in_force`); si solo cambian monto, moneda, categoría o nota, conserva las tasas almacenadas.
4. **Eliminar (`delete_expense`)**:
   - Marca `deleted_at` y `updated_at` de forma idempotente (`ExpenseNotFound`, `404` si no existe).

### Resumen

5. **Resumen por categoría (`summarize`)**:
   - Para el rango inclusivo `[from, to]` sobre `spent_on`, devuelve una fila por cada categoría con al menos un gasto no eliminado (`category_id`, `category_name`, `count`, `total_usd`).
   - `total_usd` suma cada gasto `usd` tal cual y cada gasto `ves` dividido entre su `bcv_rate_value` guardado, cuantizando a 2 decimales (`ROUND_HALF_EVEN`) solo al final por categoría.
   - Ordenado por `total_usd DESC` y luego por `category_name`.

## Endpoints HTTP (`Authorization: Bearer <access_token>`)

- `PUT /expenses/categories/{category_id}`: crea (`201`) o actualiza idempotentemente (`200`) una categoría.
- `GET /expenses/categories?include_archived=false`: lista categorías (`active` primero y luego `archived`, por `name` e `id`).
- `POST /expenses/categories/{category_id}/archive` y `/unarchive`: archiva o desarchiva una categoría (`200`).
- `PUT /expenses/{expense_id}`: crea (`201`) o actualiza idempotentemente (`200`) un gasto.
- `GET /expenses/{expense_id}`: devuelve un gasto no eliminado (`404` si no existe o fue eliminado).
- `DELETE /expenses/{expense_id}`: borrado lógico idempotente (`200` con el gasto incluyendo `deleted_at`).
- `GET /expenses?from=YYYY-MM-DD&to=YYYY-MM-DD&category_id=`: lista gastos no eliminados en el rango inclusivo de `spent_on`, ordenados por `spent_on DESC`, `created_at DESC`, `id ASC`. `from > to` devuelve `422`.
- `GET /expenses/summary?from=YYYY-MM-DD&to=YYYY-MM-DD`: resumen por categoría en USD (`422` si `from > to`).
- Los montos `Decimal` se serializan como strings. Errores mapeados en `main.py`: no encontrado → `404`, conflicto de nombre/archivo/eliminación/tasa → `409`, validación → `422`.
