# Changelog

## 2026-11-25

- **Fase 6 — Sincronización incremental**: dominio `sync/` con endpoint `GET /sync/changes` en transacción `REPEATABLE READ` aislada de autenticación, ventana semiabierta `(since, until]` con `SYNC_SAFETY_LAG = 30 s`, validación estricta de cursor UTC (`InvalidSyncCursor` `422`), precedencia fila vs. tombstone, columna `updated_at` en `product_prices` (backfill desde `created_at`), índices `updated_at` en las 10 tablas sincronizables, tabla `sync_tombstones` y triggers `AFTER DELETE ... FOR EACH ROW` en PostgreSQL para `income_occurrences`, `shopping_list_items` y `product_prices` (ADR-010).

## 2026-11-23

- **Fase 5 — Metas de ahorro**: dominio `goals/`, tablas `goals` (con índice único parcial `uq_goals_lower_name_active` sobre metas no eliminadas) y `goal_contributions` (con tasas históricas BCV y USDT y borrado lógico), servicio con upsert idempotente de metas y aportes, movimientos entre metas, progreso calculado al leer (en lote con una sola consulta `GROUP BY`), equivalentes en bolívares con las tasas de hoy (`null` si falta alguna) y endpoints bajo `/goals`.

## 2026-11-22

- **Fase 4 — Productos y lista de compras**: `caracas_date` movida a `app.clock`; dominios `products/` y `shopping/`, tablas `products`, `product_prices`, `shopping_lists` y `shopping_list_items`; catálogo con `last_price`, archivo e historial de precios con tasas BCV y USDT; listas de compras con ítems, compra/corrección/reverso sincronizados por orden + idempotencia, totales estimados y comprados, cierre idempotente generando gasto en `expenses` mediante `uuid5`, recomendaciones por intervalo promedio de compra y endpoints bajo `/products` y `/shopping`.
- **Fase 3 — Gastos por categoría**: método `RatesService.usdt_in_force`, dominio `expenses/`, tablas `expense_categories` (con índice único en `lower(name)`) y `expenses` (con tasas históricas BCV y USDT y borrado lógico `deleted_at`), servicio con upsert idempotente por `id` generado por el cliente, archivo/desarchivo de categorías, borrado lógico, resumen mensual/por rango en USD con `Decimal` y endpoints bajo `/expenses`.
- **Fase 2 — Ingresos recurrentes**: dominio `income/`, tablas `income_rules` (con `days SMALLINT[]`, `ended_at` y `CHECK`s) e `income_occurrences` (con `CHECK` compuesto de cobro y unicidad `(rule_id, expected_date)`), servicio con upsert idempotente por `id` generado por el cliente, cierre con `ended_at`, extensión de horizonte configurable (`INCOME_HORIZON_MONTHS`), registro/corrección/reverso de cobros usando `RatesService.bcv_in_force`, endpoints `/income/rules` y `/income/occurrences`, script `scripts/generate_income_occurrences.py` y cron diario a las `00:30` en `America/Caracas`.

## 2026-11-20

- **Fase 1 — Tasas de cambio**: dominio `rates/`, tabla `exchange_rates` con índice único parcial para BCV, fuentes `BcvHtmlSource` (con certificado intermedio Sectigo OV R36 empaquetado), `DolarApiSource` y `BinanceP2PSource`, servicio con validación cruzada y tolerancia configurable (`BCV_TOLERANCE_PERCENT`), endpoints `GET /rates/latest` y `GET /rates`, scripts CLI (`fetch_rates.py`, `set_rate.py`, `review_rates.py`) y contenedor `scheduler` con `supercronic`.

## 2026-11-18

- API creada desde la plantilla `fastapi-starter-kit` (commit `f4f8304`): autenticación, usuarios, `/health`, Docker y calidad. El historial de la plantilla está en su propio repositorio.
- Documentación adaptada a Ahorra: visión, roadmap por módulos de la v1, sprint y backlog. Se quitó el prompt de construcción de la plantilla.
