# Listas de compras, totales, cierre y recomendaciones

## Conceptos

- **Lista de compras (`ShoppingList`)**: agrupa ítems con `id` (UUID generado por el cliente), `name` (1–80 caracteres sin espacios extremos ni en blanco), `status` (`open` | `closed`), `closed_at` y `expense_id` (ambos obligatorios si `closed`, nulos si `open`), `deleted_at` (`TIMESTAMPTZ` UTC en borrado lógico), `created_at` y `updated_at`.
- **Ítem de lista (`ShoppingListItem`)**: con `id` (UUID generado por el cliente), `list_id`, `product_id` (único `(list_id, product_id)`), `quantity` (`Decimal` > 0 cuantizado a 3 decimales), `status` (`pending` | `bought`), `bought_price` / `bought_currency` / `bought_at` (todos obligatorios si `bought`, nulos si `pending`), `created_at` y `updated_at`. El precio estimado no se almacena en el ítem: siempre refleja el `last_price` actual del producto.

## Integración entre dominios (orden + idempotencia)

`shopping` no importa modelos ni repositorios de otros dominios; interactúa con `ProductsService`, `ExpenseService` y `RatesService` únicamente mediante los protocolos definidos en `shopping/domain.py` y adaptadores finos en `shopping/api.py`.

Para mantener commits por repositorio sin una unidad de trabajo compartida, toda escritura entre dominios ejecuta primero la operación remota idempotente y actualiza la fila de `shopping` al final:

- **`buy_item`**: valida entradas, estado de la lista y tasas del día antes de escribir; luego hace upsert de la entrada `purchase` por `list_item_id` en `ProductsService` (actualizando `last_price` desde la entrada más reciente); por último marca el ítem como `bought`. Un reintento tras un fallo parcial converge al mismo estado.
- **`unbuy_item`**: elimina la entrada `purchase` por `list_item_id` en `ProductsService` (sin efecto si ya no existe) y restaura `last_price`; por último vuelve el ítem a `pending` limpiando `bought_*`.
- **`close_list`**: crea el gasto en `ExpenseService.upsert_expense` con id determinístico `uuid5(SHOPPING_EXPENSE_NAMESPACE, str(list_id))` y nota `"Shopping list: <list name>"`; por último cierra la lista guardando `closed_at` y `expense_id`. Si `ExpenseService` falla (categoría archivada, tasa faltante, etc.), el error se propaga intacto y la lista permanece `open`; un reintento reutiliza el mismo UUID sin duplicar el gasto.

## Reglas de negocio (`ShoppingService`)

1. **Listas (`upsert_list`, `delete_list`)**: `PUT` crea (`201`) o renombra idempotentemente (`200`) una lista abierta; editar una lista cerrada o eliminada lanza `ListClosed` o `ListDeleted` (`409`). `DELETE` aplica borrado lógico idempotente (`deleted_at`). `GET /shopping/lists` excluye eliminadas (`created_at DESC, id ASC`) y acepta `?status=open|closed`.
2. **Ítems (`upsert_item`, `delete_item`)**: exige lista abierta, producto existente y no archivado (`ProductArchived`, `409` al agregar o cambiar a un producto archivado), unicidad `(list_id, product_id)` (`DuplicateListItem`, `409`) y `quantity > 0`. En un ítem `bought` solo se puede editar `quantity`; cambiar `product_id` o eliminarlo lanza `ItemAlreadyBought` (`409`). `delete_item` borra físicamente ítems `pending` (`204`).
3. **Compra (`buy_item`) y reverso (`unbuy_item`)**:
   - `buy_item` toma `price` y `currency` explícitos (ambos juntos, o `422` si falta uno) o usa `last_price` del producto (`PriceRequired`, `422` si no tiene). Exige tasas BCV y USDT vigentes hoy (`RateNotAvailableForDate`, `409`). Comprar un ítem ya `bought` actúa como corrección: actualiza la misma entrada `purchase` y conserva el `bought_at` original.
   - `unbuy_item` devuelve el ítem a `pending` y restaura el `last_price` del producto (o `None` si no quedan entradas). Si el ítem ya está `pending`, es idempotente (`200`). Ambos exigen lista abierta (`ListClosed` / `ListDeleted`, `409`).
4. **Totales (`list_totals`)**: exige tasas BCV y USDT vigentes hoy (incluso si la lista está vacía). Calcula `estimated` (ítems con `last_price`, contando los faltantes en `unpriced_items`) y `bought` (ítems comprados a `bought_price`): suma `price × quantity` en bolívares a precisión completa convirtiendo `usd` a tasa BCV, calcula `total_usd = total_ves / bcv` y `usdt_to_sell = total_ves / usdt`, y cuantiza cada total una sola vez a 2 decimales (`ROUND_HALF_EVEN`). Devuelve también `bcv_rate` y `usdt_rate` (`{value, value_date}`).
5. **Cierre (`close_list`)**: `spent_on` por defecto es hoy (`caracas_date`). Exige al menos un ítem comprado (`NothingBought`, `422`). Si todos los ítems comprados comparten moneda, usa esa moneda y la suma directa; si hay monedas mixtas, convierte los `usd` a `ves` con la tasa BCV vigente en `spent_on`. Ignora ítems `pending`. Cerrar una lista ya cerrada la devuelve sin cambios ignorando el cuerpo. Una lista cerrada no se reabre.
6. **Recomendaciones (`recommendations`)**: acepta `days_ahead` (por defecto `7`, rango `1–60`, `422` fuera de rango) y exige tasas BCV y USDT vigentes hoy. Para cada producto no archivado con al menos 2 fechas distintas de compra (`source = purchase`, excluyendo listas con `deleted_at`): calcula `avg_interval_days` (media de días entre fechas consecutivas, cuantizada a 1 decimal), `next_due = última fecha + round(avg_interval_days)` (`ROUND_HALF_EVEN`), e incluye el producto si `next_due <= hoy + days_ahead`. Calcula `suggested_quantity` como la media de `quantity` de sus ítems comprados en listas no eliminadas (3 decimales), ordena por `next_due, product_name, product_id`, y devuelve `{items, totals}` con el estimado a `last_price`.

## Endpoints HTTP (`Authorization: Bearer <access_token>`)

- `PUT /shopping/lists/{list_id}`: crea (`201`) o actualiza idempotentemente (`200`) una lista.
- `GET /shopping/lists?status=`: lista listas no eliminadas (`created_at DESC, id ASC`).
- `GET /shopping/lists/{list_id}`: devuelve la lista no eliminada con sus ítems ordenados por `created_at ASC, id ASC` (incluyendo `product_name`, `product_unit` y `product_last_price`).
- `DELETE /shopping/lists/{list_id}`: borrado lógico idempotente (`200`).
- `GET /shopping/lists/{list_id}/totals`: totales estimados y comprados con tasas de hoy.
- `POST /shopping/lists/{list_id}/close`: cierra la lista y crea el gasto asociado (`200`).
- `PUT /shopping/lists/{list_id}/items/{item_id}`: agrega (`201`) o actualiza (`200`) un ítem.
- `DELETE /shopping/lists/{list_id}/items/{item_id}`: elimina físicamente un ítem pendiente (`204`).
- `POST /shopping/lists/{list_id}/items/{item_id}/buy`: marca o corrige compra (`200`, cuerpo opcional).
- `POST /shopping/lists/{list_id}/items/{item_id}/unbuy`: revierte compra a pendiente (`200`).
- `GET /shopping/recommendations?days_ahead=7`: productos recomendados y totales estimados.
