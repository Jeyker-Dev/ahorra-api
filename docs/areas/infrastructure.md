# Infraestructura

## Configuración

Variables de entorno leídas con pydantic-settings en `src/app/config.py`. `.env.example` las lista todas con valores vacíos o de ejemplo, y `.env` nunca se versiona.

| Variable | Ejemplo | Regla |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://app:app@db:5432/app` | Obligatoria |
| `JWT_SECRET` | (`openssl rand -hex 32`) | Obligatoria, al menos 32 caracteres; si no, la API no arranca |
| `ACCESS_TOKEN_TTL_MINUTES` | `15` | Opcional |
| `REFRESH_TOKEN_TTL_DAYS` | `30` | Opcional |
| `BCV_TOLERANCE_PERCENT` | `0.5` | Opcional; tolerancia porcentual entre BCV HTML y dolarapi |
| `INCOME_HORIZON_MONTHS` | `3` | Opcional; meses calendario del horizonte de ocurrencias de ingresos |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | `app` | Solo para el servicio `db` de compose |

## Docker

- `Dockerfile` multi-stage: la etapa `builder` instala las dependencias con uv a partir de `uv.lock` (sin las de desarrollo) y descarga y verifica por SHA-256 el binario de `supercronic` (`v0.2.51`). La etapa `runtime` instala `tzdata` para el soporte de `America/Caracas`, copia el entorno virtual, el código, `docker/` y `supercronic`, agrega el entorno virtual al `PATH` y corre como usuario no root (`app`).
- Dentro del contenedor los comandos se corren directo (`alembic`, `uvicorn`, `python`, `supercronic`), sin `uv run`, para que uv no intente sincronizar las dependencias de desarrollo al arrancar.
- `docker-compose.yml`:
  - `db`: PostgreSQL 17 con volumen persistente y `healthcheck` (`pg_isready`).
  - `api`: depende de `db` en estado `healthy`, corre `alembic upgrade head` y después Uvicorn en el puerto 8000, expone `healthcheck` sobre `GET /health` con `urllib` y lee `.env`.
  - `scheduler`: reutiliza la misma imagen de `api`, corre `supercronic /app/docker/scheduler/crontab` como usuario no root con `TZ=America/Caracas` (tasas BCV/USDT y extensión diaria del horizonte de ingresos a las `00:30`), depende de `db` y `api` en estado `healthy` (garantizando que las migraciones ya corrieron), lee `.env` y usa `restart: unless-stopped`.
- El mismo compose sirve en local y en el VPS; en el VPS solo cambia el `.env`.

## Salud

`GET /health` ejecuta `SELECT 1` contra la base. Responde 200 `{"status": "ok"}` si responde y 503 `{"status": "unavailable"}` si no.

## Calidad

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

Configuración en `pyproject.toml` (de la skill `python-conventions`, más `asyncio_mode` para los tests async):

```toml
[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = [
  "E", "F", "I", "N", "UP", "B", "SIM",
  "C90",      # cyclomatic complexity
  "FBT",      # boolean trap: no flag parameters
  "PLR2004",  # magic value comparison
  "RET505",   # no else after return
  "PLR0913",  # too many arguments
  "PLR0915",  # too many statements
]

[tool.ruff.lint.mccabe]
max-complexity = 10

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["PLR2004", "FBT"]

[tool.mypy]
strict = true
plugins = ["pydantic.mypy"]
files = ["src", "tests"]

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["*_test.py"]
asyncio_mode = "auto"
```

## Producción (VPS)

La API corre en el mismo VPS que FunWork y lifeforge-api, detrás del proxy compartido.

- **Dominio**: `https://ahorra-api.duckdns.org`. El Caddy compartido (`~/proxy` en el VPS) termina TLS y redirige al contenedor `api` por la red Docker externa `edge`, con el alias `ahorra-api:8000`. El sitio vive en `~/proxy/sites/ahorra.caddy`.
- **Stack**: `compose.prod.yml` levanta `api`, `scheduler` y `db`. La base no publica puertos; solo es accesible dentro del stack. Las migraciones no corren al arrancar el contenedor, sino desde `scripts/deploy.sh` antes de reiniciar.
- **Directorio en el VPS** (`~/ahorra-api`): `.env` de producción (permisos 600, nunca en el repo), `src/` (clon del repo), `compose.prod.yml`, los scripts de backup y `backups/`.
- **Deploy**: `scripts/deploy.sh` corre los checks locales y, por SSH, actualiza `src/`, construye la imagen `ahorra-api:<sha>`, hace un backup, migra y reinicia. Conserva la imagen anterior para volver atrás. El destino se configura con `AHORRA_DEPLOY_HOST` (o un `.deploy.env` ignorado por git).
- **Backups**:
  - Local: `backup_db.sh` guarda un `pg_dump` comprimido en `~/ahorra-api/backups` en cada deploy y todos los días a las 03:35 (hora del VPS), conservando los 14 más recientes.
  - Externo: `backup_offsite.sh` sube el dump y el `.env` con restic al bucket S3 `ahorra-api-backups` todos los días a las 03:45, con retención de 7 diarios, 4 semanales y 6 mensuales. Las credenciales y la contraseña de restic están en `~/.config/backup/ahorra-api.env` (permisos 600). Sin la contraseña de restic los backups no se pueden restaurar.
- **Usuario**: se crea una sola vez con `scripts/create_user.py` dentro del contenedor `api`.
