# Requerimientos: pedidos que van a llegar tarde, antes de que lleguen

Fecha: 2026-11-10. Base: `docs/research.md` y los perfilados del agente de planificación sobre el archivo real (sección 4), hechos con Python 3.11 en una venv de pruebas, con `python -I` y los datos en una carpeta aparte. Las suposiciones se marcan como **[Supuesto]**; lo que hay que comprobar al programar, como **[Verificar]**. Las decisiones citadas (Dxx) están en `decisions.md`; el diseño, en `plan.md`.

## 1. Problema

Una empresa que vende en línea y despacha a todo el mundo promete una fecha de entrega en cada pedido, pero la demora aparece recién cuando ya pasó: el cliente reclama y nadie lo había visto venir. No hay una lista de los pedidos que conviene mirar antes.

Quién lo sufre:

- **Logística**: no sabe qué pedidos revisar, adelantar o cambiar de modo de envío, y revisa todos o ninguno.
- **Atención al cliente**: recibe el reclamo sin aviso, cuando avisar antes con una fecha realista habría costado menos.
- **Dirección**: no sabe cuánto cuesta una alerta frente a cuánto cuesta un retraso, ni si un modelo agrega algo a una regla que cualquiera puede escribir.

Qué cuesta hoy:

- El 57,3 % de las líneas de pedido no canceladas llega tarde (research.md). **[Supuesto]**: los datos no traen costos de retraso ni de intervención; el costo se declara, no se mide (D14).
- Los trabajos publicados sobre este dataset informan AUC de hasta 0,89 con columnas que solo se conocen después del envío (research.md, «Trabajos previos»). Un equipo que copiara ese modelo pondría en uso un clasificador que «sabe» la respuesta recién cuando el pedido ya llegó.

## 2. Objetivo

Un flujo mensual que carga cada entrega de pedidos nuevos y de resultados ya conocidos, la valida, descarta los datos personales en la entrada, calcula para cada pedido nuevo, con lo que se sabe al crearlo, la probabilidad calibrada de que llegue tarde, marca los que superan un umbral elegido por costo, sigue cada mes cómo resultaron las alertas ya cerradas y deja las tablas de un tablero de Power BI en modelo estrella, el diseño del tablero de alertas en HTML y una exportación pequeña para el sitio. El modelo se compara contra reglas simples (la tasa de retraso por modo de envío y una regla de dos variables) con criterios fijados antes de construir (sección 8.1), y la prueba se evalúa una sola vez, bajo un sello (D17).

## 3. Alcance

Incluido:

- Fuente: `DataCoSupplyChainDataset.csv` de Mendeley Data, versión 5 (DOI 10.17632/8gx2fvg2k6.5, CC BY 4.0), un único archivo CSV; pedidos del 2015-01-01 al 2018-01-31 (D02).
- Momento de la predicción: al crear el pedido (D03). Unidad: el pedido, no la línea (D04).
- Refresco mensual sobre un archivo que no cambia (D09): un lanzamiento con lo conocido al 2017-01-07, trece entregas mensuales (2017-01 a 2018-01) y un cierre al 2018-02-07 que solo trae resultados. Cada entrega trae dos flujos: los pedidos creados en el período y los resultados que se conocieron en el período.
- Partición en el tiempo con purgas de 7 días; validación partida en V1 y V2; el tramo posterior al cambio de forma de los datos (desde 2017-10) se informa aparte (D08).
- Modelo: `HistGradientBoostingClassifier` de scikit-learn, tres conjuntos de variables y una grilla fija de ocho configuraciones, elegido y calibrado en V1, con el umbral elegido en V2 (D11-D14).
- Líneas base B0-B5 (D15); criterios C01-C10 (D16); intervalos por remuestreo pareado (D18).
- Umbral por costo con costos supuestos y declarados y un análisis de sensibilidad en un rango declarado (D14).
- Modelo congelado en el lanzamiento y sello del modelo y de la prueba (D17).
- Orquestación en Apache Airflow sobre Docker Compose y una CLI equivalente para Windows sin Airflow (D23).
- Tablas para Power BI en Parquet, diseño del tablero de alertas en HTML generado desde una especificación, exportación para el sitio en JSON, documentación completa con la estructura del proyecto 01 (S10).

Fuera de alcance:

- Predecir al despachar: la columna de despacho ya contiene la demora; el dataset no permite validar ese momento (D03).
- Costos reales de un retraso o de una intervención: no hay una fuente defendible (research.md); se declaran supuestos (D14).
- Reentrenar dentro del flujo mensual: el modelo se entrena una vez, en el lanzamiento, y se congela (D17). El runbook dice cómo sería una versión 2.
- Los pedidos cancelados como clase propia: quedan fuera de la tarea, aunque se puntúan como cualquier pedido nuevo y se cuentan (D05).
- Una API de puntuación en tiempo real (FastAPI): la función de puntuación queda lista para envolverla, sin servicio (D33).
- El archivo de clickstream `tokenized_access_logs.csv`: trae señales posteriores al pedido (research.md).
- Construir el `.pbix` en Power BI Desktop (interfaz gráfica; S4) y publicarlo.

## 4. Datos medidos

Fuente (research.md, comprobado en Mendeley): `https://data.mendeley.com/public-files/datasets/8gx2fvg2k6/files/72784be5-36d3-44fe-b75d-0edbf1999f65/file_downloaded`, que responde 302 a un enlace firmado de Amazon S3; 95.910.149 bytes; SHA-256 `fa6d022ed437155e1a2f0378710602848703c8a7f203f7ff5d77805bf8480aa6` (el mismo que publica la API de archivos de Mendeley); MD5 igual al `ETag` de S3; cp1252 (UTF-8 falla en el byte 1.709), CRLF, sin BOM ni bytes nulos; 180.519 filas de datos y 53 columnas, todas con 53 campos; 0 filas duplicadas; fechas con el formato `%m/%d/%Y %H:%M`, sin zona horaria.

Perfilado del agente de planificación (scripts fuera del repositorio; solo conteos sobre validación y prueba, ninguna métrica de modelo fuera del entrenamiento):

| Medida | Valor |
| --- | --- |
| Pedidos | 65.752 |
| Columnas con un solo valor dentro de cada pedido | 20 de 20 revisadas: modo de envío, tipo de pago, mercado, región, país, ciudad y estado del pedido, segmento, país e identificador del cliente, fechas de pedido y de envío, días programados y reales, etiqueta, `Delivery Status`, `Order Status`, latitud, longitud y código postal del pedido: 0 pedidos con dos valores |
| Columnas que cambian dentro del pedido | producto, categoría, departamento, tasa de descuento y las tres de rentabilidad (varían por línea pese a su nombre) |
| Líneas por pedido antes de 2017-10 | 1 a 5, casi uniforme: entre 11.337 y 11.691 pedidos con cada número |
| Líneas por pedido desde 2017-10 | 8.340 de 8.403 pedidos con una línea |
| Pedidos cancelados (`Shipping canceled`) | 2.855 pedidos completos (7.754 líneas); ningún pedido cancelado en parte |
| Envío menos pedido | exactamente 12 h en Same Day, 48 h en First Class y de 48 a 144 h en pasos de 24 h en Second y Standard Class |
| Días reales | iguales a la diferencia en días de calendario entre el envío y el pedido en el 100 % de los pedidos del entrenamiento; el 97,2 % de research.md es el redondeo hacia arriba, que falla solo en los Same Day de 0 días |
| Resultado conocido | a más tardar 6 días después de la fecha del pedido (`max(fecha del pedido + días reales, fecha del envío)`) |

Bloques de la partición (pedidos y líneas no cancelados; D08):

| Bloque | Pedidos del | al | Pedidos | Líneas | Pedidos cancelados (fuera) |
| --- | --- | --- | ---: | ---: | ---: |
| Entrenamiento | 2015-01-01 | 2016-06-30 | 29.894 | 89.531 | 1.355 |
| Purga 1 | 2016-07-01 | 2016-07-07 | 375 | 1.150 | 22 |
| Validación V1 | 2016-07-08 | 2016-09-30 | 4.649 | — | — |
| Validación V2 | 2016-10-01 | 2016-12-31 | 5.032 | — | — |
| Validación (V1 + V2) | | | 9.681 | 29.199 | 436 |
| Purga 2 | 2017-01-01 | 2017-01-07 | 377 | 1.094 | 12 |
| Prueba | 2017-01-08 | 2017-09-30 | 14.527 | 43.598 | 670 |
| Posterior al cambio de forma | 2017-10-01 | 2018-01-31 | 8.043 | 8.193 | 360 |
| Total | | | 62.897 | 172.765 | 2.855 |

Mecánica de la etiqueta, solo en el bloque de entrenamiento (pedidos):

| Modo | Pedidos | Días programados | Días reales | Tasa de retraso |
| --- | ---: | ---: | --- | ---: |
| First Class | 4.541 | 1 | siempre 2 | 1,000 |
| Second Class | 5.885 | 2 | 2 a 6, casi uniforme | 0,8017 |
| Same Day | 1.595 | 0 | 0 (853 pedidos) o 1 (742) | 0,4652 |
| Standard Class | 17.873 | 4 | 2 a 6, casi uniforme | 0,4013 |
| Todos | 29.894 | | | 0,5745 |

- **El corte del mediodía en Same Day**: en el entrenamiento, los 853 pedidos Same Day creados antes de las 12:00 llegaron a tiempo y los 742 creados desde las 12:00 llegaron tarde, sin una excepción. Con 12 h entre el pedido y el envío, el envío cruza la medianoche si el pedido entra desde el mediodía. Es una variable que existe al crear el pedido (D10). research.md no la vio porque su modelo no tenía la hora del pedido.
- **Dentro de cada modo, nada más se mueve**: con pruebas de chi cuadrado por modo en el entrenamiento, ni el mercado, ni la región, ni el tipo de pago, ni el segmento, ni el número de líneas, ni el departamento, ni el día de la semana, ni el mes, ni el monto del pedido tienen p < 0,05 en Second y Standard Class. En Same Day, la región da p = 0,002 (14 regiones, tasas de 0,298 a 0,585) **[Verificar en U11 si desaparece al controlar por la hora]**.
- **AUC dentro del entrenamiento** (ajuste y medida sobre los mismos pedidos, solo como referencia): la tasa por modo 0,740; la tasa por modo con el corte del mediodía 0,773. Es lo que vio el agente de planificación antes de fijar los criterios (D16).
- **Rentabilidad**: dentro de cada modo, las líneas tarde tienen un beneficio medio algo menor (Same Day 18,36 frente a 21,22; Standard Class 20,18 frente a 21,80), con la misma proporción de líneas con beneficio negativo (18,84 % frente a 18,75 %). No alcanza para fijar cuándo se calcula; se excluye por precaución (D06).

Rendimiento medido en este equipo (Ryzen 9 5950X, 64 GB, Windows 10, pandas 3.1.1 de la venv de pruebas; el proyecto fija pandas 2.3.3 **[Supuesto: tiempos parecidos, se miden en U25]**): lectura del CSV completo con `dtype=str` 1,10 s; agregación por pedido del entrenamiento 0,36 s; un ajuste de `HistGradientBoostingClassifier` (300 iteraciones, 29.894 pedidos, 14 columnas) 0,72 s; ese script llegó a un pico del conjunto de trabajo (`PeakWorkingSetSize`) de 471 MB y a un pico de memoria comprometida (`PeakPagefileUsage`) de 1.999 MB. `GetProcessMemoryInfo` por `ctypes` necesita declarar `argtypes` en Windows de 64 bits: sin ellos, el seudoidentificador del proceso da `OverflowError` (medido).

Entorno medido: Python 3.11.1 en Windows (el único instalado); la imagen `apache/airflow:3.4.0` trae Python 3.13.18 (proyectos 01 y 02); scikit-learn 1.9.1 trae `sklearn.frozen.FrozenEstimator` (comprobado).

## 5. Requerimientos funcionales

- **RF01 Fuente íntegra.** La tarea `fetch_source` descarga el CSV por HTTPS solo si no está en `data/raw/`, a un `.part` con SHA-256 calculado al vuelo, y lo renombra con `os.replace` solo si la huella coincide con `source.accepted_sha256` (modo `http`). En modo `local`, la primera huella vista queda registrada en el manifiesto y las siguientes deben coincidir. Una huella distinta rechaza la corrida sin tocar nada (V01).
- **RF02 Descarga segura.** Cada salto, incluidas las redirecciones, es HTTPS y va a un host de `source.allowed_hosts`; a lo sumo `source.max_redirects` redirecciones; el cuerpo no supera `source.max_bytes`. Cualquier otra cosa corta la descarga antes de escribir el archivo final (V02).
- **RF03 Lectura segura.** El archivo se trata como no confiable: decodificación cp1252 estricta, sin bytes nulos ni BOM, encabezado con las 53 columnas exactas en orden, 53 campos en cada fila, tipos convertibles al 100 % (V03-V05).
- **RF04 Minimización en la entrada.** Las 15 columnas personales (nombres, correo, contraseña, calle, códigos postales, ciudades y estados, identificadores del cliente, latitud y longitud) se usan solo para las comprobaciones de la lectura y se descartan antes de escribir cualquier archivo intermedio; nunca llegan a SQLite ni a ninguna salida (D07). Un correo o una contraseña sin enmascarar rechaza la fuente (V06).
- **RF05 Lectura canónica en dos flujos.** Se escriben `data/interim/orders.parquet` (una fila por pedido, solo columnas que se conocen al crearlo), `data/interim/order_lines.parquet` (una fila por línea, solo columnas del momento del pedido) y `data/interim/outcomes.parquet` (una fila por pedido con el resultado: estado de entrega, estado del pedido, días reales, fecha del envío, etiqueta, rentabilidad y la fecha en que se conoce el resultado). Las comprobaciones de la fuente (V07-V09) corren antes de escribirlos.
- **RF06 Entregas.** La tarea `deliver` publica la siguiente entrega pendiente en `data/raw/deliveries/<delivery_id>/` con sus dos archivos (pedidos y resultados) y su manifiesto (período, filas, SHA-256): el lanzamiento `L2017-01-07`, las mensuales `M2017-01` a `M2018-01` y el cierre `C2018-02`. Un pedido pertenece a la entrega de su fecha; un resultado, a la de su fecha de conocimiento. Sin entregas pendientes, la corrida termina como `no_delivery` y corta el flujo.
- **RF07 Validación de la entrega.** Reglas V20-V24, M01-M05 e I01-I05 del plan, con filas y pedidos afectados; reporte JSON y Markdown por corrida.
- **RF08 Rechazo sin daño.** Con una regla bloqueante, la entrega pasa a cuarentena, la corrida termina como `rejected` y el almacén, los Parquet, el HTML, la exportación del sitio y el sello quedan como estaban, byte a byte.
- **RF09 Carga.** Pedidos, líneas y resultados de la entrega se agregan a SQLite en una transacción; `staging` y los marts se reconstruyen con archivos SQL versionados y controles en SQL. Un control con filas deshace todo.
- **RF10 Etiqueta.** Un pedido no cancelado llega tarde si sus días reales superan a los programados (`is_late`). La etiqueta existe en el almacén recién cuando llega su resultado; los pedidos cancelados no tienen etiqueta de la tarea (D05).
- **RF11 Variables solo del momento del pedido.** Las variables salen de una lista permitida de columnas del pedido y de sus líneas, agregadas por pedido, más la hora, el día de la semana, el día del mes y el mes de su creación; ninguna columna de la lista de fuga puede llegar a la matriz (D06, D11).
- **RF12 Partición en el tiempo.** Los bloques de la sección 4, por fecha del pedido, con purgas de 7 días; V1 y V2 dentro de la validación; el tramo posterior al cambio de forma, aparte (D08).
- **RF13 Líneas base.** B0 (todo tarde), B1 (solo First Class), B2 (tasa de entrenamiento por modo, marca si es ≥ 0,5), B3 (días programados ≤ 2), B4 (control negativo: solo Standard Class) y B5 (tasa de entrenamiento por modo y franja del pedido, antes o desde las 12:00), todas con tasas calculadas solo en el entrenamiento (D15).
- **RF14 Modelo.** Se ajustan en el entrenamiento los tres conjuntos de variables por la grilla fija; se elige el de menor pérdida logarítmica en V1; se calibra con regresión isotónica en V1; el artefacto se guarda con su SHA-256 y solo se carga si la huella coincide (D11-D13, D30).
- **RF15 Umbral por costo.** Con los costos supuestos de la configuración, el umbral es el que maximiza el beneficio neto en V2, en una grilla de 0,01 a 0,99; nunca F1. Para cada punto de equilibrio del rango declarado (0,10 a 0,90, de a 0,05) se repite la elección para el modelo, B2 y B5 (D14).
- **RF16 Lanzamiento congelado y sello.** El lanzamiento entrena, calibra, elige el umbral, registra el artefacto y escribe la parte del lanzamiento del sello (`reports/model_seal.json`); las corridas siguientes reusan ese artefacto sin cambios (D17).
- **RF17 Puntuación mensual.** Cada corrida puntúa una vez cada pedido nuevo de su entrega (también los que después se cancelan) con el modelo congelado y guarda la probabilidad, la marca, el umbral, la versión del modelo y la fecha de cálculo. Un puntaje nunca cambia en las corridas siguientes.
- **RF18 Seguimiento.** Cada corrida cierra los resultados que llegaron y calcula, por mes del pedido y por modo, las alertas, las alertas cerradas, cuántas llegaron tarde, cuántos pedidos tarde quedaron sin alerta y los retrasos esperados frente a los observados.
- **RF19 Evaluación una sola vez.** Cuando la fecha de cálculo alcanza el fin de la prueba más la purga (2017-10-07), se calculan los criterios C01-C10 en la prueba y se escriben en el sello; en el cierre, el tramo posterior al cambio de forma. Una evaluación con otra configuración que la sellada no reemplaza el resultado sellado: se informa aparte como posterior al sello (D17).
- **RF20 Resumen honesto.** `reports/evaluation_summary.json` y `.md` dicen el veredicto de cada criterio según su número, incluido «no supera»; el bloque de resultados del README se copia de ese resumen entre marcadores.
- **RF21 Modelo estrella.** Se exportan a `outputs/powerbi/` las dimensiones y los hechos del plan en Parquet, con claves únicas e integridad referencial.
- **RF22 Diseño del tablero.** Se genera `dashboard/powerbi-design.html` desde una especificación del modelo semántico: tablas, relaciones, medidas DAX, carga en Power Query y cinco páginas (alertas, dónde se traban, seguimiento, validación del modelo, calidad y corridas) con visuales ubicados y vistas previas con datos reales.
- **RF23 Exportación para el sitio.** Se escriben en `outputs/site/` cinco JSON pequeños y versionados (resumen, reglas de validación, fuga, algoritmos y alertas) con el contrato de `plan.md`, sección 18, generados de las salidas reales, sin datos personales ni identificadores y con un tamaño mínimo de celda.
- **RF24 Orquestación.** Un DAG de Airflow (`latedel_monthly`) en Docker Compose y una CLI (`python -m latedel run` y `replay`) ejecutan la misma lista de tareas, en el mismo orden, con las mismas funciones.
- **RF25 Documentación.** README con la narrativa del proyecto 01 (problema, solución, resultados, recorrido, cómo correrlo, paso a paso), diccionario de datos, ficha del modelo, registro de decisiones, runbook, paso a paso, advertencias de las revisiones y evaluación independiente, con figuras generadas.

## 6. Requerimientos no funcionales

- **RNF01 Reproducibilidad.** Versiones fijadas con `==` (D28), semillas fijas, ajustes sin parada temprana. Dos repeticiones completas sobre la misma fuente dan Parquet y JSON con el mismo contenido, salvo las columnas de la corrida.
- **RNF02 Rendimiento** (este equipo, medido en U25). Repetición completa (fuente ya descargada, 15 entregas y la corrida sin entrega) ≤ 10 min; corrida de lanzamiento ≤ 3 min; corrida mensual ≤ 60 s; repetición completa en modo rápido ≤ 2 min; suite por defecto (sin `real_data` ni `docker`) ≤ 150 s; prueba de Docker ≤ 10 min. Estimación con lo medido: 24 ajustes de unos 0,7 s, la calibración, la sensibilidad y la demostración de fuga en el lanzamiento; unos 10 a 20 s por corrida mensual **[Supuesto]**.
- **RNF03 Memoria, con el contador nombrado.** El pico del conjunto de trabajo del proceso es ≤ 1,5 GB. Se mide, no se estima, con la biblioteca estándar: en Windows, `PeakWorkingSetSize` de `GetProcessMemoryInfo` (por `ctypes`, con `argtypes` declarados); en Linux, `ru_maxrss` de `resource.getrusage(RUSAGE_SELF)` (en KiB). Se guarda por tarea. En Windows se guarda además el pico de memoria comprometida (`PeakPagefileUsage`) como cifra informativa, sin umbral (en la medición de la sección 4 fue cuatro veces el conjunto de trabajo). Toda cifra de memoria que diga la documentación nombra su contador (lección A3 del proyecto 02).
- **RNF04 Portabilidad.** La CLI corre en Windows 10 con Python 3.11; el DAG, en la imagen `apache/airflow:3.4.0` (Python 3.13.18). Toda ruta relativa de la configuración y de la CLI se resuelve contra la carpeta del proyecto, nunca contra el directorio de trabajo (D24).
- **RNF05 Idempotencia y atomicidad.** Repetir una tarea con la misma entrada no cambia el resultado; cada escritura de tablas es transaccional; cada archivo de salida se escribe a un temporal y se renombra con `os.replace`.
- **RNF06 Sin fuga de información.** (a) Las variables de un pedido salen solo de columnas que existen al crearlo; (b) todo lo calculado en una fecha de cálculo usa solo pedidos creados y resultados conocidos hasta esa fecha; (c) toda decisión del modelo (variables, configuración, calibración, umbral, costos) usa solo el entrenamiento y la validación; (d) la prueba se evalúa una vez, con la configuración sellada en el lanzamiento (D17).
- **RNF07 Trazabilidad.** Cada fila de hechos y cada archivo de salida llevan el `run_key`, la fecha de cálculo y la huella de la fuente; los puntajes llevan además la versión del modelo y el umbral. Cada tarea registra inicio, fin, filas, duración, pico de memoria y, si falla, la traza completa.
- **RNF08 Tecnologías.** Solo las del landing (S5) y las ya anotadas en `LANDING-EXPANSION.md`; este proyecto no suma ninguna (D27).
- **RNF09 Licencia.** La atribución CC BY 4.0 con la cita completa («Constante, Fabian; Silva, Fernando; Pereira, António (2019), 'DataCo SMART SUPPLY CHAIN FOR BIG DATA ANALYSIS', Mendeley Data, v5, http://doi.org/10.17632/8gx2fvg2k6.5») aparece en el README, el diseño del tablero, los metadatos de cada Parquet y la exportación del sitio.
- **RNF10 Versionado.** Nunca se versionan la fuente, las entregas, el almacén, los intermedios, el artefacto del modelo ni nada de `data/quick/`; cada Parquet versionado pesa ≤ 25 MB; la exportación del sitio, ≤ 200 KB en total. El sello del modelo y de la prueba (`reports/model_seal.json`) sí se versiona.
- **RNF11 Seguridad.** Descarga: HTTPS en cada salto, lista de hosts permitidos, tope de redirecciones y de bytes, huella fijada (D02). Archivo: sin archivos comprimidos que extraer; lectura con límites (RF03); un correo o una contraseña sin enmascarar rechazan la fuente (V06); todo texto de los datos se escapa en el HTML. Artefacto: el modelo se carga solo si su SHA-256 coincide con el registrado (D30). Compose de Airflow: interfaz solo en `127.0.0.1`; **cada secreto se exige desde el entorno con `${VAR:?}` y sin valor por defecto, incluida la contraseña de la base de metadatos (en el servicio de PostgreSQL y dentro de la cadena de conexión)**; `.env.example` con las claves vacías; `src/`, `sql/`, `config/` y `dags/` montados en solo lectura; imágenes con versión y resumen; un rechazo no se reintenta (D23). Sin credenciales en el proyecto.
- **RNF12 Datos personales.** Ningún nombre, apellido, correo, contraseña, calle, código postal, ciudad o estado del cliente o del pedido, coordenada ni identificador del cliente llega a SQLite, a `data/interim/`, a un Parquet, al HTML, a un JSON, a un reporte, a un registro ni al archivo de estado de una corrida. Los agregados publicados en el sitio y en las vistas previas del HTML ocultan las celdas con menos de 20 pedidos (D07, D22).
- **RNF13 Idioma.** Documentación en español neutro; código, identificadores, tablas y columnas en inglés; medidas, títulos de visuales y textos del sitio en español (D26).
- **RNF14 Honestidad.** La documentación dice solo lo que consta en el registro del documento de seguimiento y en `git log` (lección A8 del proyecto 01), y se sincroniza después de la última corrección: el número de pruebas, los tiempos, las cifras de memoria y las advertencias abiertas que dice coinciden con lo medido en el último commit (lección B3 del proyecto 02).

## 7. Requerimientos de datos

- **Clave del pedido**: `order_id`, entero (`Order Id`). **Clave de la línea**: `line_id`, entero (`Order Item Id`), única.
- **Fechas**: `order_ts`, fecha y hora local del archivo (sin zona; se guardan como texto ISO sin zona); `order_date`, su fecha; `outcome_known_date = max(order_date + días reales, fecha del envío)`; `as_of_date`, fecha de cálculo (fin del día).
- **Etiqueta**: `is_late = días reales > días programados`, solo en pedidos no cancelados.
- **Régimen**: `regime = "pre_break"` para pedidos anteriores al 2017-10-01 y `"post_break"` desde esa fecha.
- **Montos**: los de la fuente (`Sales`, `Order Item Total`, `Order Item Discount`), en la moneda del archivo, que no está declarada **[Supuesto: dólares]**. Los costos del umbral están en unidades de costo genéricas («u.»): lo que decide el umbral es su cociente (D14).
- **Contrato del archivo**: las 53 columnas exactas, en orden; una columna de más, de menos o renombrada es bloqueante (V04).
- **Licencia**: CC BY 4.0, con la cita de RNF09.

## 8. Criterios de aceptación

«Sintético» quiere decir el accesorio generado por las pruebas; «real», el CSV de Mendeley (marca `real_data`).

| ID | Criterio | Umbral | Verificación |
| --- | --- | --- | --- |
| AC01 | Fuente íntegra | Una huella distinta de la aceptada deja el `.part` sin renombrar, rechaza la corrida y no cambia nada; en modo `local`, una huella distinta de la registrada también | Unitaria con un servidor local |
| AC02 | Descarga segura | Una redirección a `http://`, a un host fuera de la lista o una cadena de más de `max_redirects`, y un cuerpo de más de `max_bytes`, dan V02 y no escriben el archivo final | Unitaria con un servidor local |
| AC03 | Lectura segura | Cada variante malformada (columna de más, de menos o renombrada; fila de 52 campos; byte nulo; BOM; byte inválido en cp1252; fecha que no convierte) da su regla (V03-V05) y no escribe nada en `data/interim/` | Contrato |
| AC04 | Datos personales fuera desde la entrada | Ninguna columna personal en `data/interim/*.parquet` ni en SQLite; los valores centinela del accesorio (nombres, calle, códigos postales, coordenadas, identificadores de cliente de un rango propio) no aparecen en ningún byte de `data/interim/`, del almacén, de `outputs/`, `reports/`, `dashboard/`, los archivos de estado ni los registros | Contrato e integración |
| AC05 | Enmascarado | Un correo o una contraseña sin enmascarar en una sola fila dan V06 y no escriben nada | Contrato |
| AC06 | Lectura canónica | Real: 180.519 líneas, 65.752 pedidos, 2.855 pedidos cancelados (7.754 líneas), 62.897 pedidos para la tarea; sintético: los recuentos de la verdad del accesorio | Real y contrato |
| AC07 | Coherencia del pedido | Un pedido con dos modos, dos fechas o dos etiquetas da V07 (bloqueante) | Contrato |
| AC08 | Etiqueta | Real: `Late_delivery_risk` = días reales > programados en el 100 % de los pedidos no cancelados; una discrepancia da V08 (bloqueante); un resultado conocido 7 o más días después del pedido da V09 (bloqueante) | Contrato y real |
| AC09 | Entregas | Lanzamiento, 13 mensuales y el cierre con sus períodos; la siguiente es siempre la contigua; cada pedido y cada resultado van en la entrega de su fecha; republicar no duplica (V22); sin pendientes, `no_delivery` | Unitaria e integración |
| AC10 | Validación | Cada variante malformada da al menos su regla con el identificador esperado; el accesorio válido, 0 bloqueantes; los recuentos de las advertencias e informativas son los de la verdad del accesorio | Contrato |
| AC11 | Sin daño tras un rechazo | El SHA-256 del SQLite, de cada Parquet, del HTML, de cada JSON del sitio y del sello es idéntico antes y después de una corrida rechazada | Integración |
| AC12 | Carga | Filas cargadas = filas de la entrega; todos los controles SQL en 0; recargar desde cero da el mismo contenido; un control que falla deja el almacén como estaba | Integración |
| AC13 | Sin fuga I: columnas | `build_features` lanza `LeakageError` si recibe o produce una columna de la lista de fuga o fuera de la lista permitida; las columnas de la matriz son exactamente las del conjunto configurado; el SQL y el código de las variables no leen ninguna tabla de resultados | Unitaria y estática |
| AC14 | Sin fuga II: perturbación | Cambiar todas las columnas de resultado de todos los pedidos no cambia ninguna variable; cambiar los resultados todavía no conocidos en una fecha, o borrar, cambiar y agregar pedidos posteriores a ella, no cambia ningún modelo, calibración, umbral, puntaje ni marca de esa fecha (igualdad exacta) | Integración |
| AC15 | Partición | Bloques con los bordes exactos; los pedidos de las purgas no entran en ningún bloque; cada etiqueta del entrenamiento se conoce antes del inicio de la validación y cada una de la validación antes del lanzamiento; real: los conteos de la sección 4 | Unitaria y real |
| AC16 | Líneas base | Valores exactos en un caso a mano; cambiar etiquetas de la validación o de la prueba no cambia ninguna tasa de B1-B5 | Unitaria |
| AC17 | Modelo y controles | En el accesorio con una señal plantada (control positivo), el modelo supera a B5 con el intervalo de C03 por encima de 0; en el accesorio sin señal (control negativo), ese intervalo contiene el 0; dos corridas eligen la misma configuración y dan probabilidades idénticas | Integración (sintético) |
| AC18 | Calibración | El calibrador se ajusta solo con V1 (cambiar etiquetas de V2 o de la prueba no lo cambia); probabilidades finitas en [0, 1] (M04) | Unitaria |
| AC19 | Umbral por costo | Beneficio neto exacto en un caso a mano; el umbral sale solo de V2; cambiar cada clave de `[costs]` cambia el umbral o el beneficio como dice la fórmula (ninguna se ignora); en un accesorio donde F1 elegiría marcar ≥ 95 % de los pedidos, el umbral por costo no lo hace; la sensibilidad tiene una fila por punto de equilibrio del rango | Unitaria |
| AC20 | Sello | El lanzamiento escribe la parte del lanzamiento del sello y la primera evaluación de la prueba la suya; ninguna se reescribe; repetir desde cero con la misma configuración reproduce los valores sellados; con otro umbral, otros costos, otras variables u otro modelo, el resultado se guarda como posterior al sello y no reemplaza el sellado (M05) | Integración |
| AC21 | Lanzamiento congelado | Las corridas siguientes reusan el artefacto del lanzamiento sin cambios; un artefacto con otra huella se rechaza antes de deserializarlo (M03) | Integración |
| AC22 | Puntuación y seguimiento | Cada pedido nuevo de una entrega recibe exactamente un puntaje con su fecha de cálculo y versión del modelo; ningún puntaje cambia en las corridas siguientes; las métricas mensuales de alertas cerradas son exactas en un caso a mano | Unitaria e integración |
| AC23 | Evaluación y criterios | Métricas exactas en un caso a mano de diez pedidos; el remuestreo da lo mismo con la misma semilla; C01-C10 se calculan con los umbrales de la configuración; el texto de cada veredicto coincide con su número; con un resumen desfavorable, el README dice «no supera» | Unitaria y consistencia real |
| AC24 | Modelo estrella | Claves únicas y no nulas; 100 % de las claves foráneas en su dimensión; todas las relaciones 1:* con filtro unidireccional y activas; esquema de cada Parquet = especificación; cada referencia `tabla[columna]` de las medidas existe | Contrato |
| AC25 | Diseño del tablero | El HTML contiene todas las páginas, visuales, medidas y relaciones de la especificación; no carga recursos externos; pesa < 2 MB; un nombre de producto con `<script>` en el accesorio aparece escapado | Unitaria |
| AC26 | Exportación para el sitio | Los cinco JSON cumplen su esquema, suman ≤ 200 KB, no contienen `order_id`, ningún identificador ni ningún valor centinela personal, ninguna celda publicada tiene menos de 20 pedidos y sus cifras son iguales a las de los Parquet y del resumen de la misma corrida | Contrato |
| AC27 | Estado y cierre de la corrida | La corrida queda `running` en SQLite y en la tabla exportada hasta que `report` la cierra; una corrida interrumpida después de exportar no queda `ok`; una que falla reescribe la tabla exportada y el HTML como `failed`; una `no_delivery` reescribe la tabla exportada y el HTML; la traza queda en el archivo de estado; si las escrituras del propio manejador de fallas fallan, quien llama sigue viendo el error de la tarea | Integración |
| AC28 | Corridas cerradas | Cualquier tarea de una corrida `ok` o `no_delivery` se rechaza antes de escribir nada (salida 2); una segunda `deliver` en una corrida que ya tiene entrega, también; las tareas del modelo de una corrida cuya entrega no es la última cargada, también; los registros de la corrida cerrada quedan byte a byte iguales | Integración y punta a punta |
| AC29 | CLI | Códigos de salida 0, 1, 2 y 3; una combinación de opciones incompatible sale con 2 antes de escribir nada; una selección que no empieza por `fetch_source` exige `--run-key`; cada opción documentada cambia el resultado (una prueba por opción: ninguna se ignora en silencio); toda ruta relativa se resuelve contra la carpeta del proyecto | Punta a punta |
| AC30 | Modo rápido aislado | Desde un clon limpio y desde otro directorio de trabajo, la repetición rápida termina y nada fuera de `data/quick/` cambia (hash de todo el árbol del proyecto antes y después) | Punta a punta |
| AC31 | DAG y Docker | Identificadores y orden de tareas iguales a los de la CLI; programación mensual, sin catchup, una corrida y una tarea activas; `pip check` sin conflictos en la imagen; `airflow dags test` dos veces seguidas (lanzamiento y `M2017-01`) en modo rápido terminan bien con el modelo congelado idéntico; un rechazo falla sin reintentos | Estática y Docker |
| AC32 | Compose endurecido | Interfaz en `127.0.0.1`; cada secreto con `${VAR:?}`, incluida la contraseña de PostgreSQL y la de la cadena de conexión; ningún valor literal de secreto en todo el compose (la prueba recorre cada valor); `.env.example` con las claves vacías; `src/`, `sql/`, `config/` y `dags/` en solo lectura; imágenes con versión y resumen; nombre de proyecto propio | Estática |
| AC33 | Punta a punta real | La repetición completa cumple RNF02 y RNF03, con tiempos y memoria medidos y el contador nombrado; cada Parquet ≤ 25 MB | Real |
| AC34 | Reproducibilidad | Dos repeticiones completas seguidas dan el mismo hash de contenido en cada Parquet y JSON, sin las columnas de la corrida | Integración (sintético) |
| AC35 | Documentación | README con los títulos del plan en orden; el diccionario cubre cada columna de cada Parquet con unidad; cada decisión con sus siete campos (Contexto, Opciones, Decisión, Motivo, Afecta, Quién, Estado); runbook R1-R13; el número de pruebas que dicen los documentos es el de `pytest --collect-only`; cada cifra de memoria nombra su contador; el bloque de resultados es el del resumen; las advertencias abiertas que dice el README son las de `docs/review-advisories.md`; todo esto pasa en el último commit de corrección | Unitaria sobre los Markdown |
| AC36 | Suite rápida | La suite por defecto termina en ≤ 150 s, medido y anotado en la ficha del modelo | Medición |

### 8.1 Criterios del modelo, fijados antes de construir

Se fijan aquí, antes de escribir código, y se confirman en Git con este plan. Período de prueba: pedidos del 2017-01-08 al 2017-09-30 (14.527 pedidos no cancelados), con los resultados conocidos al 2017-10-07. Unidad: el pedido. Se evalúa el modelo congelado del lanzamiento, calibrado, con el umbral `t*` elegido en V2 con los costos por defecto (`alert_cost = 5`, `late_cost = 20`, `effect = 0,5`: punto de equilibrio 0,5; D14). Puntajes de las líneas base: B2, la tasa de retraso del entrenamiento del modo del pedido (First 1,000; Second 0,8017; Same Day 0,4652; Standard 0,4013); B5, la tasa del entrenamiento de la celda modo × franja (antes o desde las 12:00).

Dos columnas dicen qué se vio antes de fijarlos (D16): lo que vio la investigación (en líneas, en la prueba, sin la hora del pedido) y lo que vio el agente de planificación (solo en el entrenamiento).

| ID | Criterio | Umbral | Visto por la investigación | Visto por el planificador |
| --- | --- | --- | --- | --- |
| C01 | Discriminación frente a la regla por modo | AUC(modelo) > AUC(B2) | Sí: 0,736 frente a 0,740, sin la hora | En el entrenamiento: B5 llega a 0,773 frente a 0,740 |
| C02 | Mejora fuera del ruido frente a la regla por modo | Límite inferior del intervalo del 95 % de AUC(modelo) − AUC(B2) > 0 | No | No |
| C03 | Mejora fuera del ruido frente a la regla de dos variables | Límite inferior del intervalo del 95 % de AUC(modelo) − AUC(B5) > 0 | No | No (B5 solo en el entrenamiento) |
| C04 | Beneficio neto frente a la regla de la investigación (B2 a 0,5: First y Second Class) | BN(modelo, `t*`) > BN(B2 a 0,5) | En parte: con sus cifras en líneas a 0,5 y sin la hora, el planificador deduce un empate (131,4 frente a 131,5 u. por cada 100 líneas) | No |
| C05 | Beneficio neto frente a la regla de dos variables con su propio umbral | BN(modelo, `t*`) > BN(B5, `t*_B5`), los dos umbrales elegidos en V2 con el mismo procedimiento | No | No |
| C06 | Calibración en la prueba | ECE ≤ 0,03 (10 intervalos de igual ancho) y \|media de p − tasa observada\| ≤ 0,02 | No (solo el Brier sin calibrar: 0,192) | No |
| C07 | Brier frente a la regla por modo | Brier(modelo) < Brier(B2) | En parte: 0,192 para el modelo sin la hora; el de B2, no | No |
| C08 | Con el mismo número de alertas que B2 a 0,5 | Los k pedidos de mayor riesgo del modelo (k = los que marca B2) contienen más pedidos tarde que los k de B2 | En parte: a 0,5, 36,3 % marcado con precisión 0,862 frente a 34,4 % y 0,882 (líneas, sin la hora) | No |
| C09 | Sensibilidad a los costos | En cada punto de equilibrio de 0,10 a 0,90, de a 0,05, con los umbrales elegidos en V2: BN(modelo) ≥ BN(B2) | No | No |
| C10 | Tramo posterior al cambio de forma (informativo) | Sin umbral: AUC, BN, % marcado, precisión y recall del modelo, B2 y B5 del 2017-10-01 al 2018-01-31 (8.043 pedidos). El criterio es que esté escrito y sea consistente | Sí: B2 con precisión 0,889 y recall 0,540 (líneas) | No |

Definiciones que fijan los criterios:

- **AUC**: `roc_auc_score`, con los empates contados por mitades (las reglas tienen pocos valores distintos).
- **Intervalo del 95 %**: remuestreo pareado de los pedidos de la prueba con reposición, 1.000 remuestras, semilla 0, los mismos índices para los dos puntajes; percentiles 2,5 y 97,5 de la diferencia (D18).
- **Beneficio neto (BN)**: por pedido marcado, `is_late × late_cost × effect − alert_cost`; 0 por pedido no marcado; se informa el total en u. y por cada 1.000 pedidos. Con los costos por defecto, `BN = Σ_marcados (10 × is_late − 5)`.
- **Umbral `t*`**: el valor de la grilla 0,01 a 0,99 que maximiza el BN en V2; en un empate, el mayor (menos alertas). Para B5 y B2 en C05 y C09, el mismo procedimiento sobre su puntaje.
- **ECE**: `Σ_b (n_b / N) · |media de p en b − tasa observada en b|`, con 10 intervalos de igual ancho en [0, 1].
- **Brier**: `mean((p − is_late)²)`, con B2 usando su tasa del entrenamiento como probabilidad.
- Un criterio que no se cumple se informa con su número en el resumen, el README, la ficha del modelo, el tablero y el sitio. Un empate no supera: los criterios con «>» se cumplen solo con una diferencia a favor.

Lo esperable, dicho antes de medir: con lo que se vio en el entrenamiento (sección 4), el modelo debería encontrar el corte del mediodía y superar a B2 (C01, C02, C04, C07, C08), y no debería superar a B5 (C03, C05), porque dentro de cada celda de B5 no se vio ninguna otra señal. Si es así, el resultado del proyecto es que el modelo aprende una regla de dos variables y nada más, y se dice con esos números (D16).

## 9. Plan de pruebas

Cinco niveles con pytest en el `.venv` del proyecto, con la prueba primero en cada unidad (plan, sección 21). Marcas: `real_data` (necesita el CSV real en `data/raw/`), `docker` (necesita Docker), `slow`. Por defecto: `-m "not real_data and not docker"`.

| Nivel | Qué prueba | Requerimientos | Criterios |
| --- | --- | --- | --- |
| Unitarias | Configuración y rutas (la URL real es HTTPS y los hosts permitidos lo son), descarga con un servidor HTTP local en un hilo (redirecciones, tope de bytes, huella), variables y listas de columnas, partición, líneas base, modelo, calibración, umbral, sensibilidad, sello, evaluación, criterios, especificación del modelo semántico, HTML, exportación del sitio, compose y DAG estáticos | RF01-RF04, RF10-RF20, RF22, RF23 | AC01, AC02, AC13, AC15, AC16, AC18, AC19, AC22, AC23, AC25, AC32 |
| Contrato de datos | Contrato del archivo y reglas sobre accesorios válidos y malformados; esquema de cada Parquet y de cada JSON; integridad referencial; referencias de DAX; datos personales ausentes | RF03-RF05, RF07, RF21, RF23 | AC03-AC08, AC10, AC24, AC26 |
| Integración | Entregas, carga y controles, rechazo sin daño, sin fuga por perturbación, controles positivo y negativo, sello, lanzamiento congelado, estado y cierre, corridas cerradas, reproducibilidad | RF06, RF08, RF09, RF14, RF16-RF19, RF24 | AC09, AC11, AC12, AC14, AC17, AC20-AC22, AC27, AC28, AC34 |
| Punta a punta | CLI completa en modo rápido y aislada; repetición real (`real_data`); `airflow dags test` en Docker (`docker`) | RF24 | AC29-AC31, AC33, AC36 |
| Documentación | Títulos del README, diccionario, siete campos por decisión, runbook, número de pruebas, contadores de memoria, bloque de resultados, advertencias abiertas | RF25 | AC35 |

Accesorios: un generador (`tests/fixtures/make_fixture.py`) arma un CSV cp1252 con CRLF y las 53 columnas reales, con unos 3.000 pedidos del 2015-01-01 al 2018-01-31 que siguen la mecánica medida (12 h, 48 h o de 48 a 144 h entre pedido y envío según el modo; días reales por diferencia de calendario; de 1 a 5 líneas antes de 2017-10 y una después), pedidos cancelados completos, correo y contraseña enmascarados, valores centinela en las 15 columnas personales, un nombre de producto con `<script>`, espacios finales en otro y las columnas repetidas idénticas, todos en posiciones registradas en `FixtureTruth`. Dos variantes de señal: `none` (la del archivo real) y `standard_market` (los pedidos Standard Class de un mercado llegan tarde con probabilidad 0,7: control positivo). Más variantes malformadas, una por regla bloqueante. Las pruebas no usan la fuente real salvo con `real_data`, y ninguna prueba ni ninguna persona calcula una métrica en el bloque de prueba real antes de la corrida de U25 (D17).
