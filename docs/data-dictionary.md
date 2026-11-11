# Diccionario de datos

Qué significa cada columna del archivo de la fuente, adónde va dentro del flujo y qué significa cada columna de cada tabla que el flujo exporta para Power BI, con su unidad. Las dos primeras partes se escribieron a mano a partir de `docs/research.md`, de `docs/requirements.md` (sección 4) y de `src/latedel/schema.py`, que es el único lugar del código que lista columnas; una prueba comprueba que cada una de las 53 columnas de la fuente está aquí con el destino que le da `schema.py`. La tercera parte se genera desde la especificación del modelo semántico (`src/latedel/dashboard/semantic_model.py`) con `python -m latedel docs-sync`, y una prueba verifica que el texto y la especificación coinciden y que cada Parquet que escribió la corrida real tiene exactamente esas columnas.

## Convenciones

- **Pedido y línea.** La unidad del modelo es el pedido (`order_id`, de `Order Id`); una línea es un producto dentro de un pedido (`line_id`, de `Order Item Id`). Antes de 2017-10 un pedido tiene de una a cinco líneas; desde entonces, casi siempre una.
- **Montos.** Los de la fuente, en la moneda del archivo, que la fuente no declara (se supone que son dólares). Los costos del umbral están en unidades genéricas («u.»): lo que decide el umbral es su cociente (D14).
- **Tiempo.** La fecha y hora del pedido se guarda como texto ISO sin zona, como viene en el archivo. Las claves de fecha son enteros: `AAAAMMDD` para un día (`date_key`, `as_of_key`) y `AAAAMM` para un mes (`month_key`). La fecha de cálculo de una corrida (`as_of_date`) es el fin del día de su entrega.
- **Probabilidad y proporción.** Una probabilidad va de 0 a 1 (el riesgo, un umbral, un punto de equilibrio); una proporción es una parte de un total, también de 0 a 1 (una tasa de retraso, la parte marcada, la precisión, el recall).
- **Memoria.** Toda cifra de memoria nombra su contador: `peak_working_set_mb` es el pico del conjunto de trabajo (`PeakWorkingSetSize` de `GetProcessMemoryInfo` en Windows; `ru_maxrss` de `resource.getrusage` en Linux) y `peak_commit_mb` el pico de memoria comprometida (`PeakPagefileUsage`, solo en Windows), en MB de 1.048.576 bytes (D29).
- **Vacíos.** Un valor que no existe queda nulo, no cero: un pedido que no se puntuó no tiene riesgo, uno abierto no tiene días reales, una regla sin probabilidad no tiene Brier.
- **Estado de cada columna de la fuente.** *Verificado*: lo dice el diccionario de la fuente o se comprobó en el perfilado (`research.md`, `requirements.md`, sección 4, o la corrida real). *Inferido*: sale del nombre o de los valores observados y la fuente no lo documenta.

## Archivo de origen

`DataCoSupplyChainDataset.csv` (95.910.149 bytes, SHA-256 `fa6d022ed437155e1a2f0378710602848703c8a7f203f7ff5d77805bf8480aa6`), en cp1252 con CRLF, sin BOM ni bytes nulos: 180.519 filas de datos, todas con 53 campos, y fechas con el formato `%m/%d/%Y %H:%M`, sin zona horaria. El flujo rechaza el archivo si una columna falta, sobra o cambia de nombre o de lugar, si una fila no tiene 53 campos o si un valor no convierte a su tipo (V03 a V05).

Destinos: **pedido** (la tabla `orders`, una fila por pedido, con lo que se sabe al crearlo), **línea** (`order_lines`, una fila por línea), **resultado** (`outcomes`, el flujo aparte de lo que se conoce después: nunca llega a la matriz del modelo, D06), **personal** (se usa solo en las comprobaciones de la lectura y se descarta antes de escribir nada, D07), **repetida** o **constante** (no se lleva) y **permitida, no llevada** (se conoce al crear el pedido, pero las tablas guardan el nombre y no el código, D39).

| Columna | Destino | Nombre en el proyecto | Unidad | Significado | Estado |
| --- | --- | --- | --- | --- | --- |
| `Type` | pedido | `payment_type` | — | Tipo de pago: `DEBIT`, `TRANSFER`, `CASH` o `PAYMENT`. | Verificado |
| `Days for shipping (real)` | resultado | `real_days` | días | Días reales de envío, de 0 a 6: la diferencia en días de calendario entre la fecha de envío y la del pedido. Con los programados da la etiqueta; delata el resultado. | Verificado |
| `Days for shipment (scheduled)` | pedido | `scheduled_days` | días | Días programados, que fija el modo: 0 en Same Day, 1 en First Class, 2 en Second Class y 4 en Standard Class (V10). | Verificado |
| `Benefit per order` | resultado | `benefit_total` | moneda del archivo | Beneficio de la línea, pese al nombre; repite a `Order Profit Per Order`. Se aparta por precaución, porque no consta cuándo se calcula; el flujo de resultados guarda su suma por pedido. | Verificado |
| `Sales per customer` | repetida | — | moneda del archivo | Igual a `Order Item Total` en todas las filas. | Verificado |
| `Delivery Status` | resultado | `delivery_status` | — | `Late delivery`, `Advance shipping`, `Shipping on time` o `Shipping canceled`. Es la etiqueta con otras palabras; `Shipping canceled` marca los 2.855 pedidos cancelados. | Verificado |
| `Late_delivery_risk` | resultado | `source_label` | — | La etiqueta de la fuente: 1 si llegó tarde. Coincide con días reales mayores que programados en el 100 % de los pedidos no cancelados (V08). | Verificado |
| `Category Id` | permitida, no llevada | — | — | Código de la categoría; se lleva su nombre. | Verificado |
| `Category Name` | línea | `category_name` | — | Categoría del producto (50 distintas). | Verificado |
| `Customer City` | personal | — | — | Ciudad del cliente (cuasi identificador). | Verificado |
| `Customer Country` | pedido | `customer_country` | — | País del cliente: «EE. UU.» o «Puerto Rico». | Verificado |
| `Customer Email` | personal | — | — | Correo del cliente, enmascarado como `XXXXXXXXX` en todas las filas; uno sin enmascarar rechaza la fuente (V06). | Verificado |
| `Customer Fname` | personal | — | — | Nombre del cliente. | Verificado |
| `Customer Id` | personal | — | — | Seudónimo numérico del cliente (20.652 distintos). | Verificado |
| `Customer Lname` | personal | — | — | Apellido del cliente. | Verificado |
| `Customer Password` | personal | — | — | Contraseña del cliente, enmascarada como `XXXXXXXXX` en todas las filas (V06). | Verificado |
| `Customer Segment` | pedido | `customer_segment` | — | `Consumer`, `Corporate` o `Home Office`. | Verificado |
| `Customer State` | personal | — | — | Estado del cliente. | Verificado |
| `Customer Street` | personal | — | — | Calle del cliente, con su número. | Verificado |
| `Customer Zipcode` | personal | — | — | Código postal del cliente (cuasi identificador). | Verificado |
| `Department Id` | permitida, no llevada | — | — | Código del departamento; se lleva su nombre. | Verificado |
| `Department Name` | línea | `department_name` | — | Departamento del producto (11 distintos). | Verificado |
| `Latitude` | personal | — | grados | Latitud que el diccionario de la fuente atribuye a la tienda; se trata como personal por precaución. | Verificado |
| `Longitude` | personal | — | grados | Longitud, como `Latitude`. | Verificado |
| `Market` | pedido | `market` | — | Mercado del pedido (5 distintos, por ejemplo `LATAM`, `Europe`, `Pacific Asia`). | Verificado |
| `Order City` | personal | — | — | Ciudad de destino del pedido. | Verificado |
| `Order Country` | pedido | `order_country` | — | País de destino del pedido (164 distintos). | Verificado |
| `Order Customer Id` | personal | — | — | Igual a `Customer Id` en todas las filas. | Verificado |
| `order date (DateOrders)` | pedido | `order_ts` | fecha y hora | Cuándo se creó el pedido, con resolución de minutos y sin zona; de él salen la fecha, la hora, el día de la semana y el mes del pedido, y la entrega a la que pertenece. | Verificado |
| `Order Id` | pedido y línea | `order_id` | — | Número del pedido (65.752 distintos); la clave del pedido y de su resultado. | Verificado |
| `Order Item Cardprod Id` | línea | `product_card_id` | — | Número de tarjeta del producto (118 distintos); `product_key` en el modelo estrella. | Verificado |
| `Order Item Discount` | línea | `discount` | moneda del archivo | Descuento de la línea: `Sales` menos `Order Item Total`. | Verificado |
| `Order Item Discount Rate` | línea | `discount_rate` | proporción | Tasa de descuento de la línea (18 valores distintos). | Verificado |
| `Order Item Id` | línea | `line_id` | — | Número de la línea, único en el archivo. | Verificado |
| `Order Item Product Price` | repetida | — | moneda del archivo por unidad | Igual a `Product Price` en todas las filas. | Verificado |
| `Order Item Profit Ratio` | resultado | `profit_ratio_mean` | proporción | Rentabilidad de la línea; cálculo y momento no documentados. Se aparta por precaución; el flujo de resultados guarda su media por pedido. | Inferido |
| `Order Item Quantity` | línea | `quantity` | unidades | Unidades de la línea, de 1 a 5. | Verificado |
| `Sales` | línea | `sales` | moneda del archivo | Ventas de la línea: precio por cantidad (V13). | Verificado |
| `Order Item Total` | línea | `item_total` | moneda del archivo | Total de la línea: `Sales` menos el descuento, con diferencias de hasta 0,0100135 en el archivo real (V13 tolera 0,015). | Verificado |
| `Order Profit Per Order` | resultado | — | moneda del archivo | Igual a `Benefit per order` en todas las filas: se aparta con ella y no se guarda dos veces. | Verificado |
| `Order Region` | pedido | `order_region` | — | Región de destino del pedido (23 distintas). | Verificado |
| `Order State` | personal | — | — | Estado o provincia de destino del pedido. | Verificado |
| `Order Status` | resultado | `order_status` | — | Estado del pedido al extraer el archivo (`COMPLETE`, `CLOSED`, `PENDING`, `CANCELED`, `SUSPECTED_FRAUD` y otros); no se conoce al crearlo. `CANCELED` y `SUSPECTED_FRAUD` coinciden con `Shipping canceled`. | Verificado |
| `Order Zipcode` | personal | — | — | Código postal de destino; vacío en el 86,2 % de las filas. | Verificado |
| `Product Card Id` | repetida | — | — | Igual a `Order Item Cardprod Id` en todas las filas. | Verificado |
| `Product Category Id` | repetida | — | — | Igual a `Category Id` en todas las filas. | Verificado |
| `Product Description` | constante | — | — | Vacía en todas las filas. | Verificado |
| `Product Image` | constante | — | — | Dirección de la imagen del producto; no es un dato del pedido. | Verificado |
| `Product Name` | línea | `product_name` | — | Nombre del producto (118 distintos); se le quitan los espacios finales, que traen 1.774 líneas (V14). | Verificado |
| `Product Price` | línea | `product_price` | moneda del archivo por unidad | Precio unitario del producto. | Verificado |
| `Product Status` | constante | — | — | 0 en todas las filas. | Verificado |
| `shipping date (DateOrders)` | resultado | `shipping_ts` | fecha y hora | Fecha de envío: la del pedido más 12 h en Same Day, 48 h en First Class y de 48 a 144 h en pasos de 24 h en Second y Standard Class. Contiene los días reales; delata el resultado. | Verificado |
| `Shipping Mode` | pedido | `shipping_mode` | — | `Standard Class`, `Second Class`, `First Class` o `Same Day`. | Verificado |

## Tablas intermedias

En `data/interim/` (no se versionan), escritas por `fetch_source` antes de cualquier otra tarea y verificadas en cada lectura con un manifiesto de huellas que se escribe al final (D50):

- `orders.parquet`: una fila por pedido con las columnas de destino **pedido**, más `order_date` (la fecha del pedido) y `regime` (`pre_break` antes del 2017-10-01 y `post_break` desde esa fecha). 65.752 filas.
- `order_lines.parquet`: una fila por línea con las columnas de destino **línea**. 180.519 filas.
- `outcomes.parquet`: una fila por pedido con las columnas de destino **resultado**, más `outcome_known_date` (`max(fecha del pedido + días reales, fecha del envío)`, la fecha en que el resultado se conoce y que decide su entrega) e `is_cancelled`. 65.752 filas.
- `interim_manifest.json`: el SHA-256 de cada uno de los tres archivos y la huella del CSV de origen.

## Tablas exportadas

Veinte archivos Parquet en `outputs/powerbi/`, diez dimensiones y diez hechos, con las nueve relaciones del modelo estrella (de uno a varios, con el filtro en un solo sentido y activas; están en el diseño del tablero). Cada archivo trae en sus metadatos la corrida que lo escribió, la fecha de cálculo, la huella del CSV de origen y la atribución de la licencia. Los nombres de tablas y columnas están en inglés; los nombres de modos, segmentos, métodos y criterios, en español. Una columna sin unidad lleva un guion.

Una nota sobre `dim_pipeline_run`: la fila de una corrida toma sus picos de memoria (`peak_working_set_mb`, `peak_commit_mb`) en el primer paso del cierre, antes de que la tarea `report` mida los suyos, así que puede quedar un poco por debajo del pico de esa tarea: en el lanzamiento, 2.128,3 MB de `PeakPagefileUsage` en la fila y 2.145,8 MB en `report`, con el mismo `PeakWorkingSetSize` (594,1 MB) en los dos. Es una limitación conocida y no se corrige sobre las salidas selladas (D83).

<!-- columns:start -->
### `dim_date`

Dimensión. Un día por fila, del primer pedido al cierre. Sirve para filtrar los pedidos por la fecha en que se crearon. Clave: `date_key`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `date_key` | entero 32 | AAAAMMDD | Fecha del pedido como número (clave). |
| `date` | fecha | — | La fecha. |
| `year` | entero 32 | — | Año. |
| `month` | entero 32 | — | Mes del año (1 a 12). |
| `month_key` | entero 32 | AAAAMM | Año y mes. |
| `month_name_es` | texto | — | Nombre del mes en español. |
| `weekday` | entero 32 | — | Día de la semana (0 es lunes). |
| `weekday_es` | texto | — | Nombre del día de la semana en español. |
| `block_es` | texto | — | Bloque de la partición en el tiempo al que pertenece la fecha (entrenamiento, purga, validación, prueba o posterior al cambio de forma). |
| `is_test` | verdadero/falso | — | Verdadero si la fecha es del bloque de la prueba. |

### `dim_month`

Dimensión. Un mes por fila; es el eje del seguimiento mensual. Clave: `month_key`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `month_key` | entero 32 | AAAAMM | Año y mes (clave). |
| `month_start` | fecha | — | Primer día del mes. |
| `year_month_label` | texto | — | El mes escrito como AAAA-MM. |
| `block_es` | texto | — | Bloque de la partición en el que cae el primer día del mes. |
| `regime` | texto | — | pre_break antes del cambio de forma de los datos (2017-10) y post_break desde entonces. |

### `dim_shipping_mode`

Dimensión. Los cuatro modos de envío con la tasa de retraso del entrenamiento; vacía si todavía no hay modelo registrado. Clave: `mode_key`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `mode_key` | texto | — | Modo de envío tal como viene de la fuente (clave). |
| `mode_es` | texto | — | Nombre del modo en español. |
| `scheduled_days` | entero 32 | días | Días programados que fija la fuente para el modo. |
| `train_late_rate` | decimal | proporción | Tasa de retraso del modo en el bloque de entrenamiento: la regla B2. |
| `sort_order` | entero 32 | — | Orden de aparición, de menos a más días programados. |

### `dim_geo`

Dimensión. Una fila por combinación de mercado, región y país que tiene pedidos. No hay ciudades, estados, códigos postales ni coordenadas: se descartan en la lectura (D07). Clave: `geo_key`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `geo_key` | entero 32 | — | Número de la combinación de mercado, región y país (clave sustituta). |
| `market` | texto | — | Mercado. |
| `order_region` | texto | — | Región del pedido. |
| `order_country` | texto | — | País del pedido. |

### `dim_product`

Dimensión. Un producto por fila. Clave: `product_key`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `product_key` | entero 32 | — | Número de tarjeta del producto de la fuente (clave). |
| `product_name` | texto | — | Nombre del producto, sin espacios finales. |
| `category_name` | texto | — | Categoría del producto. |
| `department_name` | texto | — | Departamento del producto. |

### `dim_segment`

Dimensión. Los segmentos de cliente de la fuente. Clave: `segment_key`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `segment_key` | texto | — | Segmento del cliente tal como viene de la fuente (clave). |
| `segment_es` | texto | — | Nombre del segmento en español. |

### `dim_payment`

Dimensión. Los tipos de pago de la fuente. Clave: `payment_key`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `payment_key` | texto | — | Tipo de pago tal como viene de la fuente (clave). |
| `payment_es` | texto | — | Nombre del tipo de pago en español. |

### `dim_model`

Dimensión. El modelo congelado en el lanzamiento y los costos supuestos con que se eligió su umbral. Sin relaciones: alimenta tarjetas y medidas. Clave: `model_version`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `model_version` | texto | — | Versión del modelo congelado (clave). |
| `feature_set` | texto | — | Conjunto de variables elegido en V1 (f0, f1 o f2). |
| `params_es` | texto | — | Parámetros del candidato elegido, en palabras. |
| `calibration` | texto | — | Método de calibración (isotónica). |
| `threshold` | decimal | probabilidad | Umbral de alerta elegido en V2 por beneficio neto. |
| `alert_cost` | decimal | u. | Costo supuesto de una alerta. |
| `late_cost` | decimal | u. | Costo supuesto de un pedido que llega tarde. |
| `effect` | decimal | proporción | Parte del costo del retraso que evita una alerta (supuesto). |
| `break_even` | decimal | probabilidad | Punto de equilibrio: costo de la alerta dividido por costo del retraso por efecto. |
| `trained_as_of` | fecha | — | Fecha de cálculo del lanzamiento en que se entrenó. |
| `config_sha256` | texto | — | Huella SHA-256 de la configuración del lanzamiento. |
| `seal_state` | texto | — | sealed si la configuración es la que consta en el sello; post_seal si es otra; none si todavía no hay sello. |

### `dim_pipeline_run`

Dimensión. Una fila por corrida con el estado con que la dejó SQLite. Clave: `run_key`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `run_key` | texto | — | Clave de la corrida (clave). |
| `started_at` | texto | — | Inicio de la corrida. |
| `finished_at` | texto | — | Fin de la corrida; vacío mientras corre. |
| `status` | texto | — | running, ok, failed, rejected o no_delivery. |
| `delivery_id` | texto | — | Entrega que cargó la corrida. |
| `as_of_date` | fecha | — | Fecha de cálculo de la corrida. |
| `source_sha256` | texto | — | Huella SHA-256 del archivo de la fuente. |
| `orders_delivery` | entero 64 | — | Pedidos de la entrega. |
| `outcomes_delivery` | entero 64 | — | Resultados de la entrega. |
| `orders_scored` | entero 64 | — | Pedidos puntuados por la corrida. |
| `alerts` | entero 64 | — | Alertas emitidas por la corrida. |
| `peak_working_set_mb` | decimal | MB | Pico del conjunto de trabajo del proceso (PeakWorkingSetSize en Windows, ru_maxrss en Linux). |
| `peak_commit_mb` | decimal | MB | Pico de memoria comprometida (PeakPagefileUsage); vacío fuera de Windows. |
| `message` | texto | — | Mensaje de la corrida. |
| `as_of_key` | entero 32 | AAAAMMDD | Fecha de cálculo como número. |

### `dim_as_of`

Dimensión. Tabla de parámetro: una fila por entrega cargada. Elige la fecha de cálculo en las medidas; no tiene relaciones. Clave: `as_of_key`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `as_of_key` | entero 32 | AAAAMMDD | Fecha de cálculo como número (clave). |
| `as_of_date` | fecha | — | Fecha de cálculo de una entrega cargada. |
| `as_of_label` | texto | — | Entrega y fecha, para el segmentador. |

### `fact_order`

Hecho. Un pedido por fila, con el puntaje y el resultado donde existen. El puntaje no cambia nunca después de emitido. Grano: `order_id`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `order_id` | entero 64 | — | Número de pedido de la fuente (grano). Es la clave con la que un equipo actúa sobre una alerta (D32). |
| `order_date_key` | entero 32 | AAAAMMDD | Fecha del pedido. |
| `mode_key` | texto | — | Modo de envío. |
| `geo_key` | entero 32 | — | Mercado, región y país del pedido. |
| `main_product_key` | entero 32 | — | Producto de la línea principal del pedido (la de más ventas). |
| `segment_key` | texto | — | Segmento del cliente. |
| `payment_key` | texto | — | Tipo de pago. |
| `order_ts` | texto | — | Fecha y hora de creación, sin zona. |
| `order_hour` | entero 32 | — | Hora del pedido (0 a 23). |
| `order_weekday` | entero 32 | — | Día de la semana del pedido (0 es lunes). |
| `n_lines` | entero 32 | — | Líneas del pedido. |
| `units` | entero 32 | unidades | Unidades del pedido. |
| `gross_sales` | decimal | moneda del archivo | Ventas del pedido, suma de las líneas. |
| `item_total` | decimal | moneda del archivo | Total del pedido, suma de las líneas. |
| `discount` | decimal | moneda del archivo | Descuento del pedido, suma de las líneas. |
| `block` | texto | — | Bloque de la partición: train, purge_1, valid_v1, valid_v2, purge_2, test, post_break o outside. |
| `regime` | texto | — | pre_break o post_break. |
| `outcome_status` | texto | — | open si el resultado todavía no se conoce, closed si llegó, cancelled si el pedido se canceló. |
| `outcome_known_date` | fecha | — | Fecha en que se conoció el resultado; vacía mientras está abierto. |
| `real_days` | entero 32 | días | Días reales de envío; vacíos si está abierto o cancelado. |
| `is_late` | verdadero/falso | — | Verdadero si los días reales superan a los programados; vacío si está abierto o cancelado. |
| `risk` | decimal | probabilidad | Probabilidad calibrada de llegar tarde, calculada al crear el pedido; vacía si el pedido no se puntuó (los del lanzamiento). |
| `is_alert` | verdadero/falso | — | Verdadero si el riesgo alcanza el umbral; vacío si no se puntuó. |
| `threshold` | decimal | probabilidad | Umbral con que se marcó. |
| `scored_as_of_date` | fecha | — | Fecha de cálculo en que se puntuó. |
| `model_version` | texto | — | Versión del modelo que puntuó. |
| `run_key` | texto | — | Corrida que puntuó. |

### `fact_tracking_monthly`

Hecho. Seguimiento mensual de las alertas cerradas a la última fecha de cálculo, por mes del pedido y modo. Grano: `month_key`, `mode_key`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `month_key` | entero 32 | AAAAMM | Mes del pedido. |
| `mode_key` | texto | — | Modo de envío. |
| `orders_scored` | entero 32 | — | Pedidos puntuados. |
| `alerts` | entero 32 | — | Alertas emitidas. |
| `closed_orders` | entero 32 | — | Pedidos puntuados con resultado conocido que no se cancelaron. |
| `closed_alerts` | entero 32 | — | Alertas sobre pedidos cerrados. |
| `alerts_late` | entero 32 | — | Alertas cerradas que llegaron tarde. |
| `late_closed` | entero 32 | — | Pedidos cerrados que llegaron tarde. |
| `late_missed` | entero 32 | — | Pedidos cerrados que llegaron tarde sin alerta. |
| `late_expected` | decimal | pedidos | Retrasos que el modelo esperaba: suma del riesgo de los pedidos cerrados. |
| `cancelled_orders` | entero 32 | — | Pedidos puntuados que se cancelaron después. |
| `alerts_on_cancelled` | entero 32 | — | Alertas sobre pedidos que se cancelaron después (I04). |

### `fact_method_metrics`

Hecho. Las métricas de cada método en cada bloque evaluado. Grano: `scope`, `method`, `seal_state`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `scope` | texto | — | valid_v2, test o post_break. |
| `method` | texto | — | model, b0_all_late, b1_first_class, b2_mode, b3_scheduled_le2, b4_standard_only o b5_mode_noon. |
| `label_es` | texto | — | Nombre del método en español. |
| `auc` | decimal | — | Área bajo la curva ROC, con los empates por mitades. |
| `pr_auc` | decimal | — | Precisión promedio. |
| `brier` | decimal | — | Error cuadrático medio de la probabilidad; vacío en las reglas sin probabilidad. |
| `ece` | decimal | — | Error de calibración esperado con 10 intervalos; vacío en las reglas sin probabilidad. |
| `threshold` | decimal | probabilidad | Umbral con que el método marca. |
| `flagged_share` | decimal | proporción | Parte de los pedidos que marca. |
| `precision` | decimal | proporción | De los pedidos marcados, parte que llegó tarde; vacía si no marca ninguno. |
| `recall` | decimal | proporción | De los pedidos tarde, parte que marca. |
| `nb_total` | decimal | u. | Beneficio neto total con los costos supuestos. |
| `nb_per_1000` | decimal | u. | Beneficio neto por cada 1.000 pedidos. |
| `seal_state` | texto | — | sealed si se calculó con la configuración sellada; post_seal si con otra. |

### `fact_evaluation_summary`

Hecho. Los criterios C01 a C10 con su veredicto. Grano: `criterion_id`, `seal_state`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `criterion_id` | texto | — | C01 a C10. |
| `name_es` | texto | — | Qué mide el criterio. |
| `value` | decimal | — | Valor del modelo. |
| `baseline_value` | decimal | — | Valor con el que se compara. |
| `ci_low` | decimal | — | Límite inferior del intervalo del 95 % (C02 y C03). |
| `ci_high` | decimal | — | Límite superior del intervalo del 95 % (C02 y C03). |
| `threshold_es` | texto | — | Umbral preregistrado, en palabras. |
| `passed` | verdadero/falso | — | Verdadero si se cumple; vacío en C10, que no tiene umbral. |
| `seen_by_research` | texto | — | Si la investigación había visto este número antes de fijar el umbral: sí, en parte o no. |
| `seen_by_planner` | texto | — | Si el agente de planificación lo había visto: sí, en parte o no. |
| `verdict_es` | texto | — | El veredicto, con su número; dice «supera» o «no supera». |
| `seal_state` | texto | — | sealed o post_seal. |

### `fact_roc`

Hecho. La curva ROC de cada método, reducida a unos 200 puntos. Grano: `scope`, `method`, `seal_state`, `point`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `scope` | texto | — | valid_v2, test o post_break. |
| `method` | texto | — | model, b2_mode o b5_mode_noon. |
| `seal_state` | texto | — | sealed o post_seal. |
| `point` | entero 32 | — | Orden del punto en la curva. |
| `fpr` | decimal | proporción | Tasa de falsos positivos. |
| `tpr` | decimal | proporción | Tasa de verdaderos positivos. |

### `fact_calibration`

Hecho. La calibración de cada método por intervalos de igual ancho. Grano: `scope`, `method`, `seal_state`, `bin`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `scope` | texto | — | valid_v2, test o post_break. |
| `method` | texto | — | model, b2_mode o b5_mode_noon. |
| `seal_state` | texto | — | sealed o post_seal. |
| `bin` | entero 32 | — | Intervalo de probabilidad (0 a 9). |
| `p_mean` | decimal | probabilidad | Probabilidad media del intervalo. |
| `observed` | decimal | proporción | Tasa de retraso observada en el intervalo. |
| `orders` | entero 32 | — | Pedidos del intervalo. |

### `fact_cost_sensitivity`

Hecho. Cómo cambia el beneficio neto, y la decisión, si cambian los costos supuestos. Grano: `scope`, `break_even`, `method`, `seal_state`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `scope` | texto | — | valid_v2 o test. |
| `break_even` | decimal | probabilidad | Punto de equilibrio del costo, de 0,10 a 0,90. |
| `method` | texto | — | model, b2_mode o b5_mode_noon. |
| `threshold` | decimal | probabilidad | Umbral elegido en V2 para ese punto de equilibrio. |
| `nb_per_1000` | decimal | u. | Beneficio neto por cada 1.000 pedidos. |
| `flagged_share` | decimal | proporción | Parte de los pedidos que marca. |
| `seal_state` | texto | — | sealed o post_seal. |

### `fact_leakage`

Hecho. Qué le haría al AUC cada columna que delata el resultado. Grano: `source_column`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `source_column` | texto | — | Columna de la fuente (grano); shipping_mode es la referencia. |
| `column_es` | texto | — | Nombre en español. |
| `kind` | texto | — | reference, outcome o profit. |
| `known_at_es` | texto | — | Cuándo se conoce la columna. |
| `auc_with_mode_valid` | decimal | — | AUC en V2 de un clasificador entrenado con el modo de envío y esta columna. |
| `note_es` | texto | — | Por qué queda fuera del modelo. |

### `fact_importance`

Hecho. Importancia por permutación de las variables del modelo en uso, medida en V2. Grano: `feature`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `feature` | texto | — | Variable del modelo (grano). |
| `feature_es` | texto | — | Nombre en español. |
| `auc_drop` | decimal | — | Caída media del AUC en V2 al permutar la variable. |

### `fact_validation`

Hecho. Una fila por corrida y regla de validación. Grano: `run_key`, `rule_id`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `run_key` | texto | — | Corrida. |
| `rule_id` | texto | — | Identificador de la regla (V01, M03, I02...). |
| `name_es` | texto | — | Qué comprueba la regla. |
| `severity` | texto | — | bloqueante, advertencia o informativa. |
| `scope` | texto | — | source, delivery o model. |
| `status` | texto | — | pasa, falla o advierte. |
| `rows_affected` | entero 64 | filas | Filas afectadas. |
| `orders_affected` | entero 64 | pedidos | Pedidos afectados. |
<!-- columns:end -->
