# Backlog

## Más adelante

- [ ] [INFRA] CI con GitHub Actions — un push ejecuta ruff, mypy y pytest contra un PostgreSQL de servicio y falla si alguno falla — depends on: -
- [ ] [DB] Limpieza de refresh tokens vencidos — un comando borra los tokens vencidos o revocados hace más de `REFRESH_TOKEN_TTL_DAYS` días y no toca los activos — depends on: -
- [ ] [AUTH] Límite de intentos de login — tras N intentos fallidos para un mismo email en una ventana de tiempo, `/auth/token` responde 429 — depends on: -
