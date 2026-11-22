# Ingresos recurrentes

## Conceptos

- **Regla de ingreso (`IncomeRule`)**: define un ingreso periódico con `id` (UUID generado por el cliente), `name` (1–100 caracteres, sin espacios extremos ni en blanco), `amount` (`Decimal` > 0, cuantizado a 2 decimales con `ROUND_HALF_EVEN`), `currency` (`usd` | `ves`), `days` (`tuple[int, ...]`, días del mes entre 1 y 31, ordenados y sin duplicados), `start_date`, `end_date` opcional, `ended_at` (`TIMESTAMPTZ` UTC cuando se cierra la regla), `created_at` y `updated_at`.
- **Ocurrencia (`IncomeOccurrence`)**: instancia concreta en una fecha esperada (`expected_date`). Tiene `id` (UUID generado por el servidor), `rule_id`, `expected_date`, `expected_amount`, `currency`, `status` (`expected` | `received`), campos de cobro (`received_date`, `received_amount`, `rate_value`, `rate_date`, `received_at`) y `updated_at`.

## Reglas de fechas (`src/app/income/domain.py`)

- Para cada mes del rango, cada día en `days` se mapea a ese día o al último día del mes si no existe (ej. 31 → 30 en abril; 29/30/31 → 28 o 29 en febrero según año bisiesto).
- Si varios días colapsan en la misma fecha del mes (ej. 30 y 31 en febrero), se conserva una sola ocurrencia.
- Solo existen ocurrencias dentro de `[start_date, end_date]` (inclusivo; abierto cuando `end_date` es `None`).
- `occurrence_dates(rule, from_date, to_date)` devuelve la lista ordenada de fechas válidas.

## Horizonte y reloj

- `today` se calcula siempre como `caracas_date(clock.now())` (`America/Caracas`).
- El fin del horizonte es `add_months(today, INCOME_HORIZON_MONTHS)` (por defecto 3 meses calendario, ajustando el día al largo del mes destino).
- La ventana de generación siempre termina en `min(end_date, horizon_end)` cuando `end_date` está definido.

## Reglas de negocio (`IncomeService`)

1. **Crear o actualizar (`upsert_rule`)**, idempotente por `id`:
   - Si el `id` es nuevo (incluyendo carreras concurrentes por el mismo `id`), inserta la regla y genera ocurrencias desde `start_date` hasta el fin del horizonte.
   - Si el `id` ya existe y tiene `ended_at` definido, lanza `IncomeRuleEnded` (409).
   - Si los campos normalizados (`name` sin espacios extremos, `amount` cuantizado, `days` ordenados y sin duplicados, `currency`, `start_date`, `end_date`) son idénticos a la regla almacenada, no hace cambios y la devuelve.
   - Si hay cambios, actualiza la regla (`updated_at = clock.now()`), elimina sus ocurrencias con `status = expected` y `expected_date >= today`, y regenera las faltantes desde `max(today, start_date)` hasta el fin del horizonte sin tocar ocurrencias `received` ni `expected` anteriores a `today` y omitiendo fechas ya existentes.
2. **Finalizar regla (`end_rule`)**:
   - Marca `ended_at = clock.now()` y `updated_at = clock.now()` sin modificar `end_date`, y elimina sus ocurrencias con `status = expected` y `expected_date >= today`. Las ocurrencias `received` se conservan.
   - Si ya estaba finalizada, la devuelve sin modificar marcas de tiempo. Si no existe, lanza `IncomeRuleNotFound`.
3. **Extender el horizonte (`generate_occurrences`)**:
   - Para cada regla activa (`ended_at IS NULL` y `end_date IS NULL OR end_date >= today`), inserta las ocurrencias faltantes desde `max(start_date, today)` hasta el fin del horizonte (solo fechas desde hoy hacia adelante, nunca retroactivo) usando `ON CONFLICT (rule_id, expected_date) DO NOTHING`. Devuelve cuántas filas nuevas creó.
4. **Registrar cobro (`receive`)**:
   - Acepta `received_amount` y `received_date` opcionales (por defecto `expected_amount` y `expected_date`), cuantiza `received_amount` a 2 decimales y exige `> 0`.
   - Consulta la tasa BCV vigente en `received_date` mediante `BcvRateLookup` (respaldado por `RatesService.bcv_in_force`), para ambas monedas (`usd` y `ves`). Si no existe tasa, lanza `RateNotAvailableForDate` (409).
   - Pasa `status` a `received` y registra `rate_value`, `rate_date`, `received_at = clock.now()` y `updated_at = clock.now()`. Si ya estaba `received`, actúa como corrección sobrescribiendo los valores.
5. **Deshacer cobro (`unreceive`)**:
   - Solo válido sobre ocurrencias `received` (`OccurrenceNotReceived` en otro caso); vuelve `status` a `expected`, limpia `received_*` y `rate_*` y actualiza `updated_at`.

## IDs offline-first y sincronización

- La app móvil crea la regla localmente en Drift con su propio UUID y la envía por `PUT /income/rules/{rule_id}`, permitiendo reintentos idempotentes sin duplicados.
- Todas las respuestas incluyen `updated_at` para resolver el orden de sincronización en el cliente.

## Endpoints HTTP (`Authorization: Bearer <access_token>`)

- `PUT /income/rules/{rule_id}`: devuelve `201` si creó la regla, `200` si ya existía.
- `GET /income/rules`: arreglo JSON con todas las reglas, activas (`ended_at IS NULL`) primero, luego finalizadas, ordenadas por `name` y `id`.
- `GET /income/rules/{rule_id}`: detalle de la regla (`404` si no existe).
- `DELETE /income/rules/{rule_id}`: finaliza la regla y devuelve `200` con la regla actualizada (`404` si no existe).
- `GET /income/occurrences?from=YYYY-MM-DD&to=YYYY-MM-DD&status=expected|received`: `from` y `to` obligatorios, `status` opcional; filtra por `expected_date` inclusivo, ordena por `expected_date`, `rule_name` e `id`, e incluye `rule_id` y `rule_name` en cada ítem. `from > to` devuelve `422`.
- `POST /income/occurrences/{occurrence_id}/receive`: acepta cuerpo opcional o vacío; devuelve `200` con la ocurrencia actualizada.
- `POST /income/occurrences/{occurrence_id}/unreceive`: revierte un cobro y devuelve `200` con la ocurrencia.
- Los campos `Decimal` se serializan como strings. Errores mapeados en `main.py`: no encontrado → `404`, conflicto de regla/estado/tasa → `409`, validación → `422`.

## Planificación (`scheduler`)

- `scripts/generate_income_occurrences.py` corre todos los días a las `00:30` (`America/Caracas`) desde `docker/scheduler/crontab`.
