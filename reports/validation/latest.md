# Reporte de validación

- Corrida: `20261111T113954030322Z`
- Resultado: **Aceptado: ninguna regla bloqueante falla.**
- SHA-256 del CSV: `fa6d022ed437155e1a2f0378710602848703c8a7f203f7ff5d77805bf8480aa6`
- Entrega: C2018-02; filas de la entrega: 248

## Reglas

| ID | Regla | Severidad | Estado | Filas | Pedidos |
| --- | --- | --- | --- | ---: | ---: |
| V01 | El CSV tiene la huella aceptada (o la registrada, en modo local) | bloqueante | pasa | 0 | 0 |
| V02 | Descarga segura y tamaño: HTTPS en cada salto, host permitido, redirecciones y bytes dentro del límite | bloqueante | pasa | 0 | 0 |
| V03 | Codificación cp1252 estricta, sin bytes nulos ni marca de orden de bytes, con saltos CRLF o LF | bloqueante | pasa | 0 | 0 |
| V04 | El encabezado tiene las 53 columnas exactas, en orden | bloqueante | pasa | 0 | 0 |
| V05 | Cada fila tiene 53 campos, las fechas, los enteros y los decimales convierten y los días no son negativos | bloqueante | pasa | 0 | 0 |
| V06 | El correo y la contraseña del cliente están enmascarados en todas las filas | bloqueante | pasa | 0 | 0 |
| V07 | Cada línea tiene su identificador y cada pedido un solo valor en sus columnas constantes | bloqueante | pasa | 0 | 0 |
| V08 | En los pedidos no cancelados, la etiqueta es «días reales mayores que los programados» | bloqueante | pasa | 0 | 0 |
| V09 | Cada resultado se conoce dentro de los días que cubre la purga | bloqueante | pasa | 0 | 0 |
| V10 | Pedido con días programados distintos de los de su modo de envío | advertencia | pasa | 0 | 0 |
| V11 | Pedido cancelado (queda fuera de la tarea; se puntúa si es nuevo) | advertencia | advierte | 7754 | 2855 |
| V12 | Columnas repetidas que no coinciden (se usa una copia) | advertencia | pasa | 0 | 0 |
| V13 | Importes que no cuadran: ventas contra precio por cantidad, o total contra ventas menos descuento | advertencia | pasa | 0 | 0 |
| V14 | Espacios finales en el nombre del producto (se recortan) | advertencia | advierte | 1774 | 1774 |
| V15 | Columnas personales descartadas en la lectura | informativa | pasa | 0 | 0 |
| V16 | Columnas constantes, vacías o repetidas que no se llevan | informativa | pasa | 0 | 0 |
| V17 | Pedidos desde el cambio de forma de 2017-10 (régimen posterior) | informativa | pasa | 8557 | 8403 |
| V18 | Columnas apartadas al flujo de resultados | informativa | pasa | 0 | 0 |
| V20 | La entrega es la siguiente del plan y sus períodos empiezan el día siguiente al fin de los de la anterior | bloqueante | pasa | 0 | 0 |
| V21 | Cada pedido tiene su fecha dentro del período de pedidos y cada resultado la suya dentro del de resultados | bloqueante | pasa | 0 | 0 |
| V22 | Ningún pedido, línea ni resultado está ya cargado ni repetido dentro de la entrega | bloqueante | pasa | 0 | 0 |
| V23 | Cada línea y cada resultado corresponde a un pedido cargado o de la misma entrega | bloqueante | pasa | 0 | 0 |
| V24 | La entrega no está vacía y sus filas y su SHA-256 son los del manifiesto | bloqueante | pasa | 0 | 0 |
| M02 | Las columnas de la matriz son las del conjunto elegido y ninguna es de resultado ni personal | bloqueante | pasa | 0 | 0 |
| M03 | El artefacto del modelo tiene la huella registrada (se comprueba antes de cargarlo) | bloqueante | pasa | 0 | 0 |
| M04 | Cada pedido nuevo tiene una probabilidad finita en [0, 1] | bloqueante | pasa | 0 | 0 |
| M05 | La configuración es distinta de la sellada (el resultado se guarda aparte, como posterior al sello) | advertencia | pasa | 0 | 0 |
| I01 | Pedidos nuevos en la entrega | informativa | pasa | 0 | 0 |
| I02 | Alertas emitidas y porcentaje de pedidos alertados | informativa | pasa | 8840 | 23600 |
| I03 | Resultados que llegaron en la entrega | informativa | pasa | 248 | 248 |
| I04 | Alertas sobre pedidos que después se cancelaron | informativa | pasa | 419 | 1030 |
| I05 | Pedidos de la entrega desde el cambio de forma de 2017-10 | informativa | pasa | 0 | 0 |

## No evaluadas

Se evalúan en otro momento del flujo: M01.

## Ejemplos

### V15: Columnas personales descartadas en la lectura

- `{"columns": 15}`

### V16: Columnas constantes, vacías o repetidas que no se llevan

- `{"columns": 7}`

### V18: Columnas apartadas al flujo de resultados

- `{"columns": 8}`

### I02: Alertas emitidas y porcentaje de pedidos alertados

- `{"alerts": 8840, "scored": 23600, "alert_share": 0.374576}`

### I04: Alertas sobre pedidos que después se cancelaron

- `{"alerts_on_cancelled": 419, "cancelled_orders": 1030}`
