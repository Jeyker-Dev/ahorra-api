# Changelog

## 2026-11-20

- **Fase 1 — Tasas de cambio**: dominio `rates/`, tabla `exchange_rates` con índice único parcial para BCV, fuentes `BcvHtmlSource` (con certificado intermedio Sectigo OV R36 empaquetado), `DolarApiSource` y `BinanceP2PSource`, servicio con validación cruzada y tolerancia configurable (`BCV_TOLERANCE_PERCENT`), endpoints `GET /rates/latest` y `GET /rates`, scripts CLI (`fetch_rates.py`, `set_rate.py`, `review_rates.py`) y contenedor `scheduler` con `supercronic`.

## 2026-11-18

- API creada desde la plantilla `fastapi-starter-kit` (commit `f4f8304`): autenticación, usuarios, `/health`, Docker y calidad. El historial de la plantilla está en su propio repositorio.
- Documentación adaptada a Ahorra: visión, roadmap por módulos de la v1, sprint y backlog. Se quitó el prompt de construcción de la plantilla.
