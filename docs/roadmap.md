# Roadmap

## Principios

- Una fase por módulo de la v1, en el orden en que los otros módulos los necesitan: las tasas primero, porque los gastos, las compras y las metas convierten montos con ellas.
- Cada fase termina con `ruff`, `mypy --strict` y `pytest` en verde, y con `docker compose up` funcionando.
- Lo que se corrija en la autenticación o la infraestructura heredadas de la plantilla se corrige primero en la plantilla y después se trae acá.

## Fase 0 — Base desde la plantilla

Proyecto creado desde `fastapi-starter-kit`: autenticación, usuarios, `/health`, Docker y calidad. Hecha.

## Fase 1 — Tasas de cambio

Consulta periódica de BCV y USDT/Bs, historial de tasas y endpoint con la última tasa conocida. Hecha.

## Fase 2 — Ingresos recurrentes

Reglas de repetición, generación de ocurrencias y confirmación de cada ocurrencia como recibida. Hecha.

## Fase 3 — Gastos por categoría

Categorías y gastos con su moneda y la tasa vigente al registrarlos. Hecha.

## Fase 4 — Productos y lista de compras

Catálogo propio, último precio, historial de precios y recomendación de compras.

## Fase 5 — Metas

Monto objetivo en dólares, avance y equivalentes en bolívares.

## Más adelante

- La v2 de la app: escaneo de facturas, precios de supermercados y saldos de Binance.
- Lo que la plantilla deja para más adelante: CI, limpieza de refresh tokens y límite de intentos de login.

## Referencias externas

- Visión del producto: `finance/docs/00-vision.md`.
- Plantilla de origen: https://github.com/Jeyker-Dev/fastapi-starter-kit
- OAuth 2.0 Security Best Current Practice (rotación de refresh tokens): https://datatracker.ietf.org/doc/html/rfc9700
