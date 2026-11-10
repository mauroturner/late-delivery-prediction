# Investigación previa: pedidos que van a llegar tarde, antes de que lleguen

Fecha de la investigación: 2026-11-10.

**Leyenda de etiquetas**

- **[Verificado]**: medido directamente sobre el archivo real descargado, o comprobado en la página de la fuente. Se indica el script o la URL.
- **[Documentación]**: tomado de una página oficial o de un registro que se abrió o que aparece en una búsqueda. Se indica la URL.
- **[Fuente secundaria]**: tomado de un fragmento de búsqueda o de un texto que no se abrió completo. Conviene confirmarlo antes de citarlo.
- **[Inferido]**: deducción propia a partir de datos observados. No está confirmada por una fuente.

Esta investigación es una primera mirada: no es el resultado del proyecto. Las cifras de modelo de la sección "Primera mirada del modelo" se obtuvieron con una sola configuración, sin búsqueda de hiperparámetros.

---

## Objetivo

Verificar la fuente, la licencia, el esquema, la fuga de información, la validación en el tiempo, las líneas base, el criterio de costo, los datos personales y los trabajos previos del proyecto "Pedidos que van a llegar tarde, antes de que lleguen" (proyecto 03 del catálogo, tarea T4 en `odd/tasks/portfolio-projects.md`): un clasificador de gradient boosting que, con lo que se sabe al crear el pedido, estima la probabilidad de que llegue tarde, con validación por fecha de pedido y un umbral elegido por costo.

**Hallazgos principales**

1. La fuente es el dataset "DataCo SMART SUPPLY CHAIN FOR BIG DATA ANALYSIS" en Mendeley Data, versión 5, DOI 10.17632/8gx2fvg2k6.5. La licencia es CC BY 4.0. El archivo principal, `DataCoSupplyChainDataset.csv`, pesa 95.910.149 bytes y su SHA-256 es `fa6d022ed437155e1a2f0378710602848703c8a7f203f7ff5d77805bf8480aa6`, igual al que publica Mendeley. [Verificado]
2. El archivo tiene 180.519 filas y 53 columnas, sin filas duplicadas exactas. Hay 65.752 pedidos y 20.652 clientes. Los pedidos van del 2015-01-01 al 2018-01-31; los envíos, hasta el 2018-02-06. [Verificado]
3. La variable objetivo, `Late_delivery_risk`, vale 1 en 98.977 filas (54,8 % del total). Sin los envíos cancelados, la tasa es 57,3 %. Antes de octubre de 2017 la tasa mensual oscila entre 51,9 % y 56,9 %. [Verificado]
4. Hay fuga de información en tres niveles. `Delivery Status` coincide con el objetivo en el 100 % de las filas. La etiqueta equivale a `Days for shipping (real)` > `Days for shipment (scheduled)` en todos los pedidos no cancelados. Y `shipping date` contiene prácticamente la misma información: su diferencia con la fecha del pedido coincide con los días reales en el 97 % de las filas. [Verificado]
5. Desde octubre de 2017 los datos cambian de forma: un pedido por cliente y una línea por pedido, con 8.557 filas en cuatro meses. Solo 81 clientes aparecen antes y después de ese corte. [Verificado]
6. Propuesta de validación en el tiempo: entrenamiento de 89.531 filas (2015-01-01 a 2016-06-30), validación de 29.199 (2016-07-08 a 2016-12-31) y prueba de 43.598 (2017-01-08 a 2017-09-30), con una purga de 7 días entre bloques. El tramo posterior al cambio (8.193 filas) se reporta aparte. [Verificado]
7. Líneas base en prueba: "solo First Class" tiene precisión 1,00 con recall 0,26. La regla "tasa histórica de entrenamiento por modo, si es al menos 0,5" marca First y Second Class: precisión 0,88 y recall 0,53, con 34 % de los pedidos marcados. [Verificado]
8. Primera mirada del modelo: gradient boosting con las columnas que se conocen al crear el pedido alcanza AUC 0,736 en prueba. Un modelo que usa solo el modo de envío y los días programados llega a AUC 0,740. Las demás columnas no aportan señal en esta prueba. [Verificado]
9. Ninguna fuente abierta que se encontró da un costo por pedido tarde o por alerta que sirva de base. El umbral debe partir de supuestos declarados por el proyecto. [Verificado en búsqueda; fuentes de proveedor]
10. Las columnas de correo y contraseña están enmascaradas (`XXXXXXXXX` en todas las filas). Nombres, apellidos y calles parecen generados, pero no se pudo comprobar. Ninguna columna personal debe llegar a las salidas publicadas. [Verificado / Inferido]
11. El texto del sitio contradice los datos en varios puntos: dice que el modelo combina región, producto y días programados, que hay "2 campos" que delatan el resultado y que el riesgo se calcula "al despachar". Ver la sección "Contradicciones con el sitio". [Verificado]

---

## Fuentes

**Datos**

- Página del dataset: https://data.mendeley.com/datasets/8gx2fvg2k6/5 [Verificado: JSON-LD y texto de la página, descargados el 2026-11-10]
- Citación que indica la página: "Constante, Fabian; Silva, Fernando; Pereira, António (2019), 'DataCo SMART SUPPLY CHAIN FOR BIG DATA ANALYSIS', Mendeley Data, v5, http://doi.org/10.17632/8gx2fvg2k6.5" [Verificado]
- Licencia: campo `license` del JSON-LD de la página, "CC BY 4.0", con enlace a http://creativecommons.org/licenses/by/4.0 [Verificado]
- Fecha de publicación de la versión 5 según el JSON-LD: 2019-03-12. [Verificado]
- Lista de archivos con tamaño y SHA-256: https://data.mendeley.com/public-api/datasets/8gx2fvg2k6/files?folder_id=root&version=5&$start=0&$limit=1000. Es el endpoint que usa la propia página, encontrado en su código JavaScript (`/public-api/datasets/{id}/files`). No es una API pública documentada. [Verificado]
- Descarga directa, sin cuenta: https://data.mendeley.com/public-files/datasets/8gx2fvg2k6/files/72784be5-36d3-44fe-b75d-0edbf1999f65/file_downloaded. Responde 302 a un enlace firmado de Amazon S3. [Verificado]
- Diccionario de variables: https://data.mendeley.com/public-files/datasets/8gx2fvg2k6/files/29dc7b05-dda6-4834-8354-5b5cc44430df/file_downloaded [Verificado]
- Es el mismo dataset que aparece en espejos de Kaggle, según búsqueda. Los espejos no se revisaron. [Fuente secundaria]

**Trabajos previos** (ver la sección correspondiente)

- Hugging Face, `yash441alt/dataco-supply-chain-delay-prediction`: https://huggingface.co/yash441alt/dataco-supply-chain-delay-prediction [Verificado: página abierta]
- Hugging Face, `idoyaaran/dataco-supply-chain-model`: https://huggingface.co/idoyaaran/dataco-supply-chain-model [Fuente secundaria: fragmento de búsqueda]
- Research Square, rs-10027505: https://www.researchsquare.com/article/rs-10027505 [Verificado solo el título; el contenido no se pudo leer]
- Research Square, rs-11019500: https://www.researchsquare.com/article/rs-11019500 [Verificado solo el título; el contenido no se pudo leer]
- Universidad de Ulster, comparación de modelos sobre este dataset: https://pure.ulster.ac.uk/en/publications/supply-chain-analytics-a-performance-evaluation-of-machine-learni/ [Fuente secundaria: fragmento de búsqueda]
- Artículo de DEV Community que advierte que `Delivery Status` es casi una copia del objetivo: https://dev.to/sugnikm/how-i-built-a-late-delivery-risk-predictor-for-apl-logistics-what-a-95-delay-rate-in-first-class-1d11 [Fuente secundaria: fragmento de búsqueda. Lo confirma la sección de fuga con datos propios]

**Costo del retraso y de la intervención**

- Sendcloud, "How delivery frustrations impact repeat purchases": https://www.sendcloud.com/how-delivery-frustrations-impact-repeat-purchases/ [Verificado: página abierta; fuente de proveedor sin metodología descrita]
- International Journal of Logistics Management, experimento de escenarios sobre tiempo de entrega y compra, DOI 10.1108/IJLM-05-2016-0130: https://emerald.com/insight/content/doi/10.1108/IJLM-05-2016-0130 [Fuente secundaria: fragmento de búsqueda; no se abrió]
- ShipAid, "How much do delivery issues actually cost ecommerce brands": https://www.shipaid.com/blogs/news/how-much-do-delivery-issues-actually-cost-ecommerce-brands [Fuente secundaria: fragmento de búsqueda; no se abrió]

**Librerías**

- scikit-learn, `HistGradientBoostingClassifier`: https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingClassifier.html [Documentación, no abierta en esta investigación; se usó la versión instalada]

---

## Metodología de verificación

- **Entorno**: Windows 10 Pro. Python 3.11.1 en la venv `scratchpad\p3-research-venv`, creada con `py -3.11 -m venv`. Versiones instaladas: pandas 3.1.1, scikit-learn 1.9.1, pyarrow 26.0.1, NumPy 2.4.6. Todos los scripts se ejecutaron con `python -I`.
- **Descarga**: `curl` a la URL de Mendeley, sin cuenta ni cookies. Los archivos están en `scratchpad\p3-data\`. El `zip` de todo el dataset no se descargó: la página ofrece `/public-api/zip/{id}/download/{version}`, pero los tres archivos sueltos bastan.
- **Lectura**: `pandas.read_csv` con `encoding="cp1252"` y `dtype=str` para no alterar ceros ni espacios. Las fechas se analizaron con el formato `%m/%d/%Y %H:%M`; el 100 % de las filas se convierte sin error.
- **Scripts**: `scratchpad\p3-research-scripts\` (`s01` a `s14`). Cada script tiene su salida `.out`. Las cifras del documento salen de esas salidas.
- **Verificación cruzada**: el SHA-256 y el tamaño del archivo descargado se compararon con los que publica la API de Mendeley. El ETag del servidor se comparó con el MD5 del archivo.
- **Límites**: la primera mirada del modelo usa una sola configuración de gradient boosting. Las cifras de costo son supuestos o fuentes de proveedor. Las cifras de personas son las columnas del archivo; no se confirmó si los datos son reales o generados.

---

## Fuente de datos

### Descarga y control de cambios

| Concepto | Valor | Estado |
|---|---|---|
| Archivo principal | `DataCoSupplyChainDataset.csv`, 95.910.149 bytes | [Verificado] |
| SHA-256 del archivo principal | `fa6d022ed437155e1a2f0378710602848703c8a7f203f7ff5d77805bf8480aa6` (igual al de la API de Mendeley y al calculado localmente con `sha256sum`) | [Verificado] |
| MD5 del archivo principal | `354eb3cd362619ef120ca2201337ae92` (igual al ETag del servidor) | [Verificado] |
| Diccionario, `DescriptionDataCoSupplyChain.csv` | 3.444 bytes; SHA-256 `9828e34669bd6d77e3b4463364cc44a5d52446b5e246fc258758cfe592566c4b` | [Verificado] |
| Tercer archivo, `tokenized_access_logs.csv` | 95.446.364 bytes; SHA-256 `7a4372df63a1e87f5add68bc036bb6064db09069998f25a679aec39f1a8d7765`. Es clickstream, fuera del alcance. No se descargó | [Verificado en la API] |
| Respuesta inicial | 302 a un enlace firmado de S3, sin ETag ni `Last-Modified` en esa respuesta | [Verificado con `curl -sIL`] |
| Respuesta final de S3 | `ETag` = MD5; `Last-Modified: Fri, 03 Jan 2020 05:53:57 GMT`; `Content-Length: 95910149`; `Accept-Ranges: bytes` | [Verificado con `curl -sIL`] |
| Rango de bytes | `Range: bytes=0-99` sobre el enlace firmado devuelve 206 con 100 bytes | [Verificado] |

**Consecuencia para el control de cambios**: el archivo es una versión fija (v5, publicada en 2019). El control debe comparar el SHA-256 descargado con el valor fijado en el proyecto y detenerse si cambia. Los encabezados del servidor no bastan: la descarga pasa por un enlace firmado que caduca, y la respuesta inicial (302) no trae ETag. Como el archivo pesa 96 MB, la descarga completa es razonable para una corrida única. Un muestreo por rango sí es posible, a diferencia de lo que ocurrió en los proyectos 01 y 02.

**Cita para el sitio y el README** (texto de la página): "Constante, Fabian; Silva, Fernando; Pereira, António (2019), 'DataCo SMART SUPPLY CHAIN FOR BIG DATA ANALYSIS', Mendeley Data, v5, http://doi.org/10.17632/8gx2fvg2k6.5. Licencia: CC BY 4.0."

### Filas, columnas y tipos

- **Codificación**: `file` la identifica como ISO-8859. Decodifica como cp1252 sin errores. Como UTF-8 falla en el byte 1709, en "Rajast\xe1n": la codificación de la fuente no es UTF-8. [Verificado]
- **Saltos de línea**: CRLF en las 180.520 líneas físicas (encabezado más 180.519 filas). No hay BOM ni bytes nulos. [Verificado]
- **Filas**: 180.519 filas de datos. Todas tienen 53 campos. [Verificado con `s02_schema_stdlib.py`]
- **Fechas**: `order date (DateOrders)` va de 2015-01-01 00:00 a 2018-01-31 23:38. `shipping date (DateOrders)` va de 2015-01-03 00:00 a 2018-02-06 22:14. Ninguna fecha queda sin convertir. [Verificado]
- **Diferencia envío menos pedido**: entre 0,5 y 6,0 días. [Verificado]

Tabla de columnas (53). "Distintos" cuenta valores distintos en todo el archivo. "Vacíos" cuenta celdas vacías. Las columnas de identificador se marcan como "id".

| Columna | Tipo | Distintos | Vacíos | Notas |
|---|---|---:|---:|---|
| Type | texto | 4 | 0 | Tipo de pago según el diccionario |
| Days for shipping (real) | entero | 7 | 0 | Valores 0 a 6. Días reales; solo se conoce al llegar. Etiqueta, no feature |
| Days for shipment (scheduled) | entero | 4 | 0 | Valores 0, 1, 2 y 4, fijados por el modo de envío |
| Benefit per order | decimal | 21.998 | 0 | Idéntica a `Order Profit Per Order` |
| Sales per customer | decimal | 2.927 | 0 | Idéntica a `Order Item Total` |
| Delivery Status | texto | 4 | 0 | Late delivery 98.977; Advance shipping 41.592; Shipping on time 32.196; Shipping canceled 7.754. Posterior al envío |
| Late_delivery_risk | entero | 2 | 0 | Objetivo: 1 = tarde, 0 = no tarde |
| Category Id | entero | 51 | 0 | Código de categoría |
| Category Name | texto | 50 | 0 | |
| Customer City | texto | 563 | 0 | Personal (cuasi identificador) |
| Customer Country | texto | 2 | 0 | "EE. UU." 111.146; "Puerto Rico" 69.373 |
| Customer Email | texto | 1 | 0 | `XXXXXXXXX` en todas las filas. Enmascarada |
| Customer Fname | texto | 782 | 0 | Nombre. Personal. "Mary" aparece en 65.150 filas |
| Customer Id | id | 20.652 | 0 | Seudónimo numérico del cliente |
| Customer Lname | texto | 1.110 | 8 | Apellido. Personal. "Smith" aparece en 64.104 filas |
| Customer Password | texto | 1 | 0 | `XXXXXXXXX` en todas las filas. Enmascarada |
| Customer Segment | texto | 3 | 0 | Consumer, Corporate, Home Office |
| Customer State | texto | 46 | 0 | |
| Customer Street | texto | 7.458 | 0 | Calle. Personal. Forma: número y nombre de calle |
| Customer Zipcode | texto | 996 | 3 | Personal (cuasi identificador) |
| Department Id | entero | 11 | 0 | |
| Department Name | texto | 11 | 0 | |
| Latitude | decimal | 11.250 | 0 | Ubicación de la tienda, según el diccionario |
| Longitude | decimal | 4.487 | 0 | Ubicación de la tienda, según el diccionario |
| Market | texto | 5 | 0 | |
| Order City | texto | 3.597 | 0 | |
| Order Country | texto | 164 | 0 | |
| Order Customer Id | id | 20.652 | 0 | Idéntica a `Customer Id` (100 % de las filas) |
| order date (DateOrders) | fecha-hora | 65.752 | 0 | Fecha del pedido. Columna de tiempo |
| Order Id | id | 65.752 | 0 | Pedido. Hay 1 a 5 líneas por pedido |
| Order Item Cardprod Id | id | 118 | 0 | Código de producto (RFID) |
| Order Item Discount | decimal | 1.017 | 0 | Sales menos Order Item Total |
| Order Item Discount Rate | decimal | 18 | 0 | |
| Order Item Id | id | 180.519 | 0 | Clave de línea |
| Order Item Product Price | decimal | 75 | 0 | Idéntica a `Product Price` |
| Order Item Profit Ratio | decimal | 162 | 0 | Cálculo y momento no documentados |
| Order Item Quantity | entero | 5 | 0 | Valores 1 a 5 |
| Sales | decimal | 193 | 0 | Igual a precio por cantidad |
| Order Item Total | decimal | 2.927 | 0 | Igual a Sales menos el descuento |
| Order Profit Per Order | decimal | 21.998 | 0 | Idéntica a `Benefit per order` |
| Order Region | texto | 23 | 0 | |
| Order State | texto | 1.089 | 0 | |
| Order Status | texto | 9 | 0 | Estado al extraer el archivo. `CANCELED` y `SUSPECTED_FRAUD` coinciden con las 7.754 filas `Shipping canceled`. Posterior al pedido |
| Order Zipcode | texto | 610 | 155.679 | Vacía en el 86,2 % de las filas |
| Product Card Id | id | 118 | 0 | Idéntica a `Order Item Cardprod Id` (100 %) |
| Product Category Id | entero | 51 | 0 | Idéntica a `Category Id` (100 %) |
| Product Description | texto | 0 | 180.519 | Vacía en todas las filas |
| Product Image | texto | 118 | 0 | URL de la ficha del producto |
| Product Name | texto | 118 | 0 | Espacios finales en el 0,98 % de las filas |
| Product Price | decimal | 75 | 0 | |
| Product Status | entero | 1 | 0 | Constante 0 |
| shipping date (DateOrders) | fecha-hora | 63.701 | 0 | Fecha de envío. Contiene los días reales (ver fuga) |
| Shipping Mode | texto | 4 | 0 | Standard Class 107.752; Second Class 35.216; First Class 27.814; Same Day 9.737 |

Nota de nombres: la primera columna del diccionario tiene espacios al final en algunos nombres (`Days for shipping (real)     `). El encabezado real del archivo no los tiene. [Verificado]

### Duplicados y columnas repetidas

- **Duplicados exactos** en las 53 columnas: 0. [Verificado]
- **Claves**: `Order Item Id` es única en las 180.519 filas. Cada pedido tiene un solo cliente y una sola etiqueta de tarde: no hay pedidos con etiquetas mezcladas. [Verificado]
- **Columnas idénticas** (100 % de las filas): `Benefit per order` = `Order Profit Per Order`; `Sales per customer` = `Order Item Total`; `Order Item Product Price` = `Product Price`. [Verificado]
- **Relaciones de precio**: `Sales` = precio × cantidad (100 %); `Order Item Total` = `Sales` − `Order Item Discount` (100 %). [Verificado]
- **Constantes**: `Product Status` (0), `Customer Email` y `Customer Password` (`XXXXXXXXX`). [Verificado]

**Recomendación para el plan**: conservar una sola copia de cada columna repetida y excluir las constantes. Ninguna de esas columnas aporta información nueva.

---

## Perfil de calidad de datos

- **Vacíos**: `Customer Lname` 8 filas; `Customer Zipcode` 3; `Order Zipcode` 155.679 (86,2 %); `Product Description` 180.519 (100 %). [Verificado]
- **Envíos cancelados**: 7.754 filas (4,3 %) con `Delivery Status` = `Shipping canceled`. Todas tienen `Order Status` = `CANCELED` (3.692) o `SUSPECTED_FRAUD` (4.062). No tienen tiempo de entrega. **Propuesta**: excluirlas de la tarea de "tarde". [Verificado]
- **Definición observada de la etiqueta**: en los 172.765 pedidos no cancelados, `Late_delivery_risk` = 1 si y solo si `Days for shipping (real)` > `Days for shipment (scheduled)`. Ningún pedido tarde tiene días reales menores o iguales a los programados. Las 4.423 filas con días reales mayores sin etiqueta de tarde son todas envíos cancelados (First Class 1.301; Second Class 1.091; Standard Class 1.828; Same Day 203). [Verificado con `s10`, `s14`]
- **Rangos**: días reales de 0 a 6. Días programados según el modo: Same Day 0; First Class 1; Second Class 2; Standard Class 4. `Sales` entre 9,99 y 1.999,99. `Benefit per order` entre −4.274,98 y 911,80. [Verificado]
- **Cantidades**: `Order Item Quantity` de 1 a 5. No hay cantidades negativas ni cero. [Verificado]

---

## Fuga de información

### Columnas posteriores al envío

| Columna | Cuándo se conoce | ¿Puede ser feature al crear el pedido? | Evidencia |
|---|---|---|---|
| `Late_delivery_risk` | Al llegar el pedido | No: es el objetivo | Definición del diccionario |
| `Delivery Status` | Al cerrar el envío | No | Coincide con el objetivo en 100 % de las filas (98.977 de 98.977 tardes; ninguna fuera de esa categoría). Accuracy de la regla `Delivery Status = Late delivery`: 1,000 |
| `Days for shipping (real)` | Al llegar | No | Define la etiqueta en los pedidos no cancelados. Sola da AUC 0,708. Con el modo de envío da AUC 1,000 en prueba |
| `shipping date (DateOrders)` | Al despachar | No, con esta fuente | Su diferencia con la fecha del pedido coincide con `Days for shipping (real)` en 97,2 % de las filas (redondeo hacia arriba) y tiene correlación 0,997. Sola da AUC 0,716. Con el modo y los días programados da AUC 0,9985 en prueba |
| `Order Status` | Al extraer el archivo (estado final o intermedio) | No | `COMPLETE` y `CLOSED` describen pedidos ya terminados; `CANCELED` y `SUSPECTED_FRAUD` coinciden con `Shipping canceled`. Con esta columna, el AUC aleatorio sube de 0,751 a 0,780 |
| `Benefit per order`, `Order Profit Per Order`, `Order Item Profit Ratio` | No documentado | No verificado | Dependen de costos que quizá solo existen al cerrar el pedido. Se excluyen por precaución. Quedan como pregunta para el plan |
| `Sales per customer` | Al crear el pedido | Sí, pero duplicada | Igual a `Order Item Total`. Se usa la otra |

**Momento de la predicción**: el dataset no permite validar un modelo "al despachar", porque la columna de despacho ya contiene la demora. Con las columnas que existen al crear el pedido, el momento que se puede validar es **al crear el pedido**. [Inferido a partir de los datos]

### Columnas disponibles al crear el pedido

- Tipo de pago (`Type`), modo de envío (`Shipping Mode`), días programados (`Days for shipment (scheduled)`).
- Mercado (`Market`), región (`Order Region`), país del destino (`Order Country`), segmento y país del cliente (`Customer Segment`, `Customer Country`).
- Categoría, departamento y producto (`Category Name`, `Department Name`, `Product Name`).
- Cantidad, precio, descuento, ventas y total de la línea (`Order Item Quantity`, `Order Item Product Price`, `Order Item Discount`, `Order Item Discount Rate`, `Sales`, `Order Item Total`).

### Cuánto aporta cada columna sin fuga

| Modelo o regla (prueba, periodo 2017-01-08 a 2017-09-30) | AUC | Observación |
|---|---:|---|
| Solo días programados (como puntaje) | 0,713 | Ordena peor que el modo |
| Solo modo de envío (tasa de entrenamiento como puntaje) | 0,740 | Igual que GBM con modo y días programados |
| GBM con modo y días programados | 0,740 | PR-AUC 0,780 |
| GBM con todas las columnas de la sección anterior | 0,736 | PR-AUC 0,828 |
| GBM con columnas de pedido y `Order Status` (aleatorio, no temporal) | 0,780 | Accuracy 0,716. Ver la sección "Trabajos previos" |

**Conclusión**: sin columnas posteriores, la señal la lleva el modo de envío. Región, producto, cliente y montos no mejoran la predicción en esta prueba.

### Cómo se vería un modelo con columnas que delatan el resultado

- Regla directa con `Delivery Status`: accuracy 1,000 y AUC 1,000.
- GBM con `Delivery Status` y `Days for shipping (real)`: AUC 1,000 en prueba (`s07`, modelo B).
- GBM con modo de envío y días reales, sin la columna de estado: AUC 1,000 en prueba (`s10`).
- GBM con modo, días programados y la diferencia entre envío y pedido: AUC 0,9985 en prueba (`s11`).

Un modelo así no sirve para ninguna decisión: la "predicción" ya contiene la respuesta.

---

## Series de tiempo y validación temporal

### Balance por mes

Tasa de tarde por mes, sobre todas las filas (incluye cancelados, que cuentan como no tarde). [Verificado con `s04`]

| Mes | Filas | % tarde | Mes | Filas | % tarde |
|---|---:|---:|---|---:|---:|
| 2015-01 | 5.322 | 54,11 | 2016-07 | 5.305 | 51,93 |
| 2015-02 | 4.729 | 54,85 | 2016-08 | 5.334 | 55,27 |
| 2015-03 | 5.362 | 54,76 | 2016-09 | 5.160 | 54,98 |
| 2015-04 | 5.126 | 53,84 | 2016-10 | 5.398 | 54,78 |
| 2015-05 | 5.357 | 55,09 | 2016-11 | 5.210 | 54,63 |
| 2015-06 | 5.134 | 54,11 | 2016-12 | 5.269 | 56,06 |
| 2015-07 | 5.299 | 55,46 | 2017-01 | 5.217 | 53,61 |
| 2015-08 | 5.273 | 55,68 | 2017-02 | 4.906 | 54,67 |
| 2015-09 | 5.140 | 56,69 | 2017-03 | 5.347 | 55,26 |
| 2015-10 | 5.302 | 54,81 | 2017-04 | 5.212 | 53,78 |
| 2015-11 | 5.235 | 54,27 | 2017-05 | 5.317 | 53,47 |
| 2015-12 | 5.371 | 54,68 | 2017-06 | 4.951 | 53,50 |
| 2016-01 | 5.317 | 55,58 | 2017-07 | 5.318 | 54,78 |
| 2016-02 | 4.894 | 54,15 | 2017-08 | 5.305 | 56,44 |
| 2016-03 | 5.210 | 55,76 | 2017-09 | 5.189 | 54,23 |
| 2016-04 | 5.097 | 55,23 | 2017-10 | 2.255 | 53,66 |
| 2016-05 | 5.302 | 55,66 | 2017-11 | 2.055 | 55,09 |
| 2016-06 | 5.054 | 56,85 | 2017-12 | 2.124 | 55,18 |
| | | | 2018-01 | 2.123 | 56,29 |

- No hay meses sin datos entre 2015-01 y 2018-01. [Verificado]
- La tasa es estable: entre 51,9 % y 56,9 % antes de octubre de 2017; entre 53,7 % y 56,3 % después. No hay un cambio de nivel en el objetivo, aunque sí en el volumen y la forma de los pedidos. [Verificado]

### Quiebre de régimen en octubre de 2017

- Antes de 2017-10: 171.962 filas, 57.349 pedidos, 12.330 clientes, 2,999 líneas por pedido. [Verificado]
- Desde 2017-10: 8.557 filas, 8.403 pedidos, 8.403 clientes, 1,018 líneas por pedido. Cada cliente aparece una sola vez. [Verificado]
- Clientes que aparecen en los dos periodos: 81. [Verificado]
- Causa no conocida. Puede ser un cambio en la forma de generar o extraer los datos, o una muestra distinta. [Inferido]

### Propuesta de partición

Se usa la fecha del pedido. Se excluyen los envíos cancelados (`Shipping canceled`). Entre bloques hay una purga de 7 días: los días de envío llegan hasta 6, así que la etiqueta de un pedido queda cerrada en ese plazo. [Verificado]

| Bloque | Desde | Hasta | Filas | Pedidos | Clientes | % tarde |
|---|---|---|---:|---:|---:|---:|
| Entrenamiento | 2015-01-01 | 2016-06-30 | 89.531 | 29.894 | 11.343 | 57,55 % |
| Purga (no se usa) | 2016-07-01 a 07-07 y 2017-01-01 a 01-07 | | 2.244 | 752 | 729 | 55,08 % |
| Validación | 2016-07-08 | 2016-12-31 | 29.199 | 9.681 | 6.801 | 57,03 % |
| Prueba | 2017-01-08 | 2017-09-30 | 43.598 | 14.527 | 8.623 | 57,02 % |
| Tramo posterior al cambio (solo se reporta) | 2017-10-01 | 2018-01-31 | 8.193 | 8.043 | 8.043 | 57,48 % |

- Total: 172.765 filas para la tarea (89.531 + 29.199 + 43.598 + 8.193 + las purgas) y 7.754 canceladas, que suman las 180.519. [Verificado]
- Duración: entrenamiento 17,9 meses; validación 5,8; prueba 8,7; tramo posterior 4,0. [Verificado]
- Clientes de prueba que ya aparecen en entrenamiento: 7.872 de 8.623 (91 %). Si el modelo usa columnas del cliente, conviene verificarlo. [Verificado]
- **Por qué no usar el tramo posterior para prueba**: cambia la forma de los datos (una línea por pedido, un pedido por cliente). Un resultado ahí no se compara con el resto. Se reporta por separado. [Inferido]

---

## Líneas base

Todas las reglas se evalúan con la misma definición de etiqueta y sobre los mismos bloques. Las tasas de entrenamiento se calculan solo con el bloque de entrenamiento. [Verificado con `s06`]

Tasa de entrenamiento por modo: First Class 1,000; Second Class 0,804; Same Day 0,470; Standard Class 0,399.

| Regla | Val: % marcado | Val: precisión | Val: recall | Prueba: % marcado | Prueba: precisión | Prueba: recall |
|---|---:|---:|---:|---:|---:|---:|
| B0. Todos tarde | 100,00 | 0,570 | 1,000 | 100,00 | 0,570 | 1,000 |
| B1. Solo First Class | 16,13 | 1,000 | 0,283 | 14,97 | 1,000 | 0,263 |
| B2. Tasa de entrenamiento por modo, si es al menos 0,5 (First y Second) | 35,01 | 0,889 | 0,546 | 34,42 | 0,882 | 0,532 |
| B3. Días programados de 2 o menos | 40,55 | 0,826 | 0,587 | 39,87 | 0,833 | 0,583 |
| B4. Control negativo: solo Standard Class | 59,45 | 0,396 | 0,413 | 60,13 | 0,396 | 0,417 |

Lectura:

- **B2** es la línea base que el modelo debe superar. Con la precisión de B2 (0,88), la mayoría de los pedidos marcados llega tarde; el problema es el recall, que queda en 0,53.
- **B1** es la regla más precisa, pero solo recupera 26 % de los pedidos tarde.
- **B4** queda por debajo de la tasa base (0,40 frente a 0,57): confirma que el modo permite separar en ambos sentidos.
- En el tramo posterior al cambio, las mismas reglas dan resultados casi iguales (precisión de B2 0,889; recall 0,540). Eso sugiere que la relación entre modo y demora no cambió, aunque el formato de los datos sí. [Verificado]

---

## Umbral por costo

### Qué fuentes hay

- **No hay una fuente abierta** que dé el costo de un pedido tarde o de una alerta para un caso como este. El dataset no trae costos de intervención ni de devolución. [Verificado]
- **Sendcloud** (proveedor de envíos, encuesta propia 2025, 8.000 consumidores en 8 países europeos): 31,9 % de los clientes dice que una entrega tarde erosiona la confianza; en España, 42,3 %. La página no describe la metodología ni define la medida de "dejar de comprar". Es evidencia de intención, no de comportamiento. [Verificado en la página; fuente de proveedor]
- **Experimento de escenarios** publicado en el *International Journal of Logistics Management* (DOI 10.1108/IJLM-05-2016-0130): más tiempo de entrega aumenta el riesgo percibido y reduce la satisfacción y la intención de compra. Es un experimento, no datos de una tienda real. [Fuente secundaria]
- **Costo de atención**: un proveedor de logística cita entre 3 y 15 dólares por ticket de soporte, y dice que las consultas de seguimiento son entre 25 % y 40 % del total. Es un dato de proveedor sin fuente primaria. [Fuente secundaria]

**Conclusión**: no hay una cifra defendible para el costo de un retraso. El proyecto debe declarar supuestos y mostrar la sensibilidad a esos supuestos.

### Cómo se elegiría el umbral

Una alerta vale la pena cuando el costo esperado de no actuar supera el costo de actuar:

> Marcar el pedido si `p × c_retraso × e ≥ c_alerta`, es decir, si `p ≥ c_alerta / (c_retraso × e)`.

- `p`: probabilidad de retraso que da el modelo, calibrada.
- `c_retraso`: costo de un pedido tarde (supuesto).
- `c_alerta`: costo de intervenir un pedido (supuesto).
- `e`: fracción de los retrasos que la intervención evita (supuesto).

Ejemplo ilustrativo, no fuente: si intervenir cuesta 10, la intervención evita la mitad de los retrasos y un retraso cuesta 60, el umbral es 10 / (60 × 0,5) = 0,33.

**Procedimiento propuesto**:

1. Fijar `c_alerta`, `c_retraso` y `e` como supuestos declarados por escrito antes de mirar el bloque de prueba.
2. Calibrar `p` en validación (por ejemplo, regresión isotónica o de Platt) con el modelo entrenado en entrenamiento.
3. Elegir el umbral que maximiza el beneficio neto esperado en validación.
4. Congelar el umbral y aplicarlo una sola vez a prueba.
5. Mostrar la sensibilidad a `c_retraso` y `c_alerta` en prueba como reporte, sin usarla para elegir.

**No usar F1 para elegir el umbral.** En validación, el F1 máximo está en 0,27, y ese umbral marca el 99,9 % de los pedidos (recall 0,999, precisión 0,570). Con 57 % de tardes, F1 premia marcar casi todo. [Verificado en `s07`]

---

## Datos personales

| Columna | Qué contiene | Estado | Regla para las salidas |
|---|---|---|---|
| `Customer Email` | Correo | Enmascarada: `XXXXXXXXX` en las 180.519 filas | No publicar |
| `Customer Password` | Clave | Enmascarada: `XXXXXXXXX` en las 180.519 filas | No publicar |
| `Customer Fname` | Nombre | Parece generado: 782 nombres; "Mary" en 65.150 filas | No publicar |
| `Customer Lname` | Apellido | Parece generado: 1.110 apellidos; "Smith" en 64.104 filas | No publicar |
| `Customer Street` | Calle y número | Parece generado: 7.458 direcciones distintas, todas con número | No publicar |
| `Customer Zipcode`, `Order Zipcode` | Código postal | Real en apariencia; cuasi identificador | No publicar a nivel de fila; solo agregados |
| `Customer City`, `Order City`, `Customer State`, `Order State` | Ciudad y estado | Cuasi identificador | Solo agregados, con tamaño mínimo por celda |
| `Latitude`, `Longitude` | Ubicación de la tienda | Coordenadas | No publicar a nivel de fila |
| `Customer Id`, `Order Customer Id` | Seudónimo del cliente | Único por cliente, se repite en los pedidos | No publicar; no usar como feature |

[Verificado: columnas y conteos con `s05` y `s12`. Que "parece generado" es una observación, no una comprobación. [Inferido]]

**Regla para el proyecto**: ningún nombre, calle, código postal, coordenada ni identificador de cliente aparece en el README, en la documentación, en el sitio, en el tablero ni en los registros. Las salidas publicadas son agregados por mercado, región, modo de envío y producto, y no se muestran celdas con pocos pedidos. El umbral del tamaño mínimo se fija en el plan.

Nota: la fuente declara la licencia CC BY 4.0. La atribución no exime de la regla anterior: el riesgo de reidentificación no depende de la licencia.

---

## Primera mirada del modelo

Primera mirada con las columnas que se conocen al crear el pedido, con la partición propuesta. **No es el resultado del proyecto.** Se usó `HistGradientBoostingClassifier` de scikit-learn, con `max_iter=300`, `learning_rate=0.05`, `max_leaf_nodes=31`, `min_samples_leaf=50` y `random_state=0`. Las categorías se pasan con `categorical_features="from_dtype"`. [Verificado con `s07`]

Columnas del modelo: `Type`, `Shipping Mode`, `Market`, `Order Region`, `Order Country`, `Customer Segment`, `Customer Country`, `Category Name`, `Department Name`, `Product Name`, `Days for shipment (scheduled)`, `Order Item Quantity`, `Order Item Product Price`, `Order Item Discount`, `Order Item Discount Rate`, `Sales`, `Order Item Total`.

Resultados en prueba (2017-01-08 a 2017-09-30, 43.598 filas):

| Modelo | AUC | PR-AUC | Brier | Umbral | % marcado | Precisión | Recall |
|---|---:|---:|---:|---:|---:|---:|---:|
| A. GBM, columnas conocidas al crear el pedido | 0,736 | 0,828 | 0,192 | 0,50 | 36,3 % | 0,862 | 0,549 |
| A con umbral por F1 en validación | 0,736 | 0,828 | 0,192 | 0,27 | 99,9 % | 0,570 | 0,999 |
| GBM solo con modo y días programados | 0,740 | 0,780 | — | — | — | — | — |
| Regla por tasa de modo (sin modelo) | 0,740 | 0,780 | — | 0,50 | 34,4 % | 0,882 | 0,532 |
| B. GBM con `Delivery Status` y días reales (fuga) | 1,000 | 1,000 | 0,000 | 0,50 | 57,0 % | 1,000 | 1,000 |

AUC en validación del modelo A: 0,737.

Sensibilidad del modelo A al umbral, en prueba:

| Umbral | % marcado | Precisión | Recall |
|---:|---:|---:|---:|
| 0,3 | 99,6 % | 0,571 | 0,997 |
| 0,4 | 71,5 % | 0,639 | 0,800 |
| 0,5 | 36,3 % | 0,862 | 0,549 |
| 0,6 | 34,8 % | 0,877 | 0,535 |
| 0,7 | 34,0 % | 0,881 | 0,526 |
| 0,8 | 24,7 % | 0,914 | 0,395 |

Lectura:

- Entre 0,5 y 0,7, el porcentaje marcado casi no cambia (34 % a 36 %). Eso coincide con B2: el modelo, en la práctica, decide qué modos marcar.
- El modelo no supera a B2 en precisión ni en recall de forma clara en el umbral 0,5 (precisión 0,862 frente a 0,882; recall 0,549 frente a 0,532). La diferencia es pequeña y cabe dentro del ruido de una sola corrida.
- Con las mismas columnas, una partición aleatoria da AUC 0,751 y accuracy 0,700 (`s09`). El resultado no depende de la partición.
- Con la columna `Order Status`, la partición aleatoria da AUC 0,780 y accuracy 0,716 (`s09`).

**Lo que esto implica**: el resultado de una primera pasada es que, con las columnas de pedido, el modelo apenas agrega sobre una regla. El proyecto debe decidir si presenta esa conclusión como resultado o si busca columnas nuevas (no disponibles en este dataset).

---

## Trabajos previos

| Trabajo | Qué reporta | Fuga | Estado |
|---|---|---|---|
| Hugging Face, `yash441alt/dataco-supply-chain-delay-prediction`: https://huggingface.co/yash441alt/dataco-supply-chain-delay-prediction | XGBoost con precisión 77,44 % y ROC-AUC 89,22 %. Dice haber excluido "post-shipment features". Las importancias más altas son días programados (36,6 %) y modo de envío (33,6 %). No dice la partición ni el número de filas | Dice que no. Con las columnas de pedido no se llega a AUC 0,89: 0,751 con partición aleatoria y 0,736 con partición temporal. El resultado solo se alcanza con columnas posteriores | Verificado en la página. No se reprodujo; la hipótesis de fuga es la más plausible, pero no está confirmada |
| Hugging Face, `idoyaaran/dataco-supply-chain-model`: https://huggingface.co/idoyaaran/dataco-supply-chain-model | XGBoost con precisión 73,43 % y ROC-AUC 83,62 % "después de quitar las columnas posteriores al envío" | No verificable sin la página completa | Fuente secundaria (fragmento) |
| Research Square, rs-10027505: https://www.researchsquare.com/article/rs-10027505 | Según el fragmento de búsqueda, marca como positivos los pedidos "Late Delivery" o "Shipping Canceled" | No verificable | No se pudo leer el contenido |
| Research Square, rs-11019500: https://www.researchsquare.com/article/rs-11019500 | Según el título, "Predicting Late Delivery Risk in Global Supply Chain Operations Using Machine Learning"; según el fragmento, XGBoost con despliegue en Streamlit | No verificable | No se pudo leer el contenido |
| Universidad de Ulster: https://pure.ulster.ac.uk/en/publications/supply-chain-analytics-a-performance-evaluation-of-machine-learni/ | Compara Random Forest, XGBoost y Gradient Boost para retraso; usa series de tiempo para la duración del envío | No verificable | Fuente secundaria (fragmento) |
| DEV Community: https://dev.to/sugnikm/how-i-built-a-late-delivery-risk-predictor-for-apl-logistics-what-a-95-delay-rate-in-first-class-1d11 | Advierte que `Delivery Status` es casi una copia del objetivo y debe excluirse | Confirmado con datos propios: coincide al 100 % | Fuente secundaria (fragmento) |

**Conclusión sobre las precisiones altas**: en las fuentes con cifras, la única forma de llegar a AUC 0,89 con estas columnas es incluir algo que se conoce después del envío. No se pudo confirmar en ninguna fuente. Para el proyecto, cualquier cifra de la literatura debe citarse con la partición, la definición de etiqueta y la lista de columnas. [Inferido]

---

## Librerías: lo que se instaló y ejecutó

| Paquete | Versión | Uso | Resultado |
|---|---|---|---|
| Python | 3.11.1 | Entorno de investigación (`py -3.11 -m venv`) | Instalado. Verificado: `python -I -c` |
| pandas | 3.1.1 | Lectura, perfil, particiones y reglas | Ejecutado sin errores |
| NumPy | 2.4.6 | Dependencia; cálculos de correlación | Ejecutado sin errores |
| scikit-learn | 1.9.1 | `HistGradientBoostingClassifier` y métricas (AUC, PR-AUC, Brier, precisión, recall, F1) | Ejecutado: modelo A, modelo B, partición aleatoria, modelo con modo y días |
| pyarrow | 26.0.1 | Instalado para mantener la pila de P1 y P2 | No se usó en esta investigación |

- Se evitó instalar LightGBM, SciPy y PyMC: la tarea pide gradient boosting y scikit-learn lo cubre. [Verificado]
- Los scripts están en `scratchpad\p3-research-scripts\`. El entorno se creó con la instalación `pip install -q pandas pyarrow scikit-learn`. [Verificado]
- Advertencias de instalación: ninguna relevante en el log de instalación. [Verificado con `pip_install.log`]

---

## Tecnologías: landing y librerías nuevas

Tecnologías que el landing declara (`src/i18n/en.ts` y `src/i18n/es.ts`): Power BI, Looker Studio; Apache Airflow, n8n, Power Automate; FastAPI, Docker, RAG, TensorFlow, Edge AI; Python, dbt, SQL, BigQuery, pandas, scikit-learn; SAP, ERPs, REST APIs, Azure, Google Cloud. [Verificado]

| Herramienta que el proyecto usaría | En el landing | Observación |
|---|---|---|
| Python | Sí | |
| pandas | Sí | |
| scikit-learn | Sí | `HistGradientBoostingClassifier`, sin librerías nuevas |
| SQL | Sí | Para las capas de datos |
| dbt y BigQuery | Sí | Solo si se usa la misma ruta que P1 y P2. SQLite es el reemplazo local ya anotado en `LANDING-EXPANSION.md` |
| Power BI | Sí | Para el tablero; si no se puede abrir la herramienta, el diseño va en HTML (S4) |
| Apache Airflow y Docker | Sí | Para el DAG de la corrida mensual, si se decide hacerla |
| pyarrow, pytest, matplotlib, NumPy, SQLite | No, pero ya están en `LANDING-EXPANSION.md` | Anotadas por P1 y P2; no hay tecnología nueva que anotar |

**Conclusión**: el proyecto no necesita tecnología que el landing no declare, salvo las que ya están anotadas en `LANDING-EXPANSION.md`. No hay que añadir nada a esa lista por este proyecto.

---

## Contradicciones con el sitio

Texto de `src/i18n/projects/late-deliveries.ts` (es y en). Cada fila compara el texto con los datos y propone una redacción. [Verificado]

| Texto del sitio | Lo que muestran los datos | Redacción sugerida |
|---|---|---|
| Lede: "con su modo de envío, su región y los días que tardó de verdad, para marcar al despachar" | Los días reales son la etiqueta, no una entrada del modelo. El modelo no puede usarlos. La región no mejora la predicción | "con su modo de envío y sus días programados, para marcar al crear el pedido los que probablemente lleguen tarde" |
| Algoritmo: "combina modo de envío, región, producto y días programados" | El modo de envío y los días programados explican la señal. El modelo con todas las columnas de pedido (AUC 0,736) no supera a la regla por modo (0,740) | "El modo de envío concentra la señal; el modelo se compara con esa regla y la mejora solo si el umbral lo justifica" |
| Datos: "2 campos que delatan el resultado" | Son tres columnas posteriores al envío (`Days for shipping (real)`, `Delivery Status`, `shipping date`) y una de estado (`Order Status`) | "tres campos y un estado delatan el resultado; el proyecto los excluye y muestra el efecto de cada uno" |
| Datos: "Estado de entrega, fuera del modelo" | Es la columna que coincide con el objetivo en 100 % | Mantener, y agregar `Order Status` a la lista de excluidas |
| Producto: "Cada pedido despachado recibe un riesgo de demora" | La columna de despacho contiene los días reales. El dataset no permite validar el momento de despacho | "Cada pedido recibe un riesgo al crearse, con los datos que ya existen en ese momento" |
| Algoritmo: "Entrena con los pedidos más viejos y prueba con los más nuevos" | Los meses desde octubre de 2017 tienen otra forma (una línea por pedido, un pedido por cliente). Probar ahí no es comparable | "Prueba con los meses de 2017 anteriores a un cambio en la forma de los datos y reporta el último tramo por separado" |
| Algoritmo: "El punto de corte sale de lo que cuesta intervenir un pedido y de lo que cuesta no hacerlo" | No hay costos en el dataset. El umbral depende de supuestos declarados | "El punto de corte sale de costos supuestos que el proyecto declara y que pueden cambiarse" |
| Resumen: "180 mil líneas de pedido" | Cierto: 180.519 líneas; 65.752 pedidos; de 2015-01 a 2018-01. Para la tarea quedan 172.765 (sin cancelados) | Mantener, y agregar "de 2015 a 2018; 172 mil para la tarea, sin cancelados" |
| Datos: "Días de envío reales, solo para la etiqueta" | Correcto | Mantener |
| Cita: "Constante, Silva y Pereira, en Mendeley Data" | Correcto: "Constante, Fabian; Silva, Fernando; Pereira, António (2019)" | Mantener; en el README, usar la cita completa |
| Licencia "CC BY 4.0" | Correcto (JSON-LD de la página) | Mantener |

---

## Riesgos y huecos

- **Quiebre de régimen de octubre de 2017**: no se conoce la causa. Puede ser una muestra distinta o un cambio en la extracción. Afecta la representatividad del periodo posterior. [Inferido]
- **Autenticidad del dataset**: los nombres y las calles parecen generados, y la distribución es muy concentrada (el nombre "Mary" aparece en 65.150 filas; el par nombre y apellido más frecuente, en 23.884). No se pudo confirmar que sean datos reales. Los resultados deben presentarse como práctica con datos públicos, no como un hallazgo de negocio. [Inferido]
- **Versión fija**: la v5 es de 2019. Si Mendeley publica una versión nueva, el proyecto debe fijarse a la v5 y declararlo. [Verificado: la página indica v5]
- **Definición de la etiqueta**: depende de la comparación de días; los cancelados quedan fuera. Otro proyecto podría definir la etiqueta de otra forma y obtener cifras distintas. [Verificado]
- **Columnas no verificadas**: `Benefit per order`, `Order Profit Per Order` y `Order Item Profit Ratio` no se sabe cuándo se calculan. Se excluyen por precaución, y eso debe quedar escrito. [Inferido]
- **Costos**: no hay una cifra defendible. El umbral depende de supuestos. [Verificado]
- **Trabajos previos no reproducidos**: la cifra de AUC 0,89 no se pudo reproducir. Puede haber una fuga no documentada. [Verificado con limitaciones]
- **Archivo de clickstream**: `tokenized_access_logs.csv` no se usó. Podría aportar señales posteriores al pedido, por eso se deja fuera.

---

## Preguntas abiertas para el plan

1. **Momento de la predicción**: al crear el pedido (propuesto), porque es lo que el dataset permite validar. Al despachar no se puede validar con estos datos.
2. **Envíos cancelados**: excluirlos de la tarea (propuesto) o modelarlos como clase aparte.
3. **Columnas de rentabilidad**: excluirlas por precaución (propuesto) hasta confirmar cuándo se calculan.
4. **Partición**: entrenamiento 2015-01-01 a 2016-06-30; validación 2016-07-08 a 2016-12-31; prueba 2017-01-08 a 2017-09-30; tramo posterior solo como reporte (propuesto).
5. **Línea base a superar**: B2 (tasa por modo) como mínimo; B1 y B3 como referencias.
6. **Costos del umbral**: definir `c_alerta`, `c_retraso` y `e` como supuestos declarados antes de mirar prueba. Hace falta una decisión del proyecto sobre estos valores.
7. **Métrica de selección del umbral**: beneficio neto esperado en validación, no F1.
8. **Modelo**: `HistGradientBoostingClassifier` de scikit-learn (propuesto), sin LightGBM.
9. **Publicación**: solo agregados por mercado, región, modo y producto, con un tamaño mínimo por celda que el plan fija.

---

## Resumen para el plan

Cada decisión que el plan debe tomar, con la evidencia de esta investigación.

- **Momento de la predicción: al crear el pedido.** El despacho ya contiene la demora (AUC 0,9985 con el modo y la diferencia de fechas). Ver "Fuga de información".
- **Etiqueta: `real > programado`, sin cancelados.** Coincide al 100 % en los 172.765 pedidos no cancelados. Excluir las 7.754 filas `Shipping canceled`. Ver "Perfil de calidad de datos".
- **Columnas excluidas**: `Delivery Status`, `Days for shipping (real)`, `shipping date`, `Order Status`, `Late_delivery_risk` (objetivo), las columnas de rentabilidad (hasta confirmar) y todas las personales. Ver "Fuga de información" y "Datos personales".
- **Partición**: entrenamiento 89.531 filas; validación 29.199; prueba 43.598; purga de 7 días; tramo posterior de 8.193 filas solo como reporte. Ver "Series de tiempo".
- **Línea base a superar**: B2 (tasa por modo), precisión 0,88 y recall 0,53 en prueba. El modelo de primera mirada no la supera de forma clara. Ver "Líneas base" y "Primera mirada del modelo".
- **Modelo**: `HistGradientBoostingClassifier`; comparar siempre con el modelo solo de modo. Ver "Primera mirada".
- **Umbral**: costos supuestos y declarados; elegir en validación por beneficio neto; nunca por F1 (F1 eligió un umbral que marca el 99,9 % de los pedidos). Ver "Umbral por costo".
- **Datos personales**: no publicar nombres, calles, códigos postales, coordenadas ni identificadores; solo agregados con tamaño mínimo. Ver "Datos personales".
- **Sitio**: cambiar cinco frases del texto (región y producto, "2 campos", "al despachar", "los más viejos y los más nuevos", costos). Ver "Contradicciones con el sitio".
- **Tecnología**: nada que el landing no declare; lo que falta ya está en `LANDING-EXPANSION.md`. Ver "Tecnologías".

---

## Salidas guardadas

Todas están fuera del repositorio:

- Descargas y derivados: `scratchpad\p3-data\` (`DataCoSupplyChainDataset.csv`, `DescriptionDataCoSupplyChain.csv`, `p3-meta-v5.json`, `p3-files.json`, `p3-page.html`, `p3-bundle.js`, `work_profile.pkl`, `work_elig.pkl`).
- Scripts y salidas: `scratchpad\p3-research-scripts\` (`s01` a `s14` en `.py` y `.out`; `s04_monthly.csv`; `s06_baselines.csv`; `s07_model_A.csv`; `s07_model_leak.csv`).
- Entorno: `scratchpad\p3-research-venv\` (Python 3.11.1, pandas 3.1.1, scikit-learn 1.9.1, pyarrow 26.0.1, NumPy 2.4.6).
