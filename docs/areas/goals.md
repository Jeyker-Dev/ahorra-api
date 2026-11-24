# Metas de ahorro y aportes

## Conceptos

- **Meta (`Goal`)**: objetivo de ahorro con `id` (UUID generado por el cliente), `name` (1–50 caracteres sin espacios extremos ni en blanco, único sin distinguir mayúsculas/minúsculas entre metas no eliminadas), `target_usd` (`Decimal` > 0, 2 decimales), `target_date` (`DATE` opcional), `note` opcional (sin espacios extremos, máximo 500 caracteres; vacía o espacios pasa a `None`), `archived_at`, `deleted_at`, `created_at` y `updated_at`.
- **Aporte (`GoalContribution`)**: movimiento puntual hacia o desde una meta con `id` (UUID generado por el cliente), `goal_id`, `kind` (`deposit` | `withdrawal`), `amount` (`Decimal` > 0, 2 decimales), `currency` (`usd` | `ves`), `contributed_on` (`DATE`), `note` opcional, las dos tasas vigentes en `contributed_on` (`bcv_rate_value`, `bcv_rate_date`, `usdt_rate_value`, `usdt_rate_date`), `deleted_at`, `created_at` y `updated_at`.

## Por qué el progreso es una suma de aportes

La meta no almacena un saldo propio: su avance se calcula siempre al leer sumando los aportes no eliminados (`GoalContributionRepository.sum_saved_usd_by_goal_ids` en una sola consulta agregada por `goal_id` para listados). Cada aporte conserva las tasas BCV y USDT vigentes en `contributed_on`, de modo que su valor histórico en USD nunca cambia cuando llegan tasas nuevas o se consultan equivalentes posteriores.

## Reglas de negocio (`GoalsService`)

1. **Crear o actualizar meta (`upsert_goal`)**, idempotente por `id`:
   - Valida `name`, `target_usd` y `note` (`422` si son inválidos). Si la meta ya existe con `deleted_at`, lanza `GoalDeleted` (`409`).
   - Si los campos normalizados coinciden con los guardados, devuelve la meta sin cambios; si cambian, exige nombre libre entre metas no eliminadas (`GoalNameTaken`, `409`). Permite editar metas archivadas y reutilizar el nombre de una meta eliminada.
2. **Archivar (`archive_goal`), desarchivar (`unarchive_goal`) y eliminar (`delete_goal`)**:
   - Operaciones idempotentes que solo actualizan `archived_at`/`deleted_at` y `updated_at` cuando cambia el estado (`GoalNotFound`, `404` si falta o fue eliminada en `archive`/`unarchive`/`get`). `delete_goal` oculta la meta y sus aportes sin eliminar los aportes físicamente.
3. **Crear, actualizar o mover aporte (`upsert_contribution`)**, idempotente por `id`, en este orden:
   - (a) Valida `amount` y `note` (`InvalidContributionAmount`, `InvalidContributionNote`, `422`).
   - (b) Si el aporte ya existe con `deleted_at` definido, lanza `ContributionDeleted` (`409`).
   - (c) Verifica que la meta destino exista y no esté eliminada (`GoalNotFound`, `404`).
   - (d) Si ya existe y todos los campos normalizados (incluido `goal_id`) son idénticos, lo devuelve sin cambios.
   - (e) Crear un aporte nuevo o mover uno existente a una meta archivada lanza `GoalArchived` (`409`); editar otros campos dentro de la misma meta archivada está permitido.
   - (f) Consulta `RatesService.bcv_in_force` y `usdt_in_force` para `contributed_on` al crear o cuando cambia `contributed_on` (`RateNotAvailableForDate`, `409` si falta alguna); si `contributed_on` no cambia, conserva las tasas guardadas.
   - Los retiros (`withdrawal`) pueden dejar el saldo acumulado negativo sin error.
4. **Eliminar (`delete_contribution`) y listar (`list_contributions`)**:
   - Exigen meta existente y no eliminada (`GoalNotFound`, `404`). `delete_contribution` valida que el aporte pertenezca a `goal_id` (`ContributionNotFound`, `404`) y marca `deleted_at` idempotentemente. `list_contributions` ordena por `contributed_on DESC, created_at DESC, id ASC`.
5. **Cálculo de progreso y equivalentes en bolívares**:
   - `saved_usd`: suma a precisión completa de los aportes no eliminados (`usd` tal cual, `ves / bcv_rate_value`, retiros negativos), cuantizada una sola vez a 2 decimales (`ROUND_HALF_EVEN`).
   - Derivados desde `saved_usd`: `remaining_usd = max(target_usd - saved_usd, 0)`, `percent = saved_usd / target_usd * 100` (1 decimal, sin tope ni piso) y `reached = saved_usd >= target_usd`.
   - Equivalentes en bolívares con las tasas vigentes **hoy** (`caracas_date(clock.now())`): `saved_ves_bcv`, `saved_ves_usdt`, `remaining_ves_bcv`, `remaining_ves_usdt` (`USD × tasa`, 2 decimales) junto a `bcv_rate` y `usdt_rate` (`{value, value_date}`). Si falta cualquiera de las dos tasas de hoy, esos cuatro campos y ambos objetos de tasa devuelven `null` sin fallar la consulta.

## Endpoints HTTP (`Authorization: Bearer <access_token>`)

Todas las rutas de metas devuelven un `GoalOut` uniforme que incluye `progress`.

- `PUT /goals/{goal_id}`: crea (`201`) o actualiza (`200`) una meta.
- `GET /goals?include_archived=false`: lista metas no eliminadas (activas primero, luego archivadas, por `name` e `id`) con progreso calculado en lote.
- `GET /goals/{goal_id}`: devuelve una meta no eliminada con su progreso (`404` si falta o fue eliminada).
- `POST /goals/{goal_id}/archive` y `/unarchive`: archiva o desarchiva una meta (`200`).
- `DELETE /goals/{goal_id}`: borrado lógico idempotente (`200` con `deleted_at` y `progress`).
- `PUT /goals/{goal_id}/contributions/{contribution_id}`: crea (`201`), actualiza o mueve (`200`) un aporte.
- `GET /goals/{goal_id}/contributions`: lista aportes no eliminados de la meta.
- `DELETE /goals/{goal_id}/contributions/{contribution_id}`: borrado lógico idempotente (`200` con `deleted_at`).
