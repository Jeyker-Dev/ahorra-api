# Catálogo de productos e historial de precios

## Conceptos

- **Producto (`Product`)**: artículo reutilizable del catálogo con `id` (UUID generado por el cliente), `name` (1–80 caracteres sin espacios extremos ni en blanco, único sin distinguir mayúsculas/minúsculas), `unit` (`unit` | `kg` | `l`), `last_price` (`Decimal` > 0 cuantizado a 2 decimales con `ROUND_HALF_EVEN`, opcional) y `last_price_currency` (`usd` | `ves`, ambos nulos o ambos definidos), `archived_at` (`TIMESTAMPTZ` UTC cuando se archiva), `created_at` y `updated_at`. Los productos nunca se borran físicamente (`ON DELETE RESTRICT` en `product_prices` y `shopping_list_items`).
- **Entrada de precio (`PriceEntry`)**: registro histórico con `id` (UUID del servidor), `product_id`, `price`, `currency`, `recorded_on` (`DATE` en `America/Caracas`), `source` (`manual` | `purchase`), `list_item_id` (obligatorio cuando `source = purchase`, nulo cuando `source = manual`, único cuando está definido), tasas vigentes ese día (`bcv_rate_value`, `bcv_rate_date`, `usdt_rate_value`, `usdt_rate_date`), `created_at` y `updated_at`. Solo es de inserción (append-only), salvo la corrección in-place (`updated_at`) o el reverso de compra desde `shopping`.

## Reglas de negocio (`ProductsService`)

1. **Crear o actualizar (`upsert_product`)**, idempotente por `id`:
   - Valida y normaliza `name` (1–80 caracteres) y verifica que `last_price` y `currency` vengan ambos definidos o ambos nulos (`InvalidProductPrice`, `422` si falta uno o el precio no es positivo).
   - En un producto existente, enviar `last_price` y `currency` nulos u omitidos conserva el precio almacenado sin cambios.
   - Si el nombre ya pertenece a otro `id` (sin distinguir mayúsculas/minúsculas), lanza `ProductNameTaken` (`409`). Permite renombrar solo mayúsculas/minúsculas sobre el mismo `id`.
   - Si al crear se informa precio o si en una actualización cambia el precio/moneda respecto al guardado, exige las tasas BCV y USDT vigentes hoy (`caracas_date(clock.now())`; lanza `RateNotAvailableForDate`, `409`, si falta alguna) y agrega una entrada `manual` en `product_prices`. Un reintento idéntico no modifica `updated_at` ni duplica entradas.
2. **Archivar (`archive_product`) y desarchivar (`unarchive_product`)**:
   - Operaciones idempotentes que actualizan `archived_at` y `updated_at` solo cuando cambia el estado (`ProductNotFound`, `404` si no existe). Los productos archivados no pueden agregarse a una lista de compras.
3. **Historial de precios (`price_history`)**:
   - Devuelve las entradas de `product_prices` en el rango inclusivo `[from, to]` sobre `recorded_on`, ordenadas por `recorded_on DESC, created_at DESC, id ASC` (`422` si `from > to`, `404` si el producto no existe).
4. **Sincronización con compras (`record_purchase_price`, `delete_purchase_price_and_restore`)**:
   - `record_purchase_price` realiza un upsert idempotente por `list_item_id` (actualizando la misma fila al corregir una compra) y recalcula `last_price` / `last_price_currency` a partir de la entrada más reciente del producto (`recorded_on DESC, created_at DESC, id ASC`), de modo que corregir una compra anterior nunca sobrescribe un precio más nuevo.
   - `delete_purchase_price_and_restore` elimina la entrada `purchase` de `list_item_id` (sin efecto si ya no existe) y restaura `last_price` / `last_price_currency` a la entrada restante más reciente, o `None` si no queda ninguna.

## Endpoints HTTP (`Authorization: Bearer <access_token>`)

- `PUT /products/{product_id}`: crea (`201`) o actualiza idempotentemente (`200`) un producto.
- `GET /products?include_archived=false`: lista productos (activos por defecto; `include_archived=true` devuelve activos primero y luego archivados, ordenados por `name` e `id`).
- `GET /products/{product_id}`: devuelve un producto por id (`404` si no existe).
- `POST /products/{product_id}/archive` y `/unarchive`: archiva o desarchiva un producto (`200`).
- `GET /products/{product_id}/prices?from=YYYY-MM-DD&to=YYYY-MM-DD`: devuelve el historial de precios en el rango inclusivo (`422` si falta algún parámetro o `from > to`).
- Los valores `Decimal` se serializan como strings. Errores mapeados en `main.py`: no encontrado → `404`, nombre duplicado o tasa faltante → `409`, validación → `422`.
