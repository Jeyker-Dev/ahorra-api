# Sincronización incremental (`sync`)

## Objetivo

La app móvil es *offline-first* y lee únicamente desde su base local en Drift. El endpoint `GET /sync/changes` entrega en una sola llamada todas las filas modificadas desde el último pull —incluyendo registros con borrado lógico (`deleted_at`), archivados (`archived_at`) o finalizados (`ended_at`)— junto con los *tombstones* de los borrados físicos, para que el cliente pueda espejar el servidor en Drift sin campos calculados.

Entidades incluidas: `income_rules`, `income_occurrences`, `expense_categories`, `expenses`, `products`, `product_prices`, `shopping_lists`, `shopping_list_items`, `goals` y `goal_contributions`. Quedan fuera `users`, `refresh_tokens` y `exchange_rates` (las tasas se consultan en `/rates`).

## Ventana con margen de seguridad (por qué no `max(updated_at)`)

Al iniciar cada request, el servidor fija `until = clock.now() - SYNC_SAFETY_LAG` (`SYNC_SAFETY_LAG = 30 s`), devuelve los registros con `since < updated_at <= until` (o `updated_at <= until` en el primer sync sin `since`) y retorna `until` como `next_cursor`:

- **Contigüidad sin huecos ni empates**: las ventanas son semiabiertas `(since, until]`. Si múltiples filas comparten el mismo `updated_at`, todas caen del mismo lado del límite; ninguna se omite ni se duplica. Usar `max(updated_at)` de las filas devueltas dejaría fuera filas insertadas concurrentemente con el mismo timestamp o impediría avanzar si hubiera filas posteriores omitidas.
- **Margen contra commits tardíos**: procesos como el job diario de ocurrencias de ingresos toman `now` del reloj antes de completar todos sus commits. Restar `30 s` evita que el cursor avance por delante de transacciones que ya fijaron su `updated_at` pero aún no son visibles para otros lectores.

## Tombstones por triggers (ADR-010)

Tres rutas siguen aplicando borrado físico porque otras reglas de negocio dependen de que la fila desaparezca de la tabla (unicidad y regeneración de horizonte):

1. `income_occurrences`: `delete_future_expected` al editar o finalizar una regla de ingresos.
2. `shopping_list_items`: `delete_by_id` al eliminar un ítem `pending` de una lista abierta.
3. `product_prices`: `delete_by_list_item_id` al revertir una compra (`unbuy`) y recalcular `last_price`.

En lugar de escribir tombstones desde la capa de servicio (lo que rompería el patrón de commit por repositorio o exigiría una unidad de trabajo compartida), PostgreSQL ejecuta la función `record_sync_tombstone()` mediante un trigger `AFTER DELETE ... FOR EACH ROW` en esas tres tablas. El trigger inserta `(TG_TABLE_NAME, OLD.id, now())` en `sync_tombstones` con `ON CONFLICT (entity, id) DO UPDATE SET deleted_at = EXCLUDED.deleted_at`. Así el tombstone forma parte de la misma transacción del `DELETE` y desaparece automáticamente si la transacción hace rollback.

### Precedencia fila vs. tombstone

Si una entidad con UUID generado por el cliente (`shopping_list_items`) es borrada físicamente (`deleted_at = t1`) y luego recreada con el mismo `id` (`updated_at = t2 > t1`) dentro de la misma ventana, `SyncService` resuelve el conflicto antes de responder: devuelve únicamente el tombstone si `deleted_at > updated_at`, y únicamente la fila en caso contrario (`updated_at >= deleted_at`).

## Consistencia y validación

- **Transacción `REPEATABLE READ`**: `GET /sync/changes` abre una sesión dedicada desde `session_factory` con `isolation_level="REPEATABLE READ"`, independiente de la sesión usada por `get_current_user` (que ya inició una transacción `READ COMMITTED` al leer `users`), de modo que las 11 consultas reflejen el mismo snapshot puntual.
- **Cursor estricto**: `since` debe venir en ISO-8601 con offset UTC explícito (`Z` o `+00:00`) y cumplir `since <= until`. Cualquier cursor malformado, *naive*, en otro huso horario o posterior a `until` lanza `InvalidSyncCursor` (`422` con `{"detail": "..."}`).
- **Solo columnas almacenadas**: se devuelven las columnas reales de cada tabla (orden `(updated_at, id)`), sin campos calculados ni *joins* (`rule_name`, `category_name`, `product_name`, `progress`, totales ni equivalentes en Bs). Cada una de las 11 claves del payload está siempre presente (arreglo vacío si no hubo cambios).

## Endpoint HTTP (`Authorization: Bearer <access_token>`)

- `GET /sync/changes?since=<ISO-8601 UTC>`: devuelve `{next_cursor, income_rules, income_occurrences, expense_categories, expenses, products, product_prices, shopping_lists, shopping_list_items, goals, goal_contributions, tombstones}`.

## Límites conocidos

- **Sin paginación**: en esta fase la respuesta entrega todos los cambios de la ventana sin límite ni paginación (adecuado para un único usuario con volumen personal bajo). Si el volumen crece en el futuro, será necesario paginar por `(updated_at, id)`.
