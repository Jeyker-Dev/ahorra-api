# Documentación de Ahorra API

## Mapa

| Archivo | Qué contiene |
|---|---|
| [00-vision.md](00-vision.md) | Qué hace la API de Ahorra, alcance de la v1 y fuera de alcance |
| [01-architecture.md](01-architecture.md) | Stack, estructura de carpetas, capas, convenciones y rutas |
| [roadmap.md](roadmap.md) | Fases y referencias externas |
| [areas/auth.md](areas/auth.md) | Endpoints, reglas y errores de autenticación y usuarios |
| [areas/rates.md](areas/rates.md) | Fuentes de tasas BCV/USDT, validación cruzada, CLI y scheduler |
| [areas/income.md](areas/income.md) | Reglas de ingresos recurrentes, ocurrencias, horizonte, cobros y endpoints |
| [areas/database.md](areas/database.md) | Tablas, reglas de migraciones y riesgos |
| [areas/infrastructure.md](areas/infrastructure.md) | Variables de entorno, Docker, salud y comandos de calidad |
| Decisiones (FunWork) | Los ADR viven en el módulo de Decisiones de FunWork, proyecto Ahorra; no en este repo |
| Tareas (FunWork) | Las tareas viven en FunWork, proyecto Ahorra, área backend; no en este repo |
| [changelog.md](changelog.md) | Qué se hizo y cuándo |

## Ciclo de trabajo

1. Antes de una tarea: leer `AGENTS.md`, `00-vision.md`, `01-architecture.md` y las áreas que toca. Crear la tarea en FunWork (proyecto Ahorra, área backend) si no existe y pasarla a `Doing`.
2. Implementar con test primero cuando haya una regla verificable, y dejar `ruff`, `mypy` y `pytest` en verde.
3. Al terminar: pasar la tarea a `Done` en FunWork con el commit en la descripción, agregar una entrada en `changelog.md`, actualizar el área si cambió una regla y registrar un ADR en FunWork si se tomó una decisión de arquitectura.
