# ADR-005: Fuentes de la tasa BCV

- **Date:** 2026-11-19
- **Status:** Accepted

## Context

- El BCV publica la tasa oficial una vez por día hábil, de noche (alrededor de las 22:40 hora de Caracas), con una fecha valor que indica el día en que rige. Los fines de semana y feriados rige la última tasa publicada.
- El BCV no tiene API: la tasa solo está en el HTML de `https://www.bcv.org.ve/`, dentro de `div#dolar` (por ejemplo, `<strong>917,77510000</strong>`, con coma decimal). La fecha valor está en el atributo `content` de `span[property="dc:date"]` (por ejemplo, `2026-11-19T00:00:00-04:00`).
- El servidor del BCV envía un certificado intermedio equivocado: el certificado del sitio lo firma "Sectigo Public Server Authentication CA OV R36", pero el servidor envía "Sectigo RSA Domain Validation Secure Server CA". Con verificación estricta, la conexión falla (`unable to verify the first certificate`).
- `https://ve.dolarapi.com/v1/dolares/oficial` expone la misma tasa en JSON (`promedio`, `fechaActualizacion`). El 2026-11-19 coincidió exactamente con el BCV (917,7751). `pydolarve.org` ya no existe.
- Si se lee mal la tasa, todos los montos en bolívares quedan mal. Ningún dato debe cambiar en silencio.

## Decision

1. El HTML del BCV es la fuente principal. Se valida que la tasa tenga formato numérico válido, que la fecha valor sea coherente y que el valor no se mueva más de un umbral configurable respecto de la última tasa guardada.
2. TLS se verifica siempre. El repo incluye el certificado intermedio "Sectigo Public Server Authentication CA OV R36" (vence el 2036-03-21) y se suma a los certificados del sistema solo para las peticiones al BCV. Nunca se desactiva la verificación.
3. `ve.dolarapi.com` se consulta como control. Si su valor difiere del BCV en más de 0,5 %, se guarda la tasa del BCV marcada como `en revisión` y se registra la diferencia. La tasa oficial nunca se reemplaza en silencio.
4. Si el BCV falla, se guarda el valor de `ve.dolarapi.com` como respaldo, con la fuente indicada.
5. Si fallan las dos, no se guarda nada nuevo: rige la última tasa conocida y la app la muestra como desactualizada.
6. Cada tasa guarda la fuente usada, el valor en `NUMERIC`, la fecha valor, `fetched_at` en UTC y su estado (`ok`, `en revisión` o `respaldo`).
7. Existe un comando CLI para cargar la tasa a mano en emergencias, registrado con fuente `manual`.
8. La consulta corre cada noche después de la publicación, con reintentos, y una vez al arrancar si falta la tasa del día.

## Alternatives considered

- Solo el HTML del BCV: un cambio en la página o una caída deja la app sin tasa y sin forma de detectar una lectura errónea.
- Solo una API de terceros: el tercero también lee el BCV, y si se equivoca o desaparece, el error llega sin que nadie lo note.
- Carga manual como fuente normal: es la tarea repetitiva que la app viene a eliminar. Queda solo para emergencias.

## Consequences

- Hay dos clientes (BCV y `dolarapi`) y una regla de reconciliación, cada uno con pruebas que usan HTML y JSON guardados como fixtures.
- Si el BCV cambia su HTML, el parser falla y el sistema sigue funcionando con el respaldo. Hay que actualizar el parser y su fixture.
- Si el BCV corrige su certificado intermedio o cambia de proveedor, hay que actualizar el certificado incluido en el repo.
- Las tasas en `en revisión` o `respaldo` necesitan una forma de verse y corregirse. Se define junto con el comando manual.
- La tasa USDT/Bs se decide en un ADR aparte.
