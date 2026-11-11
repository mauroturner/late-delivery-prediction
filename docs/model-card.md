# Ficha del modelo

Qué hace el modelo, con qué datos, en qué momento, cómo se validó, qué resultados dio y hasta dónde se puede confiar en él. Las cifras de resultados salen de `reports/evaluation_summary.json` y del sello `reports/model_seal.json`, que escribió la corrida real del 2026-11-11 (D81), la misma fuente del README; los criterios y sus umbrales se fijaron antes de escribir el código (`docs/requirements.md`, sección 8.1; D16) y ninguno se movió después de ver un resultado.

## Uso previsto y no previsto

**Para qué sirve.** Estimar, en el momento en que se crea un pedido y con lo que se sabe en ese momento, la probabilidad calibrada de que llegue tarde (días reales de envío mayores que los programados), marcar con una alerta los pedidos cuyo riesgo alcanza un umbral elegido por costo y seguir cada mes cuántas de esas alertas acertaron. Sirve para ordenar qué pedidos mirar antes, para comparar el modelo con dos reglas simples y para alimentar un tablero de seguimiento.

**Para qué no sirve.**

- No predice al despachar: la fecha de envío de la fuente ya contiene la demora (es, en los hechos, la de llegada), y el conjunto no permite validar ese momento (D03).
- No pone un costo real a una alerta ni a un retraso: los costos son supuestos y declarados (D14); otros costos dan otro umbral.
- No trata los pedidos cancelados como una clase: quedan fuera de la tarea, aunque se puntúan como cualquier pedido nuevo y se cuentan (D05).
- No dice por qué un pedido se demora: el modelo aprende el modo de envío y el corte del mediodía de Same Day, y nada más (ver «Criterios y resultados»).
- No se reentrena cada mes: se entrena una vez, en el lanzamiento, y queda congelado (D17).
- No es un servicio: la función de puntuación queda lista para envolverla, pero no hay una API (D33; una con FastAPI queda a decisión del dueño).
- No cubre otra empresa ni otros datos que los de este archivo, que muestran rasgos de datos generados (ver «Limitaciones»).

## Datos

«DataCo SMART SUPPLY CHAIN FOR BIG DATA ANALYSIS», Mendeley Data, versión 5 (Constante, Silva y Pereira, 2019; CC BY 4.0): un CSV de 95.910.149 bytes en cp1252 con CRLF, SHA-256 `fa6d022e…0aa6` (el que publica la API de archivos de Mendeley), 180.519 líneas de 53 columnas y 65.752 pedidos del 2015-01-01 al 2018-01-31. Detalle de cada columna en `docs/data-dictionary.md`; las 33 reglas de validación y sus recuentos reales, en `reports/validation/latest.md` y en `outputs/site/validation_rules.json`.

- **La unidad es el pedido** (D04). Veinte columnas tienen un solo valor dentro de cada pedido (modo de envío, mercado, región, fechas, días programados y reales, etiqueta, estados); las de producto, descuento y rentabilidad cambian por línea y se agregan.
- **Cancelados** (D05). 2.855 pedidos completos (7.754 líneas, `Delivery Status = Shipping canceled`) quedan fuera de la tarea. Quedan 62.897 pedidos, de los que 36.048 (57,3 %) llegaron tarde.
- **Datos personales** (D07). Las 15 columnas personales (nombres, correo, contraseña, calle, códigos postales, ciudades y estados, identificadores del cliente, latitud y longitud) se usan solo en las comprobaciones de la lectura y se descartan antes de escribir cualquier archivo: no llegan a SQLite, a los Parquet, al HTML, a los JSON ni a los registros. Un correo o una contraseña sin enmascarar rechaza la fuente (V06).
- **Dos flujos** (D06, D09). Los pedidos se publican por su fecha de creación, con solo las columnas que existen al crearlos; los resultados (estados, días reales, fecha de envío, etiqueta y rentabilidad), por la fecha en que se conocen: `max(fecha del pedido + días reales, fecha del envío)`, a lo sumo seis días después del pedido. Un pedido no tiene etiqueta en el almacén hasta que llega su resultado.
- **Entregas** (D09). El archivo no cambia, así que el flujo lo reparte en un lanzamiento con lo conocido al 2017-01-07 (42.152 pedidos), trece entregas mensuales de 2017-01 a 2018-01 (de 1.356 a 2.124 pedidos cada una) y un cierre al 2018-02-07 que solo trae 248 resultados. Cada pedido, línea y resultado está exactamente en una entrega.
- **El cambio de forma.** Desde 2017-10, 8.340 de los 8.403 pedidos tienen una línea, cuando antes tenían de una a cinco casi por igual: ese tramo se evalúa aparte (C10).

## Momento de la predicción

Cada pedido recibe su riesgo al crearse, con lo que existe en ese momento (D03). El agente de planificación midió en el bloque de entrenamiento que la fecha de envío es la del pedido más exactamente 12 horas en Same Day, 48 en First Class y de 48 a 144 horas en pasos de 24 en Second y Standard Class, y que los días reales son la diferencia en días de calendario entre las dos en el 100 % de los pedidos: la columna «de envío» es la de llegada, y con ella y el modo el AUC en la prueba llega a 0,9985 (`research.md`). Predecir al despachar sería usar la respuesta. Todo lo calculado en una fecha de cálculo usa solo pedidos creados y resultados conocidos hasta esa fecha; una prueba de perturbación lo comprueba con igualdad exacta (AC14).

## Variables

Tres conjuntos, de una lista permitida (D11):

- **F0**: el modo de envío y sus días programados.
- **F1**: F0 más el mercado, la región y el país del pedido, el país y el segmento del cliente, el tipo de pago, el producto, la categoría y el departamento principales, el número de departamentos, de líneas y de unidades, las ventas, el total, el descuento y la mayor tasa de descuento del pedido.
- **F2**: F1 más la hora, el día de la semana, el día del mes y el mes de creación del pedido. El modelo elegido usa F2.

Ocho columnas no llegan nunca a la matriz (D06): `Days for shipping (real)`, `Delivery Status`, `shipping date (DateOrders)` y `Late_delivery_risk` (la etiqueta) delatan el resultado; `Order Status` no se conoce al crear el pedido; y `Benefit per order`, `Order Profit Per Order` y `Order Item Profit Ratio` se excluyen por precaución, porque no consta cuándo se calculan. Cuatro guardas lo impiden: `build_features` lanza `LeakageError` si recibe o produce una columna fuera de la lista; una prueba estática comprueba que el código y el SQL de las variables no leen ninguna tabla de resultados; una prueba de perturbación cambia todos los resultados y no cambia ninguna variable; y la trampa M01 detiene el lanzamiento si el AUC del modelo elegido en V1 llega a 0,95 (D36). Lo que haría cada columna excluida, medido en V2 junto al modo de envío: los días reales y el estado de entrega dan un AUC de 1,000, la fecha de envío 0,998, y el modo solo 0,743 (`outputs/site/leakage.json`).

Importancia por permutación del modelo en uso, medida en V2 (caída media del AUC al permutar cada variable; `fact_importance`):

| Variable | Caída del AUC |
| --- | ---: |
| Modo de envío | 0,2620 |
| Hora del pedido | 0,0460 |
| Días programados | 0,0012 |
| Número de líneas | 0,0006 |
| Segmento del cliente | 0,0002 |
| Las otras 17 (mercado, región, país, producto, categoría, departamento, montos, descuentos, día y mes) | de −0,0040 a 0,0002 |

## Partición

Por fecha del pedido, con purgas de siete días que no entran en ningún bloque (D08). Cada etiqueta del entrenamiento se conoce antes del inicio de V1 y cada una de la validación antes del lanzamiento (AC15); los recuentos reales son los del plan.

| Bloque | Pedidos del | al | Pedidos | Líneas | Uso |
| --- | --- | --- | ---: | ---: | --- |
| Entrenamiento | 2015-01-01 | 2016-06-30 | 29.894 | 89.531 | Ajustar los candidatos y las tasas de las reglas |
| Purga 1 | 2016-07-01 | 2016-07-07 | 375 | 1.150 | Ninguno |
| V1 | 2016-07-08 | 2016-09-30 | 4.649 | 29.199 con V2 | Elegir el candidato y calibrarlo |
| V2 | 2016-10-01 | 2016-12-31 | 5.032 | | Elegir el umbral por costo |
| Purga 2 | 2017-01-01 | 2017-01-07 | 377 | 1.094 | Ninguno |
| Prueba | 2017-01-08 | 2017-09-30 | 14.527 | 43.598 | Evaluar una sola vez, bajo el sello |
| Posterior al cambio de forma | 2017-10-01 | 2018-01-31 | 8.043 | 8.193 | Informar aparte (C10) |

## Métodos

**Clasificador** (D12). `HistGradientBoostingClassifier` de scikit-learn 1.9.1 con una grilla fija: tasa de aprendizaje 0,05 o 0,1, 15 o 31 hojas, 20 o 100 pedidos por hoja, regularización L2 1,0, 300 iteraciones sin parada temprana y semilla 0. Ocho configuraciones por conjunto de variables, 24 ajustes, todos en el entrenamiento. Gana la de menor pérdida logarítmica en V1: F2, tasa 0,05, 15 hojas y 100 pedidos por hoja (pérdida 0,5005 y AUC 0,7757 en V1).

**Líneas base** (D15), con tasas calculadas solo en el entrenamiento: B0 todo tarde; B1 solo First Class; B2 la tasa de retraso del modo (First 1,000; Second 0,8017; Same Day 0,4652; Standard 0,4013), que marca si es 0,5 o más; B3 días programados de dos o menos; B4 solo Standard Class, un control negativo; y B5 la tasa del modo y de la franja del pedido, antes o desde las 12:00 (Same Day: 0,0 antes del mediodía, en 853 pedidos, y 1,0 desde el mediodía, en 742). En el entrenamiento, la región en Same Day parecía mover la tasa (chi cuadrado, p = 0,0025) y con la hora controlada no la mueve (p = 1).

**Intervalos** (D18). Remuestreo pareado de los pedidos de la prueba con reposición, 1.000 remuestras, semilla 0, los mismos índices para los dos puntajes; percentiles 2,5 y 97,5 de la diferencia.

**Lanzamiento congelado y sello** (D17, D54, D62). El lanzamiento entrena, calibra, elige el umbral, guarda el artefacto con su SHA-256 y escribe la parte del lanzamiento del sello: configuración, umbral, huellas de la configuración (`4fef2b2e…62fa`), de los puntajes de V2 y del artefacto (`91f953c5…fa1a`). Las corridas siguientes cargan ese artefacto solo si su huella coincide, antes de deserializarlo (D30), y puntúan cada pedido nuevo una sola vez: los puntajes y el registro de modelos son de solo inserción. La primera evaluación de la prueba, en la corrida de M2017-10, escribió la parte de la prueba del sello; las siguientes dieron `match` y no la recalcularon. Una configuración distinta de la sellada se informa como posterior al sello y no reemplaza el resultado sellado (M05).

## Calibración

Regresión isotónica ajustada solo con V1 sobre el clasificador ya entrenado y congelado (`FrozenEstimator`, D13); cambiar las etiquetas de V2 o de la prueba no cambia el calibrador (AC18). El error de calibración esperado, con 10 intervalos de igual ancho, es 0,011 en V2 y 0,016 en la prueba, con un sesgo de −0,006 (C06); la tabla por intervalo está en `fact_calibration`.

## Umbral y costos supuestos

**Beneficio neto** (D14). Por pedido marcado, `is_late × late_cost × effect − alert_cost`; por pedido no marcado, 0. Con los costos supuestos, una alerta cuesta 5 u., un retraso 20 u. y la alerta evita la mitad del retraso (`effect = 0,5`): el punto de equilibrio es 0,5 y `BN = Σ_marcados (10 × is_late − 5)`. Las unidades son genéricas: lo que decide el umbral es el cociente. Ninguna fuente pública da un costo defendible de un pedido tarde ni de una alerta (`research.md`); el dueño no eligió otros costos antes de U25 (plan, sección 26, pendiente 1), y después del sello un cambio es un análisis nuevo.

**Umbral.** El valor de la grilla de 0,01 a 0,99 que maximiza el beneficio neto en V2, nunca F1; en un empate, el mayor. El del modelo es 0,66: en V2, 1.504,4 u. por cada 1.000 pedidos con el 37,7 % marcado. B2 y B5 eligen 0,80 con el mismo procedimiento (en C04 se usa además B2 a 0,5, la regla de la investigación).

**Sensibilidad.** Para cada punto de equilibrio de 0,10 a 0,90, de a 0,05, se repite la elección del umbral en V2 para el modelo, B2 y B5, y se mide en la prueba (`fact_cost_sensitivity`, C09).

## Criterios y resultados

Prueba: 14.527 pedidos no cancelados del 2017-01-08 al 2017-09-30, con los resultados conocidos al 2017-10-07 y una tasa de retraso de 0,5699. «Visto» dice si la investigación (en líneas, en la prueba, sin la hora del pedido) o el agente de planificación (solo en el entrenamiento) habían mirado ese número antes de fijar el criterio.

| ID | Umbral | Visto por la investigación | Visto por el planificador | Veredicto |
| --- | --- | --- | --- | --- |
| C01 | AUC(modelo) > AUC(B2) | sí | en parte | El modelo supera a la regla por modo: AUC 0,773 frente a 0,742. |
| C02 | Límite inferior del intervalo del 95 % de AUC(modelo) − AUC(B2) > 0 | no | no | El modelo supera a la regla por modo fuera del ruido: la diferencia de AUC es 0,031, con un intervalo del 95 % de 0,026 a 0,036. |
| C03 | Límite inferior del intervalo del 95 % de AUC(modelo) − AUC(B5) > 0 | no | no | El modelo no supera a la regla de dos variables: la diferencia de AUC es −0,001, con un intervalo del 95 % de −0,006 a 0,005. |
| C04 | BN(modelo, t*) > BN(B2 a 0,5) | en parte | no | El modelo supera a la regla de la investigación (B2 a 0,5) en beneficio neto: 1.459,4 frente a 1.323,1 u. por cada 1.000 pedidos. |
| C05 | BN(modelo, t*) > BN(B5, t*_B5), los dos umbrales elegidos en V2 | no | no | El modelo no supera a la regla de dos variables en beneficio neto con su propio umbral (0,80): 1.459,4 frente a 1.467,3 u. por cada 1.000 pedidos. |
| C06 | ECE ≤ 0,03 y \|media de p − tasa observada\| ≤ 0,02 | no | no | El modelo supera el control de calibración: ECE 0,016 frente a un máximo de 0,03 y sesgo −0,006 frente a un máximo de 0,02 en valor absoluto. |
| C07 | Brier(modelo) < Brier(B2) | en parte | no | El modelo supera a la regla por modo en Brier: 0,1767 frente a 0,1890 (menor es mejor). |
| C08 | Con k alertas, pedidos tarde entre los k de mayor riesgo del modelo > pedidos tarde entre los k de B2 | en parte | no | Con el mismo número de alertas que B2 a 0,5 (k = 4.998), el modelo supera a B2: 4.499 pedidos tarde frente a 4.421. |
| C09 | En cada punto de equilibrio de 0,10 a 0,90, de a 0,05, con los umbrales elegidos en V2: BN(modelo) ≥ BN(B2) | no | no | El modelo supera o iguala a B2 en beneficio neto en los 17 puntos de equilibrio (0,10 a 0,90). |
| C10 | Sin umbral: AUC, BN, % marcado, precisión y recall del modelo, B2 y B5; el criterio es que esté escrito | sí | no | Informativo, sin umbral: en el tramo posterior al cambio de forma (8.043 pedidos) el AUC del modelo es 0,779, el de B2 0,745 y el de B5 0,777; el modelo marca el 37,7 % de los pedidos con precisión 0,898 y recall 0,588. |

**Veredicto: el modelo supera 7 de los 9 criterios con umbral y no supera C03 ni C05.**

Todos los métodos en la prueba (cada umbral, el elegido en V2; las reglas sin probabilidad no tienen Brier ni ECE):

| Método | AUC | PR-AUC | Brier | ECE | Umbral | Marcados | Precisión | Recall | BN total (u.) | BN por 1.000 (u.) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Modelo | 0,7731 | 0,8222 | 0,1767 | 0,0156 | 0,66 | 37,1 % | 0,894 | 0,581 | 21.200 | 1.459,4 |
| B0 todo tarde | 0,5000 | 0,5699 | — | — | 0,50 | 100,0 % | 0,570 | 1,000 | 10.155 | 699,0 |
| B1 solo First Class | 0,6323 | 0,6837 | — | — | 0,50 | 15,1 % | 1,000 | 0,265 | 10.955 | 754,1 |
| B2 tasa por modo | 0,7420 | 0,7819 | 0,1890 | 0,0025 | 0,50 | 34,4 % | 0,885 | 0,534 | 19.220 | 1.323,1 |
| B3 días programados de dos o menos | 0,7160 | 0,7251 | — | — | 0,50 | 39,9 % | 0,835 | 0,585 | 19.430 | 1.337,5 |
| B4 solo Standard Class (control negativo) | 0,2840 | 0,4968 | — | — | 0,50 | 60,1 % | 0,394 | 0,415 | −9.275 | −638,5 |
| B5 tasa por modo y franja | 0,7737 | 0,8215 | 0,1751 | 0,0059 | 0,80 | 37,3 % | 0,893 | 0,585 | 21.315 | 1.467,3 |

**Lo que dicen los números, incluido lo que no favorece al modelo.**

- **Contra la regla por modo, el modelo gana en todo.** AUC 0,773 frente a 0,742 fuera del ruido, mejor Brier, más beneficio neto con su umbral y más pedidos tarde con las mismas alertas, y nunca menos beneficio que B2 en los 17 puntos de equilibrio. La ganancia viene de Same Day: B2 le da a todo el modo la misma tasa, y el modelo aprende el corte del mediodía.
- **Contra la regla de dos variables, no gana: el modelo es esa regla.** B5 tiene un AUC apenas mayor (0,7737 frente a 0,7731) y un beneficio neto apenas mayor (1.467,3 frente a 1.459,4 u. por cada 1.000 pedidos); ni la diferencia de AUC ni la de beneficio favorecen al modelo. En V2, con su umbral, el modelo marca la misma parte de los pedidos que B5 (37,7 %) con el mismo beneficio neto (1.504,4). Fuera del modo y de la hora no hay señal: ninguna otra variable mueve el AUC en V2 más de 0,0012. Es lo que el plan escribió antes de medir (D16).
- **Tampoco gana a la regla de los días programados en beneficio neto, aunque eso no era un criterio.** B3 (marcar los pedidos con dos días programados o menos) da 1.337,5 u. por cada 1.000 pedidos, más que B2 a 0,5 (1.323,1) y menos que el modelo y que B5.
- **El tramo posterior al cambio de forma repite el orden** (C10): modelo 0,779, B5 0,777, B2 0,745 de AUC, y en beneficio neto B5 1.505,7, modelo 1.501,3 y B2 1.362,1 u. por cada 1.000 pedidos.
- **El seguimiento confirma la calibración en uso.** De enero de 2017 a enero de 2018 se puntuaron 23.600 pedidos y 8.840 tuvieron alerta; entre los pedidos que cerraron, el modelo esperaba 12.800 retrasos y hubo 12.915, y de las 8.421 alertas cerradas llegaron tarde 7.538 (`fact_tracking_monthly`).

## Rendimiento

Medido en esta máquina (AMD Ryzen 9 5950X de 16 núcleos, 64 GB, Windows 10, Python 3.11, un solo proceso) en la corrida real, con el archivo ya descargado: `python -m latedel replay` y a continuación `python -m latedel run` (D81). Los tiempos por tarea son los que registra el propio flujo en el archivo de estado de cada corrida.

| Tarea | Lanzamiento (s) | Mediana de las 13 mensuales (s) | Mensuales, del mínimo al máximo (s) | Cierre (s) |
| --- | ---: | ---: | ---: | ---: |
| `fetch_source` | 5,69 | 0,07 | 0,07 a 0,08 | 0,07 |
| `deliver` | 0,31 | 0,12 | 0,12 a 0,12 | 0,11 |
| `validate` | 0,13 | 0,15 | 0,13 a 0,17 | 0,17 |
| `load` | 1,21 | 1,05 | 0,84 a 1,22 | 1,16 |
| `fit_model` | 20,11 | 0,02 | 0,02 a 0,02 | 0,02 |
| `score` | 0,00 | 0,08 | 0,08 a 0,10 | 0,03 |
| `track` | 0,03 | 0,09 | 0,07 a 0,12 | 0,11 |
| `evaluate` | 0,68 | 0,79 | 0,67 a 2,20 | 2,00 |
| `export` | 1,41 | 1,78 | 1,45 a 2,25 | 2,23 |
| `render_dashboard` | 0,17 | 0,24 | 0,20 a 0,28 | 0,27 |
| `report` | 0,24 | 0,31 | 0,27 a 0,35 | 0,35 |
| **Corrida, sumando las tareas** | **29,98** | | de 3,95 a 6,56 | **6,52** |

La evaluación de la prueba corre en M2017-10 (la mensual de 2,20 s en `evaluate`) y la del tramo posterior en el cierre. La corrida sin entrega solo corre `fetch_source` (0,07 s) y `deliver` (0,43 s).

- **RNF02.** Repetición completa (las quince entregas y la corrida sin entrega): 105,14 s con el reloj del shell (102,66 s el `replay` y 2,48 s la corrida sin entrega) contra el tope de 10 minutos; el lanzamiento, 30 s contra 3 minutos; cada corrida mensual, de 3 a 7 s contra 60 s; el cierre, 6 s. La prueba de Docker (construir, inicializar, `pip check` y dos `airflow dags test` en modo rápido), 71,6 s en la última vez que corrió (D80), contra 10 minutos. **Se cumplen.** La repetición completa en modo rápido no se midió con reloj; la prueba que la corre, `tests/e2e/test_quick_mode.py`, es parte de la suite rápida.
- **RNF03.** El contador con tope es el pico del conjunto de trabajo del proceso, `PeakWorkingSetSize` de `GetProcessMemoryInfo` (en Linux sería `ru_maxrss` de `resource.getrusage`): **921,6 MB** en el proceso del `replay`, contra el tope de 1,5 GB (1.536 MB), y 373,0 MB en el de la corrida sin entrega. **Se cumple.** Cada tarea de cada corrida registró su pico y el nombre del contador. El pico de memoria comprometida del mismo proceso (`PeakPagefileUsage`), que se guarda como cifra informativa y sin tope, llegó a 2.824,1 MB en el `replay` y a 1.952,1 MB en la corrida sin entrega: el triple del conjunto de trabajo, como en la medición de `requirements.md`, sección 4. La cifra de memoria de la fila de cada corrida tiene una limitación (ver abajo).
- **Tamaños.** Los 20 Parquet de `outputs/powerbi/` suman 2.229.845 bytes; el mayor, `fact_order.parquet`, 2.127.541 bytes, contra el tope de 25 MB por archivo. Los cinco archivos del sitio suman 61.727 bytes contra 204.800 (62.742 con el manifiesto). El HTML del diseño, 130.972 bytes; el sello, 4.202.
- **AC36, la suite rápida** (sin `real_data` ni `docker`): **142,55 s** para 1.257 pruebas (una se salta en Windows: la del modo 0o600 del sello, que allí no es un control de acceso), medida al terminar U26, contra el tope de 150 s. **Se cumple**, con poco margen: tras U25 fueron 139,92 s para 1.237 y tras H9 142,78 s para 1.254. Las pruebas con el archivo real (`-m real_data`): 29 de 29 en 180,74 s.

## Limitaciones

- **Los datos parecen generados.** Dentro de Second y Standard Class los días reales van de 2 a 6 casi por igual, First Class tarda siempre 2 días, y en Same Day la hora del pedido decide el resultado sin una sola excepción (los 853 creados antes del mediodía llegaron a tiempo y los 742 creados desde el mediodía, tarde). Dentro de Second y Standard Class ninguna otra columna mostró señal (chi cuadrado por modo en el entrenamiento, p ≥ 0,05), y en Same Day la región dejó de mostrarla con la hora controlada. Son rasgos de una simulación más que de una operación real: el resultado dice lo que pasa en este archivo, no en una empresa. Si el sitio debe decirlo es una decisión pendiente del dueño (plan, sección 26, pendiente 5).
- **El corte del mediodía es casi toda la señal.** El modelo no aprende nada que una regla de dos variables no tenga (C03 y C05); en datos con otra mecánica podría ser distinto, y este archivo no lo permite comprobar.
- **Costos supuestos.** Alerta 5, retraso 20 y efecto 0,5 son supuestos declarados, no medidos; el umbral y el beneficio neto dependen de ellos. La sensibilidad muestra el efecto de otros puntos de equilibrio, pero no reemplaza un costo real. Elegir otros es una decisión del dueño, y después del sello es un análisis nuevo que se informa aparte.
- **Pedidos cancelados.** Se puntúan como cualquier pedido nuevo (1.030 de los puntuados se cancelaron después, con 419 alertas sobre ellos) pero no tienen etiqueta: el modelo no dice nada sobre una cancelación.
- **El cambio de forma de 2017-10.** Los pedidos de una sola línea del último tramo se evalúan aparte y sin umbral (C10); el orden de los métodos se mantiene, pero cuatro meses no alcanzan para decir que el modelo sirve en esa forma de los datos.
- **Una sola evaluación.** La prueba se evaluó una vez, con un lanzamiento, una partición y un archivo; no hay otra ventana ni otro conjunto con que comparar.
- **Rentabilidad excluida por precaución.** Las tres columnas de rentabilidad podrían conocerse al crear el pedido; como no consta cuándo se calculan, quedan fuera, y el modelo no las prueba.
- **Sin API.** La función de puntuación no está publicada como servicio; una API con FastAPI queda a decisión del dueño (D33).
- **La fila de una corrida y su memoria comprometida** (D83). La fila de `pipeline_runs`, y con ella `peak_commit_mb` y `peak_working_set_mb` de `dim_pipeline_run`, toma los picos en el primer paso del cierre, antes de que la tarea `report` mida los suyos al terminar, así que puede quedar un poco por debajo: en el lanzamiento, 2.128,3 MB de `PeakPagefileUsage` en la fila y 2.145,8 MB en `report`; el `PeakWorkingSetSize`, 594,1 MB en los dos. No cambia ningún tope (el del requisito es el conjunto de trabajo, y se comprueba también por tarea) y no se corrige, porque cambiaría cómo se calcula una columna de una salida ya sellada.
- **Un escritor a la vez, por proceso** (D82). El bloqueo de la carpeta de datos rechaza un segundo proceso mientras otro escribe; entre dos tareas de una corrida de Airflow, que son procesos distintos, nadie lo tiene, y por eso `airflow dags test` se corre con el planificador detenido.
- **Las medidas DAX no se ejecutaron.** El diseño del tablero y sus 31 medidas se verificaron con pruebas estáticas contra el modelo, pero nunca corrieron en Power BI Desktop, y el `.pbix` no está hecho.
- **Sin evaluación independiente todavía.** Ninguna cifra de esta ficha la recalculó alguien que no construyó el proyecto (plan, sección 25).

## Reproducibilidad

Versiones fijadas con `==` en `requirements.txt` (Windows con Python 3.11 y la imagen `apache/airflow:3.4.0` con Python 3.13.18), semillas fijas (el clasificador 0, el remuestreo 0) y ajustes sin parada temprana. Repetir el lanzamiento desde cero en otro almacén da la misma huella de la configuración, los mismos puntajes y los mismos bytes del artefacto (D54), y dos repeticiones completas seguidas dan el mismo contenido en cada Parquet y JSON, salvo las columnas de la corrida (AC34, una prueba sobre el accesorio). La huella de la configuración del sello es `4fef2b2e…62fa`; con ella, una repetición de la corrida real da `match` y no reemplaza el resultado sellado. Las salidas versionadas son las de la corrida real del 2026-11-11.
