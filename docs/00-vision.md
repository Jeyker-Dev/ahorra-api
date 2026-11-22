# Visión

## Qué es

Backend de Ahorra, la app móvil de finanzas personales en Flutter. Es una API en FastAPI creada desde la plantilla [fastapi-starter-kit](https://github.com/Jeyker-Dev/fastapi-starter-kit), que ya trae autenticación, base de datos, migraciones, Docker y tests. Este repositorio le agrega el dominio de Ahorra.

La visión del producto (problema, usuario y módulos) vive en el repositorio de la app, en `finance/docs/00-vision.md`. Este documento cubre solo lo que le toca a la API.

## Responsabilidades de la API

- Guardar los datos de la app y servirlos para que la app los sincronice. La app es offline-first: escribe primero en su base local y después los envía a la API.
- Consultar las tasas de cambio (BCV y USDT/Bs) varias veces al día y guardar su historial, para que la app tenga siempre la última tasa conocida.
- Aplicar las reglas que no pueden depender del dispositivo, como el historial de precios por producto o las ocurrencias de los ingresos recurrentes.

## Alcance de la v1

- **Tasas de cambio**: consulta periódica de BCV y USDT/Bs, historial y endpoint con la última tasa.
- **Ingresos recurrentes**: reglas de repetición y ocurrencias esperadas o recibidas.
- **Gastos por categoría**: cada gasto con su moneda y la tasa vigente al registrarlo.
- **Productos y lista de compras**: catálogo propio, último precio, historial de precios y recomendación de compras.
- **Metas**: monto objetivo en dólares y su avance.

## Fuera de alcance

- Todo lo que la visión de la app deja fuera de alcance: operar en Binance, conectar bancos, scraping de HTML de precios de tiendas, multiusuario e iOS.
- La lectura HTML de la tasa oficial del BCV es la única excepción, porque el BCV no ofrece una API pública (FunWork ADR-001).
- La v2 de la app (escaneo de facturas, precios de supermercados y saldos de Binance) hasta cerrar la v1.
- Registro público: los usuarios se crean con `scripts/create_user.py`.

## Objetivos

- Que la app pueda trabajar sin conexión y sincronizar sin perder ni duplicar datos.
- Que todo monto se guarde como `Decimal` con su moneda, y que cada conversión guarde la tasa que usó.
- Que `ruff`, `mypy --strict` y `pytest` sigan en verde en cada commit.
