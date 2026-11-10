# Registro de decisiones

Una entrada por decisión, con sus siete campos: Contexto, Opciones, Decisión, Motivo, Afecta, Quién y Estado. Donde había un solo camino razonable, «Opciones» lo dice y explica por qué. Las propuestas del agente de planificación (Opus) quedan para revisión del dueño; las del dueño citan el registro del documento de seguimiento (`odd/tasks/portfolio-projects.md`). Las cifras salen de `research.md` y de `requirements.md`, sección 4; los scripts de las comprobaciones del agente de planificación quedaron fuera del repositorio, en una carpeta temporal, y solo leyeron conteos de la validación y de la prueba: ninguna métrica de modelo fuera del bloque de entrenamiento.

Estados: **vigente**, **pendiente del dueño**, **reemplazada por Dxx**.

---

## D01 Enfoque: un clasificador comparado con reglas simples, con criterios que pueden no cumplirse

- **Contexto**: el sitio promete un clasificador de gradient boosting que marca los pedidos que van a llegar tarde, con validación por tiempo y un umbral por costo (S9). research.md encontró que, con las columnas del pedido, su primera mirada (AUC 0,736 en la prueba) no supera a la tasa de retraso por modo de envío (0,740), y que los trabajos publicados con AUC de hasta 0,89 usan columnas que solo se conocen después del envío.
- **Opciones**: (a) solo el clasificador, informando su AUC; (b) el clasificador comparado con reglas simples (B0-B5) y con criterios fijados antes de construir, que pueden no cumplirse; (c) solo reglas, sin modelo.
- **Decisión**: (b). `HistGradientBoostingClassifier` frente a B0-B5 (D15), con los criterios C01-C10 de `requirements.md`, sección 8.1 (D16). Un resultado desfavorable se informa tal cual, como el proyecto 02 informó sus C02 y C05.
- **Motivo**: sin una regla al lado, un AUC no dice si el modelo suma algo; la investigación sugiere que puede no sumar nada sobre una regla de una variable, y esa comparación es el resultado del proyecto. (a) repetiría el error de los trabajos publicados de informar un número sin referencia; (c) dejaría afuera lo que el sitio promete.
- **Afecta**: todo el proyecto; `requirements.md`, sección 8.1; `fact_evaluation_summary`; el README y la página del sitio.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D02 Fuente: un archivo por HTTPS desde Mendeley, huella fijada, redirecciones solo a hosts permitidos y sin archivos comprimidos

- **Contexto**: la versión 5 del dataset es de 2019 y no cambia. La URL de cada archivo responde 302 a un enlace firmado de Amazon S3 que caduca; esa respuesta no trae `ETag`; la de S3 sí, igual al MD5. La API de archivos de Mendeley publica el SHA-256 (`fa6d…0aa6`), que coincide con el calculado. Mendeley también ofrece un zip de todo el dataset, que trae además el clickstream (95 MB, fuera del alcance).
- **Opciones**: (a) descargar el zip del dataset y extraer el CSV; (b) descargar solo el CSV por su URL, seguir las redirecciones solo a hosts HTTPS permitidos y comparar con la huella fijada; (c) descargar cada mes y comparar la huella; (d) no verificar.
- **Decisión**: (b), una vez. `source.accepted_sha256` fijado; `allowed_hosts = ["data.mendeley.com", ".amazonaws.com"]` (nombre exacto o sufijo); a lo sumo 3 redirecciones; tope de 128 MiB; `.part` con SHA-256 al vuelo y `os.replace` solo si coincide. En modo `local` se registra la primera huella y las siguientes deben coincidir. Si la URL del archivo desaparece y solo queda el zip (runbook R11), usarlo exige la lista blanca de miembros del proyecto 02 (un solo miembro esperado, sin rutas, con tope descomprimido) y una decisión nueva. **[Verificar en U04]** el nombre del host de S3 al que redirige hoy.
- **Motivo**: hay un valor de confianza (la huella publicada por Mendeley), así que la huella sí verifica la descarga; los encabezados no sirven porque el enlace firmado caduca. (a) suma una superficie de extracción (rutas en los miembros, archivos que se inflan) sin ninguna ganancia; (c) bajaría 96 MB por mes para descubrir que no cambió; (d) dejaría entrar un archivo distinto en silencio. El control de las redirecciones existe porque la primera respuesta manda a otro host.
- **Afecta**: `ingest.py`; `config/pipeline.toml [source]`; V01, V02; runbook R1, R2 y R11.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D03 Momento de la predicción: al crear el pedido

- **Contexto**: el sitio dice «para marcar al despachar». research.md midió que la fecha de envío menos la fecha del pedido coincide con los días reales en el 97 % de las filas, y que con el modo y esa diferencia el AUC en la prueba es 0,9985. El agente de planificación midió en el entrenamiento que la fecha de envío es la del pedido más exactamente 12 h (Same Day), 48 h (First Class) o de 48 a 144 h en pasos de 24 h (Second y Standard Class), y que los días reales son la diferencia en días de calendario en el 100 % de los pedidos: la columna «de envío» es, en los hechos, la de llegada.
- **Opciones**: (a) al despachar, con la fecha de envío; (b) al despachar, sin esa fecha; (c) al crear el pedido.
- **Decisión**: (c). Cada pedido recibe su riesgo con lo que existe en el momento de crearlo.
- **Motivo**: (a) es una fuga: la columna ya contiene la demora; (b) no tiene ningún dato que distinga el momento del despacho del de la llegada; las columnas del pedido sí existen al crearlo y permiten validar ese momento.
- **Afecta**: `features.py`; el README; las filas 1, 5 y 14 de `SITE-UPDATES.md` (plan, sección 27).
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D04 Unidad: el pedido, no la línea

- **Contexto**: las cifras de research.md son por línea (180.519 líneas). El agente de planificación midió que las 20 columnas de pedido revisadas (incluidas la etiqueta, el modo, las fechas y el destino) tienen un solo valor dentro de cada pedido, que las líneas difieren solo en producto, descuento y rentabilidad, que antes de 2017-10 hay de 1 a 5 líneas por pedido en partes casi iguales y que desde 2017-10 casi todos los pedidos tienen una. Una alerta y su costo son por pedido.
- **Opciones**: (a) la línea como unidad, como la investigación; (b) entrenar por línea y agregar el puntaje por pedido (máximo o media); (c) el pedido como unidad, con las variables de sus líneas agregadas.
- **Decisión**: (c). Una fila por pedido, con la línea principal (la de mayores ventas; empate, menor `line_id`) para el producto, la categoría y el departamento, más el número de líneas, de departamentos, las unidades, las ventas, el total y el descuento. Las métricas, el beneficio neto y las alertas son por pedido.
- **Motivo**: por línea, un pedido de 5 líneas pesa 5 veces lo que uno de una, y el tramo posterior al cambio de forma (una línea por pedido) dejaría de ser comparable con el resto; (b) necesita una regla de agregación sin ganancia, porque la etiqueta es del pedido. El costo: las cifras de líneas base de la investigación (B2 con precisión 0,882, recall 0,532 y 34,4 % marcado) no son las del proyecto; el código las recalcula por pedido y la documentación lo dice.
- **Afecta**: `features.py`; `evaluation.py`; `fact_order`; los criterios; la fila 8 de `SITE-UPDATES.md`.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D05 Etiqueta y pedidos cancelados

- **Contexto**: en los pedidos no cancelados, `Late_delivery_risk` = 1 si y solo si los días reales superan a los programados (100 %, research.md). Hay 2.855 pedidos cancelados, completos (7.754 líneas), con `Order Status` `CANCELED` o `SUSPECTED_FRAUD`; en 4.423 de sus líneas los días reales superan a los programados sin etiqueta de tarde. Que un pedido se cancela se sabe después de crearlo.
- **Opciones**: para la tarea, (a) excluirlos del entrenamiento y de las métricas, (b) tratarlos como una clase propia, o (c) contarlos como «no tarde» (como la tabla mensual de research.md); para el producto, puntuarlos o no.
- **Decisión**: (a) para el entrenamiento y las métricas. En el producto, un pedido nuevo se puntúa sin saber que se va a cancelar, como en uso, y las alertas sobre pedidos que después se cancelaron se cuentan (I04, `alerts.json`) sin entrar en el beneficio neto.
- **Motivo**: un pedido cancelado no tiene tiempo de entrega: «tarde» no está definido para él; (c) mezclaría otro resultado en el objetivo (uno de los trabajos publicados, según su fragmento, marca «tarde o cancelado», sin poder verificarse); (b) es otro problema. Contar las alertas que se gastan en pedidos cancelados es lo honesto, porque un modelo en uso los recibe igual.
- **Afecta**: `stg_outcomes.is_late`; `mart_labelled_orders`; `tracking.py`; `alerts.json`; la ficha del modelo.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D06 Columnas excluidas por fuga y por precaución, con una guarda en el código

- **Contexto**: research.md: `Delivery Status` coincide con el objetivo en el 100 % de las filas; los días reales definen la etiqueta; la fecha de envío contiene los días reales; `Order Status` es el estado final (sus valores `CANCELED` y `SUSPECTED_FRAUD` son los cancelados) y sube el AUC de una partición aleatoria de 0,751 a 0,780; las tres columnas de rentabilidad no tienen momento documentado. El agente de planificación midió en el entrenamiento que, dentro de cada modo, las líneas tarde tienen un beneficio medio algo menor (Same Day 18,36 frente a 21,22; Standard Class 20,18 frente a 21,80) con la misma proporción de beneficio negativo (18,84 % frente a 18,75 %).
- **Opciones**: (a) excluir solo `Delivery Status`, como dice el sitio; (b) excluir las cuatro columnas posteriores al pedido y conservar la rentabilidad; (c) excluir las cuatro y la rentabilidad por precaución, con un flujo de resultados aparte en el código y pruebas que lo impidan.
- **Decisión**: (c). `LEAKING_COLUMNS` (8: el objetivo, `Delivery Status`, `Days for shipping (real)`, `shipping date (DateOrders)`, `Order Status`, `Benefit per order`, `Order Profit Per Order`, `Order Item Profit Ratio`) va solo a `outcomes`; `build_features` lanza `LeakageError` si recibe o produce una columna de esa lista o fuera de la lista permitida; una prueba estática impide que el código de las variables lea las tablas de resultados; una prueba de perturbación cambia todos los resultados y exige variables idénticas; M02 comprueba la matriz y M01 es una segunda línea (D36).
- **Motivo**: los «2 campos» del sitio son menos de los que delatan el resultado. La diferencia de rentabilidad es compatible con un cálculo posterior a la entrega, pero no lo prueba: se excluye hasta saber cuándo se calcula. Que la guarda esté en la estructura (dos flujos, una lista, una excepción) y no en una convención es lo que previene el tipo de defecto que tuvo el proyecto 01 (su bloqueante B1).
- **Afecta**: `schema.py`; `ingest.py`; `features.py`; `tests/unit/test_features.py`; `tests/integration/test_no_leak.py`; `leakage.json`; las filas 3 y 4 de `SITE-UPDATES.md`.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D07 Datos personales: se descartan en la lectura

- **Contexto**: correo y contraseña están enmascarados en todas las filas; nombres, apellidos y calles parecen generados, sin poder comprobarse; los códigos postales, las ciudades, los estados y las coordenadas son cuasi identificadores; el identificador del cliente es un seudónimo que se repite en los pedidos (research.md). La regla de la investigación: nada de eso en ninguna salida, solo agregados con un tamaño mínimo por celda que fija el plan. La licencia CC BY no exime de esa regla.
- **Opciones**: (a) guardarlos en SQLite y filtrarlos al exportar; (b) usarlos solo para las comprobaciones de la lectura que los necesitan (V06, V07) y descartarlos antes de escribir cualquier archivo; (c) reemplazarlos por un hash.
- **Decisión**: (b). Las 15 columnas de `PERSONAL_COLUMNS` no salen de `read_source`; V06 rechaza la fuente si el correo o la contraseña no están enmascarados; una prueba busca en cada byte de `data/interim/`, del almacén, de las salidas, los reportes, los estados y los registros los valores centinela del accesorio; los agregados del sitio y de las vistas previas del HTML ocultan las celdas con menos de 20 pedidos. Se conservan el país del cliente (2 valores), y el mercado, la región y el país del pedido, que no señalan a nadie a esa escala. No hay variables del cliente (D11).
- **Motivo**: un dato que no se guarda no se filtra; (a) deja el trabajo a cada exportador, registro y ejemplo de regla que venga después; (c) no protege nada, porque el dataset es público y el hash se revierte comparando con él. 20 pedidos por celda: con menos, una celda puede señalar a unos pocos clientes **[Supuesto: es un umbral habitual, no una norma]**.
- **Afecta**: `schema.py`; `ingest.py`; los ejemplos de `validate.py`; `site_export.py`; `dashboard/render.py`; AC04, AC05, AC26.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D08 Partición en el tiempo: purgas de 7 días, validación en dos mitades y el tramo posterior aparte

- **Contexto**: research.md propuso entrenamiento del 2015-01-01 al 2016-06-30, validación del 2016-07-08 al 2016-12-31, prueba del 2017-01-08 al 2017-09-30, purgas de 7 días y el tramo desde 2017-10 aparte, porque cambia de forma (una línea por pedido y un pedido por cliente). El agente de planificación midió que todo resultado se conoce a más tardar 6 días después del pedido, y contó los pedidos de cada bloque (requerimientos, sección 4).
- **Opciones**: (a) partición aleatoria (research.md: AUC 0,751); (b) la partición temporal de la investigación con una sola validación, donde se calibra y se elige el umbral sobre las mismas filas; (c) la misma partición con la validación en dos mitades: V1 para elegir y calibrar, V2 para el umbral y las cifras de validación. Para el tramo posterior: (i) como prueba; (ii) aparte.
- **Decisión**: (c) y (ii), con los bordes de `[split]`: V1 del 2016-07-08 al 2016-09-30 (4.649 pedidos) y V2 del 2016-10-01 al 2016-12-31 (5.032).
- **Motivo**: (a) no simula el uso; (b) elige el umbral sobre las filas en que se ajustó el calibrador, de forma optimista; separar V2 cuesta poco. Probar en el tramo posterior no sería comparable con el resto, porque cambia la forma de los datos. La purga de 7 días sale de la medición: con ella, cada etiqueta del entrenamiento se conoce antes del inicio de la validación y cada una de la validación antes del lanzamiento (V09 lo vigila en la fuente).
- **Afecta**: `split.py`; `config/pipeline.toml [split]`; AC15; C10.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D09 Refresco mensual simulado: lanzamiento, trece entregas y un cierre, en dos flujos

- **Contexto**: el sitio promete un modelo en uso; el archivo es estático; el proyecto 02 repitió sus últimos meses como entregas mensuales (su D07). Los resultados llegan hasta 6 días después de cada pedido.
- **Opciones**: (a) una sola evaluación, sin flujo; (b) una repetición mensual de los pedidos, con la etiqueta en las mismas filas; (c) una repetición mensual en dos flujos: los pedidos por su fecha de creación y los resultados por su fecha de conocimiento, con un lanzamiento al 2017-01-07, trece entregas mensuales y un cierre al 2018-02-07.
- **Decisión**: (c). El lanzamiento trae lo que se sabía al 2017-01-07; cada entrega mensual, los pedidos del mes y los resultados que llegaron en el mes; el cierre, solo resultados; una decimosexta corrida termina como `no_delivery`. La entrega la produce una tarea que hace de sistema de origen; en una empresa la reemplazaría la exportación del sistema de pedidos.
- **Motivo**: (b) metería la etiqueta de cada pedido en el almacén el día que se crea, que es justo la fuga que el proyecto evita; con (c), la ausencia de información futura es física: en el lanzamiento, las etiquetas de la prueba no están en el almacén. Además da al tablero lo que vería un equipo (alertas abiertas y, cada mes, las que se cerraron). (a) no muestra el producto.
- **Afecta**: `deliveries.py`; V20-V24; el DAG; `tracking.py`; AC09, AC14.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D10 El corte del mediodía en Same Day y la mecánica de la fuente

- **Contexto**: el agente de planificación midió en el entrenamiento que la fecha de envío es la del pedido más 12, 48 o de 48 a 144 horas según el modo, y que los días reales son la diferencia en días de calendario; así, los 853 pedidos Same Day creados antes de las 12:00 llegaron a tiempo y los 742 creados desde las 12:00 llegaron tarde, sin una excepción. En Second y Standard Class los días reales se reparten casi por igual entre 2 y 6, y ninguna otra columna da p < 0,05. El AUC dentro del entrenamiento pasa de 0,740 (tasa por modo) a 0,773 (tasa por modo y franja). research.md no tenía la hora del pedido en su modelo.
- **Opciones**: (a) dejar afuera la hora, como la investigación; (b) incluir las variables de calendario de la creación del pedido (hora, día de la semana, día del mes, mes) y dejar que V1 elija; (c) escribir el corte como una regla dentro del modelo.
- **Decisión**: (b), en el conjunto F2 (D11), y por separado B5 como línea base con la franja (D15). La documentación dice que los datos parecen generados (días reales uniformes dentro de cada modo, un corte sin excepciones) y que en una empresa el mediodía sería la hora de corte del despacho; los resultados son una práctica con datos públicos, no un hallazgo de negocio.
- **Motivo**: la hora existe al crear el pedido: dejarla afuera ignoraría una señal real. (c) fijaría en el modelo una regla vista en el entrenamiento; el modelo tiene que aprenderla del entrenamiento si está, y B5 mide si aprende algo más. Decir que los datos parecen generados es lo que la evidencia permite **[Inferido]**: research.md tampoco pudo confirmarlo.
- **Afecta**: `features.py` (F2); `baselines.py` (B5); C03 y C05; la ficha del modelo; las filas 1, 2 y 9 de `SITE-UPDATES.md`; el pendiente 5 del dueño.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D11 Conjuntos de variables F0, F1 y F2

- **Contexto**: la primera mirada de la investigación usó 17 columnas por línea; el pedido es la unidad (D04); la hora tiene señal en Same Day (D10); dentro de cada modo no se vio otra señal en el entrenamiento. Se podrían sumar variables de historia del cliente o tasas móviles de retraso.
- **Opciones**: (a) un solo conjunto fijado por quien planifica; (b) tres conjuntos anidados (F0: modo y días programados; F1: las columnas de la investigación llevadas al pedido; F2: F1 más el calendario de la creación) elegidos en V1; (c) sumar la historia del cliente (pedidos previos) y tasas móviles de retraso por modo; (d) sumar el nombre del producto como texto o la URL de su imagen.
- **Decisión**: (b), con las columnas del plan, sección 8; ni (c) ni (d).
- **Motivo**: elegir en V1 entre conjuntos anidados con una grilla fija es selección de modelo sin tocar la prueba. (c): el identificador del cliente es personal (D07), después del cambio de forma cada cliente aparece una vez (la historia sería 0 en casi todos), y las tasas móviles de resultados son un riesgo de fuga sin ganancia esperable (las tasas mensuales son estables). (d) repite `main_product`.
- **Afecta**: `features.py`; la selección; `algorithms.json` (`selection`).
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D12 Modelo: `HistGradientBoostingClassifier`, grilla fija y codificación explícita

- **Contexto**: el sitio dice gradient boosting con Python y scikit-learn. La investigación usó una configuración (300 iteraciones, tasa 0,05, 31 hojas, 50 muestras por hoja). LightGBM no está en el landing. Un ajuste sobre el entrenamiento tarda 0,72 s (medido).
- **Opciones**: (a) la configuración única de la investigación; (b) una grilla fija y chica (tasa 0,05 o 0,1; 15 o 31 hojas; 20 o 100 muestras por hoja; regularización L2 1,0; 300 iteraciones; sin parada temprana); (c) LightGBM o XGBoost; (d) regresión logística.
- **Decisión**: (b), con `OrdinalEncoder` explícito para las categóricas y la máscara `categorical_features`, `random_state = 0`.
- **Motivo**: scikit-learn está en el landing; (c) suma una tecnología sin un motivo que la investigación haya encontrado; (d) necesitaría escribir a mano las interacciones (modo × hora). Una grilla de ocho cuesta unos 20 s con los tres conjuntos; la parada temprana usa una partición aleatoria dentro del entrenamiento, que rompe el orden en el tiempo y la reproducibilidad. La codificación explícita fija el orden de las categorías del entrenamiento y el trato de una categoría nueva **[Verificar en U12]**.
- **Afecta**: `model.py`; `config/pipeline.toml [model]`.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D13 Selección y calibración en V1; umbral en V2

- **Contexto**: la investigación propuso calibrar en la validación, elegir el umbral que maximiza el beneficio neto en la validación y aplicarlo una vez en la prueba. scikit-learn 1.9.1 trae `sklearn.frozen.FrozenEstimator` (comprobado) para calibrar un modelo ya ajustado.
- **Opciones**: métrica de selección: AUC, pérdida logarítmica o beneficio neto; calibración: ninguna, sigmoide o isotónica; filas: toda la validación o una mitad.
- **Decisión**: se elige por la pérdida logarítmica en V1, sin calibrar; se calibra con regresión isotónica en V1 (`CalibratedClassifierCV(FrozenEstimator(pipe), method="isotonic")`); el umbral y las cifras de validación salen de V2.
- **Motivo**: la pérdida logarítmica es una regla de puntaje propia y premia la calidad de la probabilidad, que es lo que el umbral necesita; el AUC ignora la calibración; el beneficio neto depende de los costos supuestos. La isotónica sirve con pocos valores distintos y 4.649 pedidos. Separar V2 evita elegir el umbral sobre las filas del calibrador. El costo: V1 hace dos trabajos, aceptable porque la selección usa la pérdida sin calibrar.
- **Afecta**: `model.py`; `threshold.py`; el sello.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D14 Costos supuestos, umbral por beneficio neto y sensibilidad

- **Contexto**: ninguna fuente abierta da un costo defendible de un retraso o de una alerta (una encuesta de un proveedor, un costo de ticket de soporte de 3 a 15 dólares de otro, un experimento de escenarios; research.md). El ejemplo ilustrativo de la investigación (10, 60 y 0,5) da un punto de equilibrio de 0,33. F1 eligió en la validación un umbral de 0,27 que marca el 99,9 % de los pedidos. Las tasas del entrenamiento por modo son 1,000, 0,80, 0,47 y 0,40: cualquier punto de equilibrio por debajo de 0,40 marca casi todo, y uno entre 0,47 y 0,80 marca First y Second Class.
- **Opciones**: (a) los valores ilustrativos de la investigación; (b) un valor neutro por defecto, con punto de equilibrio 0,5 (alerta 5 u., retraso 20 u., efecto 0,5), y la sensibilidad de 0,10 a 0,90; (c) ningún valor por defecto, solo la curva; (d) F1 o el índice de Youden.
- **Decisión**: (b). El umbral es el que maximiza el beneficio neto en V2, en una grilla de 0,01 a 0,99 (en un empate, el mayor); la sensibilidad repite la elección para el modelo, B2 y B5 en cada uno de los 17 puntos de equilibrio y es parte del producto; los costos van en unidades genéricas («u.»), porque decide su cociente; la prueba se toca una vez con los valores por defecto.
- **Motivo**: con (a) la decisión es trivial y ningún modelo puede ayudar (todas las tasas por modo superan 0,33); 0,5 dice «actuar cuando es más probable que llegue tarde que a tiempo, con una intervención que cuesta un cuarto de un retraso y evita la mitad de su costo», una elección sin fuente que se declara como tal; (c) no le da al tablero una lista de alertas; (d) ignora los costos, y F1 marca casi todo. Lo que se sabía al elegir, dicho: con las cifras por línea de la investigación a 0,5 y sin la hora, el agente de planificación deduce un empate entre el modelo y B2 (131,4 frente a 131,5 u. por cada 100 líneas); el valor no se eligió por eso, y C04 lo dice.
- **Afecta**: `threshold.py`; `config/pipeline.toml [costs]`; C04, C05 y C09; `algorithms.json` (`cost_curve`, `threshold`); la página 4 del tablero; el pendiente 1 del dueño.
- **Quién**: agente de planificación; el dueño puede cambiar los valores antes de U25; después del sello, un cambio es un análisis nuevo (D17).
- **Estado**: vigente.

## D15 Líneas base B0-B5

- **Contexto**: research.md midió B0-B4 por línea y señaló B2 (la tasa por modo, marca si es ≥ 0,5) como la regla a superar. El agente de planificación encontró el corte del mediodía en el entrenamiento (D10).
- **Opciones**: (a) solo B2; (b) B0-B4, como la investigación; (c) B0-B4 más B5, la tasa del entrenamiento por modo y franja (antes o desde las 12:00).
- **Decisión**: (c), todas con tasas del entrenamiento; B2 y B5 también con su umbral elegido en V2 por el mismo procedimiento que el modelo, para C05 y C09.
- **Motivo**: el modelo tiene que superar la mejor regla simple conocida antes de mirar la prueba, no una regla débil elegida para perder. B5 es lo que vio el agente de planificación en el entrenamiento; si el modelo solo la iguala, la conclusión honesta es que una regla de dos variables hace el mismo trabajo.
- **Afecta**: `baselines.py`; C01-C09.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D16 Criterios C01-C10 y lo que se vio antes de fijarlos

- **Contexto**: la investigación vio en la prueba, por línea y sin la hora: AUC 0,736 y 0,740, PR-AUC 0,828 y 0,780, Brier 0,192 del modelo, y a 0,5 una precisión de 0,862 frente a 0,882, un recall de 0,549 frente a 0,532 y un 36,3 % frente a un 34,4 % marcado; en el tramo posterior, B2 con precisión 0,889 y recall 0,540. El agente de planificación vio solo el entrenamiento (la mecánica y el AUC dentro del entrenamiento: 0,740 y 0,773) y conteos de la validación y de la prueba; no calculó ninguna métrica en ellas. Fijar umbrales después de ver números podría ajustarlos a ellos.
- **Opciones**: (a) usar solo criterios no vistos; (b) usar los criterios naturales, decir cuáles se vieron y quién, y sumar criterios no vistos; (c) no fijar umbrales.
- **Decisión**: (b). C01-C10 (`requirements.md`, sección 8.1), con dos columnas sobre lo visto y umbrales naturales («mayor que», un intervalo por encima de 0, ECE de 0,03, sesgo de 0,02); la lectura pone el peso en los no vistos (C02, C03, C05, C06, C09). Los criterios quedan en `config/pipeline.toml [criteria]` y en este plan, confirmados en Git antes de U01. La expectativa queda escrita antes de medir: C03 y C05 probablemente no se cumplan; si es así, el README y el sitio dicen que el modelo aprende una regla de dos variables y nada más.
- **Motivo**: (a) dejaría afuera la pregunta principal (¿el modelo le gana a la regla por modo?) solo porque alguien ya la miró; (c) permitiría leer cualquier resultado como bueno. Escribir la expectativa antes de medir es lo que hace creíble un «no supera» posterior, en vez de una sorpresa que se esconde.
- **Afecta**: `config/pipeline.toml [criteria]`; `evaluation.py`; `reporting.py`; el README; la ficha del modelo; el tablero; el sitio.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D17 La prueba se toca una vez: modelo congelado y sello

- **Contexto**: la prueba debe tocarse una sola vez. En el proyecto 02, los criterios se confirmaron en Git antes del código (su D17), pero nada en el código impedía recalcular el resultado con otra configuración. La repetición mensual muestra el seguimiento de los meses de la prueba a medida que se cierran.
- **Opciones**: (a) solo la historia de Git como evidencia; (b) un modelo congelado en el lanzamiento y un sello versionado (la configuración del lanzamiento y, después, los resultados de la primera evaluación) que el código compara; (c) una tabla de solo inserción en SQLite.
- **Decisión**: (b). El modelo se entrena una vez, en el lanzamiento, y no se reentrena; `reports/model_seal.json` (plan, sección 13) guarda la configuración y su `config_sha256`, y después los resultados de la prueba; ninguna parte se reescribe; una evaluación con otra configuración da resultados `post_seal`, que nunca reemplazan a los sellados (M05); una con la misma debe reproducirlos. Ninguna prueba ni ninguna persona calcula una métrica en la prueba real antes de U25, que corre después de todo el código del modelo.
- **Motivo**: (a) depende de que alguien lea la historia; (c) desaparece al reconstruir desde cero (runbook R10). Un archivo versionado sobrevive a la reconstrucción, y su historia en Git muestra cuándo se escribió. Congelar el modelo hace que el seguimiento mensual sea una lectura del producto y no un ciclo de ajuste.
- **Afecta**: `registry.py`; las tareas `fit_model` y `evaluate`; `reports/model_seal.json`; AC20; runbook R7, R10 y R13.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D18 Intervalos por remuestreo pareado de pedidos

- **Contexto**: la investigación dijo que la diferencia entre su modelo y B2 «cabe dentro del ruido de una sola corrida». La prueba tiene 14.527 pedidos.
- **Opciones**: (a) solo estimaciones puntuales; (b) la prueba de DeLong para el AUC; (c) un remuestreo pareado de pedidos (1.000 remuestras, semilla 0); (d) un remuestreo por bloques de días.
- **Decisión**: (c), con los mismos índices para los dos puntajes y los percentiles 2,5 y 97,5 de la diferencia.
- **Motivo**: (a) no distingue un empate de una mejora; (b) necesita fórmulas propias y no sirve para el beneficio neto; (c) usa NumPy, sirve para cualquier métrica y es determinista con la semilla. Sobre (d): en el entrenamiento no se vio un efecto del día (días reales uniformes dentro del modo, día de la semana y mes sin señal), así que los pedidos se tratan como independientes **[Inferido]**; si la evaluación independiente encuentra agrupamiento por día, se vuelve a decidir.
- **Afecta**: `evaluation.py`; C02 y C03.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D19 Validación: qué bloquea, qué se aparta y qué se informa

- **Contexto**: los proyectos 01 y 02 usaron reglas con identificador y severidad. Aquí la fuente está limpia (sin duplicados, coherente por pedido) y los riesgos son la fuga, los datos personales, el significado de la etiqueta y los plazos de los resultados.
- **Opciones**: (a) bloquear ante cualquier anomalía; (b) bloquear solo lo que rompería la estructura, el significado de la etiqueta, la purga, el enmascarado o la serie de entregas, y advertir y contar el resto.
- **Decisión**: (b), con la tabla del plan, sección 6 (V01-V24, M01-M05, I01-I05).
- **Motivo**: un pedido cancelado es normal en el negocio (advertencia); una etiqueta que deja de significar «días reales > programados», un resultado que llega a los 7 días o un correo sin enmascarar tienen que frenar todo, porque harían mal todo lo siguiente.
- **Afecta**: `validate.py`; `reports/validation/*`; `fact_validation`; `validation_rules.json`.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D20 Almacén: SQLite en capas, con pedidos y resultados separados

- **Contexto**: el sitio dice dbt, BigQuery y SQL; los proyectos 01 y 02 usaron SQLite con SQL en capas como el reemplazo local de dbt con BigQuery (`LANDING-EXPANSION.md`) y lo probaron dentro de Docker.
- **Opciones**: (a) SQLite como los proyectos 01 y 02; (b) DuckDB; (c) dbt con BigQuery; (d) PostgreSQL.
- **Decisión**: (a), con capas `raw → stg → marts`, los pedidos y los resultados en tablas separadas y controles que devuelven las filas que violan.
- **Motivo**: 180 mil líneas le sobran; ya corrió en el contenedor de Airflow con un solo escritor; (c) necesita una cuenta en la nube y credenciales, que serían del dueño (S4); (b) y (d) suman una tecnología o un servicio sin ganancia. Las tablas separadas hacen estructurales D06 y D09.
- **Afecta**: `warehouse.py`; `sql/**`.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D21 Salidas para Power BI y diseño del tablero de alertas

- **Contexto**: S4 pide un HTML con el diseño del tablero donde Power BI necesita la interfaz. Los proyectos 01 y 02 generaron el HTML y los esquemas de los Parquet desde una sola especificación. El sitio promete «entregas a tiempo por región, modo de envío y producto, con los pedidos en riesgo arriba».
- **Opciones**: (a) lo mismo que los proyectos 01 y 02, con un hecho por pedido; (b) un hecho por pedido y fecha de cálculo, como el del proyecto 02; (c) un HTML escrito a mano.
- **Decisión**: (a): el modelo estrella del plan, sección 16 (`fact_order` con el riesgo, la alerta y el resultado de cada pedido, `fact_tracking_monthly` y los hechos de evaluación), con `dim_as_of` como tabla de parámetro para las alertas abiertas a una fecha, cinco páginas y relaciones 1:* de filtro unidireccional, todas activas, generados desde `dashboard/semantic_model.py`.
- **Motivo**: ya probado, y permite verificar con pruebas lo que pidió el dueño (relaciones 1:* con filtro unidireccional). (b) repetiría 65 mil pedidos en 15 corridas por una sola columna que cambia; las alertas abiertas a una fecha solo necesitan la fecha del puntaje y la del resultado. (c) no se puede comprobar contra los Parquet.
- **Afecta**: `export.py`; `dashboard/**`; `docs/data-dictionary.md`.
- **Quién**: dueño (S4) y agente de planificación (forma).
- **Estado**: vigente.

## D22 Exportación para el sitio y tamaño mínimo de celda

- **Contexto**: la página del proyecto se va a armar con la estructura del proyecto 01 (S10): una visualización interactiva de los algoritmos con datos reales y la figura interactiva de las reglas de validación. El proyecto 02 generó cuatro JSON con un contrato (su D22).
- **Opciones**: (a) exportar a mano después; (b) que el flujo genere los cuatro JSON del proyecto 02 adaptados; (c) que genere cinco, sumando uno para la fuga.
- **Decisión**: (c): `summary.json`, `validation_rules.json`, `leakage.json`, `algorithms.json` y `alerts.json`, con el contrato exacto del plan, sección 18, ≤ 200 KB en total, sin `order_id` ni valores personales, con las celdas de menos de 20 pedidos ocultas; los cinco pedidos de ejemplo salen de una regla fija (el riesgo mediano de cada celda de B5 en la prueba) y solo publican el mes.
- **Motivo**: lo que muestra el sitio sale de la misma corrida que el README y el tablero, y una prueba lo compara. La fuga es la historia de la sección de datos de este dataset y merece su archivo. Elegir los ejemplos por regla evita mostrar solo los casos que salen bien. Es un contrato que consume otro repositorio: cambiarlo después es de riesgo alto (ítem 3).
- **Afecta**: `site_export.py`; `outputs/site/*`; marca-personal.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D23 Orquestación: Airflow 3.4.0 en Docker con todos los secretos del entorno, y la CLI

- **Contexto**: el proyecto 02 dejó un compose endurecido y corregido (interfaz en `127.0.0.1`, tres claves con `${VAR:?}`, montajes de solo lectura, `./dags` de solo lectura, imágenes por versión y resumen, rechazos sin reintentos, `pip check` en la imagen), pero su D68 dejó pendiente del dueño la contraseña literal `airflow`/`airflow` de la base de metadatos, contra la letra de su RNF11. El proyecto 01 tuvo el conflicto de `python-dateutil` (L28).
- **Opciones**: (a) copiar el compose del proyecto 02 tal cual; (b) copiarlo y exigir también la contraseña de la base desde el entorno, en el servicio de PostgreSQL y en la cadena de conexión; (c) un diseño nuevo.
- **Decisión**: (b). DAG `latedel_monthly` (`0 6 2 * *`, sin catchup, una corrida y una tarea activas); `LATEDEL_DB_PASSWORD` con `${…:?}` en los dos lugares; `.env.example` con las cuatro claves vacías y el comando para generarlas en hexadecimal; la prueba de Docker genera las cuatro de usar y tirar, corre `pip check` y dos `airflow dags test` (lanzamiento y `M2017-01`) y borra su volumen.
- **Motivo**: RNF11 pide cada secreto desde el entorno, sin valores por defecto; corregirlo en el diseño cuesta una línea del compose y una de `.env.example`, y evita repetir la desviación del proyecto 02. El hexadecimal no necesita escaparse dentro de la cadena de conexión. **[Verificar en U23]** la interpolación dentro de esa cadena.
- **Afecta**: `airflow/**`; AC31, AC32; runbook R8.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D24 Rutas contra la carpeta del proyecto y modo rápido aislado

- **Contexto**: en el proyecto 01, una ruta relativa al directorio de trabajo falló dentro del contenedor (L14) y el modo rápido escribía sobre las salidas versionadas (su B3); en el proyecto 02, `make-fixture` escribía relativo al directorio de trabajo (su A9).
- **Opciones**: rutas relativas al directorio de trabajo o a la carpeta del proyecto; un modo rápido en las mismas carpetas con una marca, o con todas sus rutas bajo `data/quick/`.
- **Decisión**: toda ruta relativa de la configuración y de la CLI se resuelve contra `LATEDEL_HOME` o, si no existe, contra la carpeta padre de `config/`; `config/pipeline.quick.toml` pone todas sus rutas bajo `data/quick/` (ignorada por Git), incluidos el sello, el README, las figuras y el artefacto; una prueba corre la repetición rápida desde otro directorio y compara el hash de todo el árbol del proyecto.
- **Motivo**: un comando no puede depender de desde dónde se lo llama, y un modo de prueba no puede escribir sobre lo versionado; aquí, además, escribiría un sello con los resultados del accesorio.
- **Afecta**: `config.py`; `cli.py`; `config/*.toml`; `.gitignore`; runbook R12.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D25 Lecciones de los proyectos 01 y 02 que pasan a ser requisitos

- **Contexto**: las evaluaciones independientes de los proyectos 01 (tres bloqueantes y once advertencias) y 02 (B1-B3 y A1-A9) encontraron clases de defecto que este plan debe prevenir por diseño.
- **Opciones**: solo había un camino razonable: prevenir cada clase con un requisito y una prueba con nombre desde la unidad que la toca. La alternativa, esperar a la evaluación final, ya se probó dos veces y encontró los mismos tipos de defecto.
- **Decisión**: cada clase tiene su requisito y su prueba (la tabla del plan, sección 21):

  | Defecto | Proyecto | Aquí |
  | --- | --- | --- |
  | Información posterior al momento de la predicción | 01 B1 | D06, D09, RNF06, AC13, AC14 |
  | Una opción explícita ignorada en silencio | 01 B2, 02 B1 | AC19 (cada clave de costos), AC29 (cada opción) |
  | El modo rápido escribe en salidas versionadas | 01 B3 | D24, AC30 |
  | Una corrida cerrada reanudada pisa su historia | 02 B1 | D35, AC28 |
  | El manejador de fallas tapa el error de la tarea | 02 A5 | D35, AC27 |
  | Documentación sin sincronizar después de la última corrección | 02 B3 | RNF14, AC35 y el paso 4 de la sección 25 del plan |
  | Una cifra de memoria sin su contador | 02 A3, 01 A11 | D29, RNF03, AC35 |
  | Un compose con la contraseña literal de la base | 02 A6 (D68) | D23, RNF11, AC32 |
  | Decisiones sin todos sus campos | 02 B2 | este registro y AC35 |
  | Compose con claves por defecto, interfaz abierta, imágenes sin versión | 01 A1, A2 | D23, AC32 |
  | Una huella que «verificaba» sin un valor de confianza | 01 A3 | D02 |
  | Sin traza al fallar | 01 A4 | AC27 |
  | Tablas exportadas y HTML desactualizados en corridas fallidas o sin entrega | 01 A6, A7; 02 A8 | AC27 |
  | La documentación decía más que el registro | 01 A8 | RNF14 |
  | Diferencias con el sitio sin anotar | 01 A9 | plan, sección 27, y `SITE-UPDATES.md` en U27 |
  | Una selección sin clave de corrida o una tarea sobre una corrida anterior | 02 A2, A7 | AC28, AC29 |
  | Un rechazo reintentado durante 30 minutos | 02 A6 | D35 (sin reintentos) |
  | `python-dateutil` 4 contra las dependencias de Airflow | 01 L28 | D28 y `pip check` en la imagen (AC31) |

- **Motivo**: cada fila dice qué defecto previene; el motivo de cada requisito es no repetirlo, y cada uno tiene su prueba desde la unidad que lo toca, no después de la evaluación.
- **Afecta**: `requirements.md` (RNF03, RNF06, RNF11, RNF14, AC13, AC14, AC19, AC20, AC27-AC30, AC32, AC35); `plan.md`, secciones 21 y 25; `SITE-UPDATES.md`.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D26 Idioma y nombres

- **Contexto**: el sitio está en español por defecto; el código del portafolio, en inglés (supuestos del documento de seguimiento). Los proyectos 01 y 02 usan los paquetes `oilgas` y `custseg` y sus propios nombres de proyecto de Docker.
- **Opciones**: solo había un camino razonable: la regla de idiomas viene de los supuestos del documento de seguimiento y esta entrada la aplica; el nombre del paquete solo tiene que ser corto y no chocar con los otros dos.
- **Decisión**: documentación en español neutro; código, identificadores, tablas y columnas en inglés; medidas DAX, títulos de visuales y textos del sitio en español. El paquete se llama `latedel`; las variables de entorno, `LATEDEL_*`; el DAG, `latedel_monthly`; el proyecto de Docker Compose, `latedel-airflow`.
- **Motivo**: el contexto lo dice; un nombre propio evita que un volumen o una red de Docker de un proyecto toque los de otro (lección de la D55 del proyecto 02).
- **Afecta**: todo.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D27 Tecnologías fuera del landing: ninguna nueva

- **Contexto**: S5 pide usar las tecnologías del landing y anotar las demás en `LANDING-EXPANSION.md`. research.md concluyó que el proyecto no necesita nada fuera de lo ya anotado (SciPy, NumPy, SQLite, Parquet con pyarrow, matplotlib, pytest, PostgreSQL).
- **Opciones**: solo había un camino razonable: anotar lo que se use y el landing no declare; esta entrada comprueba que no hay nada nuevo, porque el artefacto del modelo usa `pickle` de la biblioteca estándar (D30) en vez de `joblib`, y el remuestreo usa NumPy.
- **Decisión**: ninguna fila nueva en `LANDING-EXPANSION.md`. SciPy entra solo como dependencia de scikit-learn; U27 agrega «03» a las filas de las tecnologías ya anotadas que este proyecto use (NumPy, SQLite, Parquet con pyarrow, matplotlib, pytest, PostgreSQL).
- **Motivo**: S5; y no sumar una dependencia directa que el landing no declara cuando la biblioteca estándar alcanza.
- **Afecta**: `LANDING-EXPANSION.md` (solo la columna de proyectos).
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D28 Entorno y versiones

- **Contexto**: los proyectos 01 y 02 resolvieron y probaron un juego de versiones fijadas en Windows con Python 3.11 y en la imagen `apache/airflow:3.4.0` con Python 3.13; después de L28 el del proyecto 01 pasa `pip check` en la imagen con `python-dateutil==2.9.0.post0` y `six==1.17.0`. Este proyecto no lee Excel (sin `openpyxl`). La investigación usó pandas 3.1.1.
- **Opciones**: (a) resolver de nuevo las versiones más recientes, incluido pandas 3; (b) reusar exactamente el juego del proyecto 01 después de L28.
- **Decisión**: (b). `requirements.txt`: `numpy==2.4.6`, `pandas==2.3.3`, `scipy==1.17.1`, `scikit-learn==1.9.1`, `pyarrow==26.0.1`, `matplotlib==3.11.2` y sus transitivas como en el proyecto 01 (`cloudpickle==3.1.2`, `contourpy==1.3.3`, `cycler==0.12.1`, `fonttools==4.68.0`, `joblib==1.6.0`, `kiwisolver==1.5.1`, `narwhals==2.28.0`, `packaging==26.3`, `pillow==13.0.0`, `pyparsing==3.3.3`, `python-dateutil==2.9.0.post0`, `pytz==2026.6`, `six==1.17.0`, `threadpoolctl==3.8.0`, `tzdata==2026.6`), nunca `python-dateutil` 4.x. `requirements-dev.txt`: `-r requirements.txt`, `pytest==9.2.0` y sus transitivas (`colorama==0.4.6`, `iniconfig==2.3.1`, `pluggy==1.7.0`, `Pygments==2.21.0`). `requires-python = ">=3.11,<3.14"`. Sin líneas `--hash` (D55 del proyecto 02); imágenes por versión y resumen.
- **Motivo**: ese juego ya se instaló junto en las dos plataformas y pasó `pip check` en la imagen; pandas 3 no se probó en la imagen. **[Verificar en U01]** `pip check` en el `.venv` y **[en U23]** en la imagen, y en U12 que los resultados de scikit-learn no cambien con pandas 2.3.3 respecto de lo medido con 3.1.1.
- **Afecta**: `requirements*.txt`; `pyproject.toml`; `airflow/Dockerfile`.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D29 Memoria: medida y con el contador nombrado

- **Contexto**: en el proyecto 02, los 1.471 MB informados eran el pico del conjunto de trabajo, mientras que el pico de memoria comprometida llegó a 3.514 MB en la corrida del evaluador, y su RNF03 decía «pico del proceso» (su A3). El agente de planificación midió aquí 471 MB de conjunto de trabajo y 1.999 MB comprometidos para la lectura y un ajuste, y que `GetProcessMemoryInfo` por `ctypes` sin `argtypes` falla con `OverflowError` en Windows de 64 bits.
- **Opciones**: (a) una sola cifra de «memoria máxima»; (b) un umbral sobre el conjunto de trabajo, el pico comprometido guardado como cifra informativa y cada cifra con el nombre de su contador; (c) el umbral sobre la memoria comprometida.
- **Decisión**: (b). RNF03: pico del conjunto de trabajo ≤ 1,5 GB (`PeakWorkingSetSize` en Windows, `ru_maxrss` en Linux); `PeakPagefileUsage` guardado sin umbral; `MemoryPeaks` lleva el nombre del contador; una prueba de documentación exige que toda cifra de memoria lo nombre; `argtypes` y `restype` declarados.
- **Motivo**: el conjunto de trabajo es lo que mide `ru_maxrss` en Linux, así que es la cifra comparable entre las dos plataformas; la memoria comprometida no tiene equivalente en la biblioteca estándar de Linux, y aquí es cuatro veces el conjunto de trabajo por razones que no se establecieron (quizá reservas de los hilos **[Inferido]**). Nombrar el contador es la corrección que el proyecto 02 necesitó al final.
- **Afecta**: `sysinfo.py`; `pipeline_runs`; la ficha del modelo; el README; AC33, AC35.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D30 Artefacto del modelo con huella verificada antes de cargarlo

- **Contexto**: el modelo congelado tiene que persistir entre corridas y dentro del contenedor. Deserializar un archivo alterado con `pickle` puede ejecutar código. El landing no declara `skops` ni ONNX.
- **Opciones**: (a) `pickle` o `joblib` sin comprobaciones; (b) `pickle` de la biblioteca estándar con el SHA-256 registrado y verificado antes de cargar; (c) `skops` u ONNX; (d) reentrenar desde el sello en cada corrida.
- **Decisión**: (b). Protocolo 5, en `data/models/<model_version>.pkl` (no versionado); la huella en `model_registry`; `load_artifact` lee los bytes una vez, calcula su SHA-256, lo compara y recién entonces deserializa esos mismos bytes (M03).
- **Motivo**: (a) es la opción insegura por defecto; (c) suma tecnología; (d) exige reentrenar de forma reproducible en cada plataforma y convierte cada corrida en un entrenamiento. La huella no vuelve seguro a `pickle` frente a quien pueda reescribir el archivo y la base a la vez, pero cierra el caso de un archivo reemplazado o dañado, y calcularla sobre los mismos bytes evita la ventana entre comprobar y cargar.
- **Afecta**: `registry.py`; M03; runbook R6.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D31 Demostración de la fuga en la validación

- **Contexto**: la sección de datos del sitio promete mostrar los campos que delatan el resultado; la investigación los midió en la prueba, por línea y con su propio código; la prueba se toca una vez (D17).
- **Opciones**: (a) copiar las cifras de la investigación; (b) recalcularlas en V2 con el código del proyecto; (c) no mostrarlas.
- **Decisión**: (b). `leakage_demo.py` entrena, con el entrenamiento, un modelo con el modo de envío y cada columna de `LEAKING_COLUMNS` (salvo el objetivo) y mide su AUC en V2, junto a la referencia del modo solo; vive en su propio módulo, que no puede escribir en `model_registry` ni en `model_scores`; el resultado va a `fact_leakage` y `leakage.json`.
- **Motivo**: (a) son cifras por línea, de otro código y de la prueba; (b) deja la prueba intacta y es reproducible; (c) dejaría afuera lo más instructivo del dataset.
- **Afecta**: `leakage_demo.py`; `fact_leakage`; `leakage.json`; la página 4 del tablero.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D32 El número de pedido en las salidas

- **Contexto**: una alerta tiene que poder usarse: el equipo necesita el número del pedido. `Order Id` no es un dato personal, pero junto con el CSV público lleva a los nombres (que parecen generados) del cliente.
- **Opciones**: (a) publicar `order_id` en los Parquet y en el diseño del tablero, nunca en los JSON del sitio; (b) una clave propia del proyecto; (c) ningún hecho por pedido.
- **Decisión**: (a).
- **Motivo**: el CSV es público con esos campos bajo CC BY: publicar el número de pedido no expone nada que la fuente no exponga, y las columnas personales no se republican (D07). (b) no cambia nada frente a quien tenga la fuente, porque se puede reconstruir ordenando igual; (c) pierde la lista de alertas. El sitio no recibe números de pedido.
- **Afecta**: `fact_order`; la página 1 del tablero; `site_export.py`; AC26.
- **Quién**: agente de planificación; el dueño puede pedir (b) (pendiente 4).
- **Estado**: vigente.

## D33 Sin API de puntuación en tiempo real

- **Contexto**: el landing declara FastAPI, y en una empresa la puntuación al crear el pedido sería un servicio; el proyecto es una repetición mensual sobre un archivo estático.
- **Opciones**: (a) un servicio con FastAPI, sus pruebas y su contenedor; (b) una función de puntuación pura (`score_new_orders`) que un servicio envolvería, sin servicio.
- **Decisión**: (b).
- **Motivo**: nada del dataset necesita tiempo real; un servicio suma un puerto, su seguridad y su contenedor sin cambiar ningún resultado. El dueño puede pedirlo para mostrar FastAPI (pendiente 6).
- **Afecta**: `scoring.py`; la ficha del modelo; runbook R13.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D34 Accesorio sintético con la mecánica medida y controles positivo y negativo

- **Contexto**: el accesorio del proyecto 02 simulaba clientes con parámetros conocidos. Aquí la mecánica real está medida (los desfasajes por modo, los días de calendario, el corte del mediodía) y lo esperable en los datos reales es que no haya señal más allá de B5 (D16).
- **Opciones**: (a) un accesorio aleatorio; (b) un accesorio con la mecánica medida; (c) el de (b) más un control positivo (una señal plantada), valores centinela en las columnas personales y una variante malformada por regla bloqueante.
- **Decisión**: (c), como dice el plan, sección 21.
- **Motivo**: si C03 no se cumple con los datos reales, el lector tiene que saber que el método habría detectado una mejora si existiera: el control positivo lo prueba, y el negativo prueba que no la inventa. Los centinelas hacen que la prueba de datos personales mire bytes, no nombres de columnas.
- **Afecta**: `tests/fixtures/*`; AC04, AC10, AC17, AC26.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D35 Estado de la corrida, cierre en orden y rechazos, desde el diseño corregido del proyecto 02

- **Contexto**: la evaluación del proyecto 02 encontró una corrida cerrada que se reabría con otra entrega (B1), un manejador de fallas cuyas escrituras podían tapar el error de la tarea (A5), una selección sin clave de corrida que dejaba un estado fallido (A2), tareas del modelo que corrían sobre una corrida anterior (A7), un HTML que no se refrescaba en una corrida sin entrega (A8) y rechazos reintentados (A6); sus correcciones son las D63-D67 de ese proyecto. El proyecto 01 marcaba `ok` antes de escribir sus archivos (L9).
- **Opciones**: solo había un camino razonable: empezar del diseño ya corregido del proyecto 02; empezar del anterior repetiría defectos que la evaluación ya encontró.
- **Decisión**: el plan, sección 19: estado por corrida, cierre en el orden de la sección 3, manejador de fallas con cada escritura protegida y el error de la tarea relanzado, rechazo de cualquier tarea de una corrida cerrada, de una segunda entrega y de las tareas del modelo sobre una entrega que no es la última, y `AirflowFailException` sin reintentos para los rechazos.
- **Motivo**: cada regla cierra un defecto encontrado, y empezar con ellas no cuesta más que agregarlas después.
- **Afecta**: `pipeline.py`; `cli.py`; el DAG; AC27, AC28, AC29.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D36 Trampa de fuga M01

- **Contexto**: todos los modelos con una columna que delata el resultado llegaron a un AUC de al menos 0,9985 en la investigación; sin esas columnas, ninguno pasó de 0,78 en ninguna partición; el techo visto en el entrenamiento con el corte del mediodía es 0,773.
- **Opciones**: (a) sin trampa, solo las guardas estructurales (D06); (b) detener el lanzamiento si el AUC del modelo elegido en V1 es ≥ 0,95; (c) solo una advertencia.
- **Decisión**: (b), con `leak_tripwire_auc = 0,95` en `[criteria]`.
- **Motivo**: las guardas estructurales son la defensa; la trampa es una segunda línea, independiente, para una fuga que nadie imaginó (por ejemplo, una columna nueva en otra versión de la fuente). 0,95 queda lejos de lo alcanzable sin fuga y por debajo de la fuga más débil medida; (c) dejaría seguir a un modelo con fuga.
- **Afecta**: `model.py`; `config/pipeline.toml [criteria]`; runbook R5.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D37 U01: comprobaciones de la configuración que el plan no enumera, y el orden en que se escribió

- **Contexto**: el plan, sección 4, enumera lo que `_check` rechaza. Las revisiones nativas de U01 y U04 del proyecto 02 (`R1-path-resolve-no-traversal-guard`, `R3-local-mode-url-unchecked`) y su evaluación final encontraron además rutas relativas que salían de la carpeta del proyecto, claves de un modo que el otro ignoraría en silencio y una URL cuyo host leen distinto dos analizadores. En U01 el código de `config.py` se escribió antes que su prueba: se lo apartó a una carpeta temporal, se dejó un `config.py` provisional (rutas tomadas tal cual, sin comprobaciones y con ayudas de host ingenuas) y la RED se observó contra él (98 pruebas fallidas, la primera `test_relative_paths_resolve_against_project_root_from_another_cwd` con `raw_dir` relativo, y `test_http_mode_requires_https_url_listed_host_and_hash` con `DID NOT RAISE ValueError`), antes de restaurar el código.
- **Opciones**: (a) solo las comprobaciones del plan; (b) esas más las que corrigieron los proyectos 01 y 02 y las que cierran una ambigüedad del plan: una ruta relativa que sale de la carpeta del proyecto, una clave de un modo que el otro ignoraría, la sintaxis de `allowed_hosts` y una URL con usuario, barra invertida o espacios; (c) dejarlas para la evaluación final.
- **Decisión**: (b). Una ruta relativa que, normalizada, sale de la carpeta del proyecto es `ValueError` con la clave (una absoluta se acepta: es explícita). En modo `local`, `url` y `accepted_sha256` deben estar vacías y `config/pipeline.quick.toml` dice `url = ""`; en modo `http`, `local_path` debe estar vacía. `allowed_hosts` no puede estar vacía, sus entradas van en minúscula, sin esquema, puerto ni ruta, y un sufijo (`.amazonaws.com`) necesita al menos dos etiquetas (`.com` solo se rechaza); `host_allowed` no acepta el dominio del sufijo en sí (`amazonaws.com`) ni nombres que solo terminan igual (`evilamazonaws.com`). `url_host` (la función que usará la descarga de U04) rechaza una URL con usuario (`user@host`), barra invertida, espacios o caracteres de control, porque ahí dos analizadores pueden leer hosts distintos. `calibration` solo acepta `isotonic` (D13), el único método implementado. Además de lo que dice el plan sobre las fechas, `launch_date` debe ser el día anterior a `test_start`, `valid_start` y `test_start` deben seguir a su bloque por `purge_days` días más uno, `post_break_start` al día siguiente de `test_end`, y `post_break_end` no puede pasar de `last_order_date`.
- **Motivo**: cada regla cierra un defecto que ya apareció en otro proyecto, o una clave que se aceptaría sin efecto; (c) repetiría lo que las dos evaluaciones anteriores encontraron al final. Que las fechas de la partición se comprueben contra las purgas hace que cambiar una sola sea un error y no un modelo distinto en silencio (`test_every_split_date_is_used_by_the_check`).
- **Afecta**: `config.py`; `config/pipeline.toml`; `config/pipeline.quick.toml`; `tests/unit/test_config.py`; U04 (usa `url_host` y `host_allowed`); AC29, AC30.
- **Quién**: agente de implementación (Sonnet).
- **Estado**: vigente.

## D38 U02: lo que el plan deja abierto en el accesorio sintético

- **Contexto**: el plan, sección 21, describe el accesorio y sus variantes malformadas, pero no fija cómo se define cada variante, cómo se garantiza que cada caso especial exista con pocos pedidos, ni cómo se hace comparable el accesorio con y sin señal. El módulo `csv` de la biblioteca estándar rechaza por defecto un campo de más de 131.072 caracteres (`_csv.Error`), lo que condiciona la variante `oversize`.
- **Opciones**: (a) `oversize` con un solo campo gigante, o con filas largas pero válidas; (b) los casos especiales (producto con `<script>`, nombre con espacios finales, nombre con coma y comillas, pedido de Same Day antes y desde el mediodía, pedido cancelado, pedido de varias líneas, pedido posterior al cambio de forma) solo por azar, o forzados en los primeros pedidos; (c) la señal plantada cambia solo el cálculo de las demás variables aleatorias, o el flujo de números aleatorios es el mismo con y sin señal.
- **Decisión**: (a) filas largas pero válidas: diez nombres de producto de 110.000 caracteres, que suman más de 1 MiB y dejan cada campo bajo el límite del lector; la variante falla solo por tamaño (V02). (b) forzados: los pedidos de índice 0 a 10 llevan los casos y no pueden ser cancelados, de modo que existen desde `n_orders = 100` (el mínimo que acepta `make_fixture`). (c) el mismo flujo: cada pedido hace las mismas extracciones con y sin señal, así que `signal="none"` y `signal="standard_market"` con la misma semilla difieren solo en el retraso de los Standard Class de LATAM (de 0,4 a 0,7). `wrong_hash` cambia una sola razón de rentabilidad de la última fila, para que el archivo siga siendo válido en todo lo demás; `outcome_after_7_days` mueve el envío de un pedido a 7 días y deja coherente la etiqueta, de modo que solo V09 la rechaza; las fechas se escriben como en la fuente, sin ceros a la izquierda (`1/31/2018 22:56`); el archivo va de los pedidos más nuevos a los más viejos, como la fuente. Un cancelado conserva la mecánica del envío y tiene etiqueta 0, como en la fuente (D05). `FixtureTruth` guarda además de lo que dice el plan cada pedido (`OrderTruth`), el SHA-256 y el tamaño; `MALFORMED_RULES` dice qué regla debe rechazar cada variante.
- **Motivo**: (a) un campo gigante no podría leerse con el límite por defecto del lector, y la prueba de tamaño no debe depender de eso; el límite en sí lo trata U03 (un campo de más de 131.072 caracteres se rechaza como fila inválida, V05, en vez de subir el límite global). (b) una prueba de un caso que existe solo a veces no prueba nada. (c) una señal plantada solo sirve de control positivo si todo lo demás es idéntico.
- **Afecta**: `tests/fixtures/make_fixture.py`; `tests/unit/test_fixture.py`; U03 (límite de campo), U06 y U07 (los recuentos esperados), U10, U12 (controles positivo y negativo), AC10, AC17.
- **Quién**: agente de implementación (Sonnet).
- **Estado**: vigente.

## D39 U03: cómo se lee una fuente que no es de confianza

- **Contexto**: el plan, sección 6, deja abierto con qué se lee el CSV (`csv` o `pandas.read_csv`, «[Verificar en U03] cuál detecta la fila corta sin tolerarla»), y dice que `read_source` devuelve `SourceFrames` y un `ValidationReport`, que es de U06. Se comprobó con `python -I`: `pandas.read_csv` rellena en silencio una fila de 52 campos (con `""` en el motor C y con `None` en el de Python, y `on_bad_lines="error"` solo vigila las filas de más). El módulo `csv` rechaza por defecto un campo de más de 131.072 caracteres (`_csv.Error`), y con `strict=True` rechaza las comillas mal puestas. Antes de escribir `ingest.py` se escribió `schema.py` (listas y esquemas de columnas, que son datos) y `files.py` (el escritor atómico del proyecto 02); las pruebas de la lógica se escribieron después y su RED se observó contra un `ingest.py` provisional (`write_interim` devolvía `[]`, `SourceReport` no tenía `lines`).
- **Opciones**: (a) `pandas.read_csv` y detectar la fila corta a posteriori (no se puede distinguir un campo vacío legítimo de uno rellenado); (b) el módulo `csv` sobre las líneas físicas leídas en bytes; (c) leer todo el archivo en memoria y decodificarlo de una vez. Para el resultado: (i) devolver ya un `ValidationReport`; (ii) devolver un `SourceReport` con los recuentos y que U06 lo convierta.
- **Decisión**: (b) y (ii). `read_source(path, cfg) -> (SourceFrames, SourceReport)` lee línea por línea en bytes: cada línea se comprueba (V03: byte nulo, CR suelto, byte inválido en la codificación configurada; V05: línea de más de 1 MiB) y se decodifica sola, de modo que cada rechazo dice la línea exacta y el archivo nunca está entero en memoria como texto. El BOM (UTF-8 o UTF-16) se mira en los primeros bytes. `csv.reader(strict=True)` sin tocar `csv.field_size_limit()` (que es global): un campo de más de 131.072 caracteres o unas comillas mal puestas son filas que no se pueden leer (V05). Los rechazos salen con la primera regla que falla, en el orden V02, V03, V04, V05, V06, V07, V08, V09, y `SourceError.problems` lista cada tipo de problema con su recuento y hasta cinco ejemplos. **Nunca se muestra el valor de una columna personal**: un correo sin enmascarar (V06), un valor de `Latitude` o de `Customer Id` que no convierte (V05) o una diferencia dentro del pedido en una columna personal (V07) se informan con la columna y la línea, sin el valor. El tamaño máximo (`source.max_bytes`) también se comprueba al leer (V02), no solo al descargar. La lectura no escribe nada; `write_interim` construye las tres tablas Arrow antes de tocar un archivo y escribe cada una con `atomic_path`. El escritor guarda en los metadatos la huella de la fuente, la atribución y los recuentos del `SourceReport`, nunca un valor de una columna personal.
- **Motivo**: (a) tolera justo el defecto que AC03 manda rechazar; (c) pondría en memoria 96 MB de bytes más el texto decodificado antes de saber si el archivo es válido. Decir la línea exacta del rechazo exige decodificar línea por línea (el decodificador de texto por bloques informa la posición dentro del bloque, no del archivo). No mostrar el valor evita que el mensaje de un rechazo, que va a registros y a reportes, lleve el dato personal que se quería mantener fuera (RNF12). (ii) deja a U06 convertir los recuentos en `RuleResult` sin que U03 dependa de un módulo posterior.
- **Afecta**: `schema.py`; `files.py` (módulo que el plan no lista, copiado del proyecto 02); `ingest.py`; `tests/contract/test_source.py`; U04 (`fetch_source` llama a `read_source` y `write_interim`), U06 (convierte `SourceReport.counts` en reglas V10 a V18); AC03 a AC08.
- **Quién**: agente de implementación (Sonnet).
- **Estado**: vigente.

## D40 U03: qué lleva cada flujo canónico y las definiciones que el plan no fija

- **Contexto**: el plan lista las columnas de `raw_orders`, `raw_order_lines` y `raw_outcomes` (sección 5) pero no cómo se calculan `benefit_total`, `profit_ratio_mean` y `outcome_known_date`, qué es un pedido cancelado, ni qué pasa con `Category Id` y `Department Id`, que están en la lista permitida (`ORDER_TIME_COLUMNS`, 23 columnas) pero no en las tablas. Sobre el archivo real, `Order Item Total` y `Sales − Order Item Discount` difieren hasta 0,0100135 (el archivo redondea al centavo valores guardados como float32) en 4.613 líneas, y `requirements.md` y `research.md` dicen que coinciden en el 100 %.
- **Opciones**: (a) cancelado = `Delivery Status` igual a `Shipping canceled`, o = `Order Status` en `CANCELED` o `SUSPECTED_FRAUD`; (b) `benefit_total` y `profit_ratio_mean` como la suma y como el promedio de las líneas, o como el valor de la primera línea; (c) llevar `Category Id` y `Department Id` a `order_lines` o dejarlas permitidas pero sin llevar; (d) la tolerancia de V13 en 0,005 (el redondeo de float32) o en un centavo y medio.
- **Decisión**: (a) `Delivery Status`: es la definición del plan para V11 y es la columna que define el objetivo de la tarea; sobre el archivo real las dos definiciones marcan las mismas 7.754 líneas (0 diferencias), y una prueba `real_data` lo vigila. (b) suma y promedio por pedido, porque las tres columnas de rentabilidad varían por línea (requerimientos, sección 4); van solo al flujo de resultados. `outcome_known_date = max(fecha del pedido + días reales, fecha del envío)` en días de calendario, como dice `requirements.md`, sección 4. (c) permitidas pero sin llevar: las tablas del plan tienen los nombres (`department_name`, `category_name`), y `Category Id` tiene 51 valores contra 50 nombres, así que llevar el código sumaría una columna que ninguna unidad usa; si una unidad posterior la necesita, es un cambio de esquema con su decisión. (d) 0,015: el mayor hueco medido es 0,010014, así que «coincide al centavo» es la lectura fiel de los dos documentos, y una tolerancia menor daría 4.613 falsos hallazgos (`MONEY_TOLERANCE` en `ingest.py`, con una prueba de los bordes). V09 se aplica a todos los pedidos, cancelados incluidos. Las columnas internas de la salida son las de `schema.SOURCE_TO_ORDER`, `SOURCE_TO_LINE` y `SOURCE_TO_OUTCOME`; `Customer Country` se conserva (2 valores) y `Customer Id` no.
- **Motivo**: son las lecturas más cercanas a lo que dicen `requirements.md`, `research.md` y el plan, y cada una se prueba contra el accesorio (valores calculados de forma independiente) y contra el archivo real (recuentos de AC06, etiqueta de AC08, lag máximo 6).
- **Afecta**: `ingest.py`; `schema.py`; U05 (las tres tablas que publica), U06 (V10 a V18), U08 (esquema SQL), U09 (`mart_labelled_orders`); AC06, AC08.
- **Quién**: agente de implementación (Sonnet).
- **Estado**: vigente.

## D41 U04: la descarga sigue las redirecciones a mano, revisa cada dirección antes de pedirla y no crea nada hasta tener respuesta

- **Contexto**: D02 fija la política (HTTPS en cada salto, hosts permitidos, a lo sumo 3 redirecciones, tope de bytes, huella fijada) y deja para U04 comprobar a qué host va hoy la redirección. Se comprobó con un `HEAD` (sin descargar el archivo): `data.mendeley.com` responde 302 a `prod-dcd-datasets-public-files-eu-west-1.s3.eu-west-1.amazonaws.com`, que cubre el sufijo `.amazonaws.com` de `allowed_hosts`. La biblioteca estándar sigue redirecciones por su cuenta, y el proyecto 02 tuvo, en las revisiones de U04, una descarga que escribía en un archivo de nombre fijo, errores de transporte sin registro y errores locales confundidos con cortes de red. El enlace firmado de S3 lleva su firma en la consulta (`?X-Amz-...`).
- **Opciones**: (a) dejar que `urllib` siga las redirecciones con un manejador que valide cada una; (b) desactivar el seguimiento de `urllib` y seguirlas a mano, revisando cada dirección antes de pedirla; (c) un cliente HTTP de terceros (descartado: sumaría una dependencia que el plan no lista).
- **Decisión**: (b). `check_hop(url, allowed_hosts, insecure_hosts, hop)` es la única regla y se aplica a la primera dirección y a cada `Location` (resuelta contra la dirección actual) antes de conectar: esquema `https` (el `http` solo para un host de `insecure_hosts`), puerto por defecto 443, host exacto o con el sufijo de la lista, y nada de usuario en la dirección, espacios, caracteres de control ni barras invertidas (son los casos en que dos analizadores leen hosts distintos). Una redirección sin `Location`, o la que pasa de `max_redirects`, también es V02 y no se pide. `download_file(url, dest, accepted_sha256, allowed_hosts, max_redirects, max_bytes, chunk_bytes, timeout_s, *, insecure_hosts=frozenset())` tiene el parámetro de solo prueba `insecure_hosts` (el plan lo describe como «un parámetro de prueba que habilita ese host solo dentro de la prueba»): `fetch_source` lo recibe y lo pasa, pero ni la configuración ni la CLI lo exponen, y la configuración real exige HTTPS. Ningún mensaje ni registro lleva la consulta de la dirección (la firma), solo esquema, host y ruta. No se crea nada (ni carpeta, ni archivo de preparación, ni `.part`) hasta que llega una respuesta 200 dentro del tope, de modo que una dirección rechazada deja el árbol intacto; el archivo de preparación se crea con `O_EXCL` y nombre aleatorio, y un `.part` solo se escribe con una huella distinta (V01), entero, reemplazando el anterior. El corte por tamaño (anunciado o recibido) y las redirecciones son V02 (`SourceError`); un corte de red, un estado distinto de 200 o un cuerpo más corto que el anunciado son `DownloadError` (reintentable) y se registran antes de lanzarse; una carpeta o un archivo de preparación que no se pueden crear y un disco lleno son `OSError` propios, registrados con su traza. `fetch_source` guarda el manifiesto de la fuente como `source_manifest.json` (no `manifest.json`, que es el de las entregas de U05), y en modo `local` registra la primera huella solo después de leer el contenido sin rechazo. Un CSV con la huella fijada pero con un contenido que `read_source` rechaza queda en `raw/` (la huella es la aceptada) y no se escribe ningún flujo ni manifiesto.
- **Motivo**: (a) deja la decisión de cada salto en el código de `urllib`, que valida después de elegir el destino; en (b) la regla es una función pura que se prueba con direcciones, y se comprueba que el servidor local no recibe nunca la petición al segundo host. Quitar la firma de los registros evita dejar en ellos un enlace temporal a un archivo de 96 MB. Lo que el proyecto 02 corrigió después de sus revisiones (archivo de preparación propio, registro de cada fallo, errores locales aparte, carpeta creada fuera del manejo de red) está aquí desde el principio.
- **No se hizo**: un tiempo máximo total de la descarga. `timeout_s` vale para la conexión y para cada lectura, así que un servidor que entrega bytes muy despacio podría alargarla; la huella fijada y la lista de hosts reducen el riesgo a una fuente de confianza, y el tope de bytes acota el tamaño.
- **Afecta**: `ingest.py` (`check_hop`, `download_file`, `fetch_source`, `SourceManifest`); `tests/fixtures/http_server.py` (el servidor local escucha también en 127.0.0.2 para tener un segundo host sin salir de la máquina); runbook R1, R2 y R11; AC01, AC02, RNF11.
- **Quién**: agente de implementación (Sonnet).
- **Estado**: vigente.

## D42 U05: cómo se publica una entrega, qué dice su manifiesto y qué bloquea a la siguiente

- **Contexto**: el plan, secciones 3, 6 y 20, fija el calendario de 15 entregas, que un pedido va en la entrega de su `order_date` y un resultado en la de su `outcome_known_date`, y que el manifiesto tiene «período, filas y SHA-256». No dice qué hash resume los tres archivos de una entrega, qué pasa si se publica dos veces o fuera de orden, ni si una entrega rechazada o a medio cargar deja pasar a la siguiente. El proyecto 02 corrigió después de su evaluación que una corrida cerrada se reanudaba y pisaba su historia (su B1); aquí el manifiesto y `publish` ya lo impiden desde la publicación.
- **Opciones**: (a) un hash por archivo en el manifiesto, o un hash del conjunto; (b) `publish` que sobrescribe una entrega ya publicada, que la rechaza siempre o que la acepta solo con el mismo contenido; (c) comprobar el orden en `publish` mirando el plan o mirando la última entrada del manifiesto; (d) `next_pending` que salta una entrega rechazada o a medio cargar, o que se detiene en ella.
- **Decisión**: (a) `sha256_json`: el SHA-256 del JSON canónico (`sort_keys`, separadores fijos) de los SHA-256 de `orders`, `order_lines` y `outcomes`, que `delivery_digest` recalcula desde los archivos; el manifiesto guarda además las filas de cada uno. (b) con el mismo contenido (los bytes se generan en memoria y se comparan con el hash del manifiesto) devuelve la entrada sin tocar un archivo; con otro contenido lanza `DeliveryConflictError`, y una entrega cargada nunca se reescribe. (c) la última entrada del manifiesto: la primera entrega debe ser el lanzamiento y cada otra empieza el día siguiente a que terminan los resultados de la anterior (`DeliveryOrderError`, antes de escribir nada), así que no hace falta pasarle el plan; `next_pending` además exige que el manifiesto sea un prefijo del plan, con sus mismos períodos (`DeliveryError` si no). (d) se detiene: devuelve la primera entrega que no está `loaded`, de modo que una entrega publicada pero no cargada (una corrida interrumpida) o rechazada vuelve a ser la siguiente y ninguna posterior corre antes. `mark_status` cambia el estado y la corrida de una entrega; una cargada solo puede quedar cargada por la misma corrida. El lanzamiento trae los resultados desde `first_order_date`; el cierre trae archivos de pedidos y líneas con cero filas pero con su esquema, para que todas las entregas se lean igual. `check_coverage` rechaza (antes de publicar nada) un pedido o un resultado con fecha fuera del plan, que de otro modo se perdería sin aviso. Los archivos llevan en sus metadatos el identificador de la entrega, la atribución y, si se pasa, la huella de la fuente. Sobre el archivo real, las 15 entregas suman 65.752 pedidos, 180.519 líneas y 65.752 resultados; el lanzamiento trae 42.152 pedidos, 126.331 líneas y 41.942 resultados, y el cierre, 248 resultados.
- **Motivo**: (a) un solo hash por entrega es lo que se compara en V24; guardar las filas permite detectar un archivo reemplazado por otro del mismo tamaño. (b) la idempotencia de AC09 sin permitir que una entrega ya usada cambie de contenido. (c) comprobarlo en el manifiesto cierra el orden donde se escribe, no donde se planifica. (d) dejar pasar una entrega rechazada saltaría el período que no se cargó y rompería V20.
- **Afecta**: `deliveries.py`; `tests/unit/test_deliveries.py`; U07 (V20 a V24 usan `delivery_digest` y `read_delivery`), U08 (carga), U21 (la tarea `deliver` usa `next_pending`, `publish` y `mark_status`); AC09.
- **Quién**: agente de implementación (Sonnet).
- **Estado**: vigente.

## D43 U06: las reglas V10 a V18 y el reporte de la fuente

- **Contexto**: el plan, sección 6, pide para V10-V18 «filas y pedidos afectados», y D39 deja a U06 convertir `SourceReport.counts` en reglas. Ese diccionario solo trae el recuento propio de cada regla (por ejemplo, `orders_off_schedule` de V10 o `discrepancies` de V12, que cuenta celdas): no dice cuántas líneas ni cuántos pedidos afecta V10, V12, V13, V14 ni V17. Varias pruebas de U02 a U04 comparan `counts` con la verdad del accesorio por igualdad exacta. La prueba de U06 se escribió antes del código: se dejó un `validate.py` provisional (reglas vacías, informe sin contenido) y la RED se observó contra él (44 pruebas fallidas y 2 que pasaban por no depender de nada, la primera `test_the_rules_have_the_ids_severities_and_scope_of_the_plan`).
- **Opciones**: (a) agregar claves a `counts` (rompe las igualdades de U02 a U04 y mezcla dos cosas: lo que la regla cuenta y a quién afecta); (b) un campo nuevo `affected` en `SourceReport`, con valor por defecto, que dice `{"rows", "orders"}` de V10, V11, V12, V13, V14 y V17; (c) recalcular las filas y los pedidos desde los marcos (no se puede: V14 ya llega recortado y V12 solo existe dentro del bucle de lectura).
- **Decisión**: (b). `load_source_report` devuelve `None` si el reporte guardado en los Parquet no trae `affected` (diseño anterior) y `interim_fingerprint` lo trata como un conjunto que no sirve, así que `fetch_source` reconstruye los flujos en vez de validar con ceros inventados. `validate_source(outcome, *, run_key, source_sha256)` recibe un `SourceError` (falla su regla; las bloqueantes evaluadas antes pasan; todas las demás quedan en `not_evaluated`, nunca como aprobadas) o un `SourceReport` (V01-V09 pasan y sus recuentos dan V10-V18). Un V02 que lanza la descarga (dirección rechazada, cuerpo por encima del tope) deja a V01 sin evaluar, porque la huella se compara después de recibir el cuerpo; un V02 de tamaño de un archivo ya en disco (`kind: size`) viene después de V01. Severidades: V10, V12, V13 y V14 advierten si encuentran algo; V11 advierte siempre que haya pedidos cancelados (2.855 en la fuente real); V15-V18 son informativas y siempre pasan, con el número de columnas como ejemplo. Un rechazo cuenta filas (V05, V06 y las líneas repetidas de V07) o pedidos (V07 por pedido, V08, V09); una regla de archivo o de encabezado (V01-V04) cuenta 0 y dice dónde. Los ejemplos de un rechazo se limpian (ASCII imprimible, 60 caracteres como máximo, como los mensajes del lector), no llevan la ruta de esta máquina (`kept`) ni el valor de una columna personal (D39). `make_result` y `with_results` quedan públicos para que U07 y los pasos del modelo sumen sus reglas al mismo informe. `write_report` escribe `latest.json` y `latest.md` y una copia por corrida en `history/` (que no se versiona), y rechaza un `run_key` que no sirva de nombre de archivo antes de escribir nada.
- **Motivo**: (b) no cambia ningún valor que ya se probaba y deja claro qué es cada número; la regla que dice «0 pedidos» no debe depender de un recuento de celdas. Reconstruir los flujos cuando falta `affected` cuesta una lectura de 1 s y evita que un reporte viejo diga «0 filas» donde no se contó. Sobre el archivo real las cifras son: V11 7.754 líneas y 2.855 pedidos, V14 1.774 líneas y 1.774 pedidos, V17 8.557 líneas y 8.403 pedidos, y V10, V12 y V13 con 0.
- **Afecta**: `ingest.py` (`SourceReport.affected`, `load_source_report`, `interim_fingerprint`); `validate.py`; `tests/contract/test_validate_source.py`; U07 (`make_result`, `with_results`), U21 (escribe el reporte de la corrida), U20 (`validation_rules.json`); AC10 (fuente).
- **Quién**: agente de implementación (Sonnet).
- **Estado**: vigente.

## D44 U07: las reglas de la entrega, la interfaz de las del modelo y lo que cuenta cada una

- **Contexto**: el plan, sección 6, fija V20-V24, M01-M05 e I01-I05, y la sección 20 la firma `validate_delivery(delivery, spec, conn, cfg)`. No dice qué cuenta `rows_affected` cuando la regla mira una entrega entera, qué hace la validación cuando los archivos no coinciden con el manifiesto (`read_delivery` lanzaría antes de poder informar V24), ni cómo se suman al informe las reglas que calculan los pasos del modelo (que corren después, en `fit_model`, `score` y `track`). El almacén no existe hasta U08, y las reglas lo leen. La prueba se escribió primero contra un `validate.py` que dejaba las reglas vacías: 24 pruebas fallidas y 3 que pasaban por no depender de las reglas.
- **Opciones**: (a) que `validate_delivery` reciba solo los marcos y lance si el manifiesto no coincide; (b) que reciba también la entrada del manifiesto y la carpeta de la entrega, compare él mismo y ofrezca una función que lea los archivos tal como están; (c) que cada paso del modelo arme su propio informe aparte. Para el conteo: filas de la tabla que la regla marca, o filas y pedidos de la entrega entera cuando falla algo global.
- **Decisión**: (b). `validate_delivery(delivery, spec, conn, cfg, *, entry, folder, run_key)` evalúa V20-V24 y I01, I03, I05 y deja las reglas del modelo (M01-M05, I02, I04) en `not_evaluated`; `validate_published_delivery(out_dir, entry, spec, conn, cfg, *, run_key)` lee los archivos con el nuevo `read_delivery(..., verify=False)` (que sigue lanzando con un archivo ausente o que no es Parquet) y, si no se pueden leer, devuelve un informe con solo V24 fallida (`unreadable_files`, con los nombres de los archivos y nunca una ruta) y el resto como no evaluado. Los pasos del modelo suman su resultado con `make_result` y `with_results`, que lo coloca en su lugar de `RULES` y lo quita de `not_evaluated`; M05 es una advertencia y las informativas siempre pasan. `rows_affected` y `orders_affected` cuentan las filas de las tres tablas que la regla marca y los pedidos distintos que mencionan: V20 y V24, que hablan de la entrega entera, cuentan todas sus filas; V21 cuenta los pedidos fuera de su período con sus líneas, y los resultados fuera del suyo; V22 cada fila ya cargada o repetida dentro de la entrega; V23 las líneas y los resultados sin pedido; I01, los pedidos que todavía no están cargados con sus líneas; I03, los resultados que todavía no están cargados; I05, los pedidos del régimen posterior al cambio con sus líneas. Los ejemplos llevan a lo sumo 5 filas por tabla, solo identificadores, fechas ISO y motivos. V20 distingue `not_in_plan`, `other_period`, `already_loaded`, `previous_not_loaded` y `not_contiguous` (el almacén cargó la anterior con un fin de período que no precede al inicio de esta); el almacén solo se lee, y sin tablas es un almacén vacío. «Vacía» (V24) quiere decir sin pedidos ni resultados, de modo que el cierre, que no trae pedidos, no lo es. Para esto `read_delivery` ganó el parámetro `verify` (por defecto verdadero: el comportamiento anterior no cambia).
- **Motivo**: (a) impediría informar V24 con la huella y las filas que difieren, que es lo que el plan pide; (c) duplicaría el informe y su orden. Contar la entrega entera en las reglas globales y las filas marcadas en las demás da un número que se puede comprobar a mano. Las pruebas de la regla usan un almacén mínimo con solo las columnas que las reglas leen, y las de U08 repiten los casos clave sobre el esquema real. Sobre el archivo real las 15 entregas pasan en orden y suman 65.752 pedidos nuevos (I01), 65.752 resultados (I03) y 8.403 pedidos del régimen posterior (I05); una segunda validación del cierre da V20 `already_loaded`.
- **Afecta**: `validate.py`; `deliveries.py` (`read_delivery`); `tests/contract/test_validate_delivery.py`; `tests/fixtures/helpers.py`; `tests/conftest.py` (fixture `delivered`); U08 (carga), U14 y U16 (suman M-rules e I02/I04), U21 (`validate` llama a `validate_published_delivery`); AC09, AC10.
- **Quién**: agente de implementación (Sonnet).
- **Estado**: vigente.

## D45 U08: el almacén, lo que carga cada transacción y lo que el plan deja abierto

- **Contexto**: el plan, secciones 5, 7 y 20, define las tablas `raw_*`, `deliveries`, `pipeline_runs`, `validation_results` y `stg_*` y la firma `load_delivery(conn, delivery, spec, run_key)`, pero esa firma no trae ni la configuración (que da las fechas de la partición y `purge_days` a los controles) ni la huella del manifiesto (que se guarda en `deliveries`); no dice si las tablas de las etapas llevan el identificador de la entrega, si las claves foráneas se declaran, qué hace `record_validation_results` cuando el mismo `run_key` guarda primero el informe de la fuente y después el de la entrega, ni cuándo se crean las tablas del modelo (`002_model.sql`). Los controles de `sql/checks/load/` son de U09. La prueba de U08 se escribió primero contra un `warehouse.py` provisional cuyas funciones lanzaban `NotImplementedError`: 27 pruebas fallidas, la primera `test_failed_check_rolls_back_a_first_load`. La primera mutación de `stg_outcomes.sql` (quitar `outcome_known_date <= :as_of`) sobrevivió: en una carga normal el almacén solo tiene resultados ya conocidos, así que el filtro no se ve; se agregó `test_the_staging_files_filter_by_the_date_they_are_given_whatever_the_warehouse_holds`, que reconstruye las etapas para fechas anteriores sobre un almacén con todo cargado, y esa mutación y la de `<` en lugar de `<=` en `stg_orders.sql` pasaron a fallar.
- **Opciones**: (a) `load_delivery` con la firma del plan y la configuración y la huella tomadas de un estado global, o con ambas como argumentos; (b) `stg_orders` y `stg_order_lines` con o sin `delivery_id` (solo con las columnas permitidas, para que `build_features` reciba exactamente lo que el plan permite); (c) claves foráneas entre las tablas `raw_*` o controles en SQL; (d) `record_validation_results` que borra todas las filas de la corrida o solo las de las reglas que reemplaza; (e) las tablas del modelo ahora o en la primera unidad que las usa.
- **Decisión**: (a) argumentos: `load_delivery(conn, delivery, spec, run_key, cfg, *, sha256_json)`. (b) sin `delivery_id` en las etapas: las columnas de `stg_orders` y `stg_order_lines` son exactamente `ORDERS_SCHEMA` y `ORDER_LINES_SCHEMA`, y `stg_outcomes` suma `is_late` (vacío si el pedido se canceló, D05). (c) sin claves foráneas en `raw_*`: `chk_line_has_order` y `chk_outcome_has_order` (U09) son los que miran, y con una clave foránea nunca podrían fallar; el almacén sí exige `NOT NULL` en cada columna (un `NaN` que se guardaría como nulo es un `IntegrityError` y deshace la carga), `CHECK` en `regime`, `kind`, `status` e `is_cancelled`. (d) por regla: un informe de la fuente y uno de la entrega de la misma corrida se guardan los dos, y repetir un informe reemplaza solo las filas de sus reglas. (e) en la primera unidad que las usa (U12 a U16): `002_model.sql` no existe todavía. Una carga hace en una transacción `BEGIN IMMEDIATE`: esquema, comprobación de que la entrega no esté cargada, filas de las tres tablas, fila de `deliveries`, etapas (`stg_orders`, `stg_order_lines`, `stg_outcomes`, en ese orden), controles de `sql/checks/load/` con los parámetros nombrados de `sql_params` (la fecha de cálculo, `purge_days` y los bordes de los bloques de la partición) y el recuento de las filas de la entrega; cualquier falla, incluso una clave que se repite, deshace todo y el archivo queda con los mismos bytes (la prueba lo compara con el SHA-256 del archivo). «Vacía» quiere decir sin pedidos ni resultados, como en V24: el cierre no lo es. `SchemaDriftError` y el resto del ejecutor son los del proyecto 02.
- **Motivo**: (a) explícito evita un estado oculto que las pruebas no puedan reemplazar; (b) las etapas son la entrada del modelo y lo que no está en la lista permitida no debe poder entrar (D06); (c) un control que no puede fallar no vigila nada; (d) el informe de la corrida se arma en dos pasos del flujo y el segundo no debe borrar el primero. Sobre el archivo real las 15 entregas cargan 65.752 pedidos, 180.519 líneas y 65.752 resultados en 7,3 s, las etapas a la fecha del cierre tienen las mismas cantidades y 36.048 de los 62.897 pedidos no cancelados llegaron tarde (57,3 %).
- **Afecta**: `warehouse.py`; `sql/schema/001_core.sql`; `sql/staging/*`; `tests/integration/test_load.py`; U09 (marts y controles), U10 (`features.py` lee `stg_orders` y `stg_order_lines`), U12-U16 (`002_model.sql`), U21 (usa `record_run` y `record_validation_results`); AC11, AC12, AC04.
- **Quién**: agente de implementación (Sonnet).
- **Estado**: vigente.

## D46 U09: los marts operativos, el bloque de cada pedido y los doce controles de la carga

- **Contexto**: el plan, secciones 7 y 9, pide `mart_main_line`, `mart_labelled_orders` (con «el bloque de la partición») y ocho controles de `sql/checks/load/`. No dice qué columnas lleva el mart de etiquetas, cómo se nombra el bloque de un pedido que ninguno contiene, cómo se recalculan las etapas para una fecha anterior a la del último almacén, ni qué vigila `chk_one_mode_per_order` cuando la tabla `raw_orders` ya tiene una fila por pedido. La prueba de U09 se escribió primero contra un `rebuild_at` provisional que lanzaba `NotImplementedError` y sin los archivos SQL: 22 pruebas fallidas, la primera `test_labelled_orders_at_as_of_exclude_later_outcomes`.
- **Opciones**: (a) un mart de etiquetas con todas las columnas del pedido o solo las que usan la partición y las líneas base (`order_ts`, `regime`, `shipping_mode`, `scheduled_days`); (b) los pedidos fuera de todo bloque se excluyen del mart o llevan un nombre propio; (c) las etapas se reconstruyen solo en la carga o también con una función pública para cualquier fecha; (d) `chk_one_mode_per_order` como la regla V07 repetida en SQL (el modo es constante por pedido, que ya no se puede ver aquí), como «cada modo tiene los mismos días programados» (que contradiría a V10, que solo advierte) o como «cada pedido de las etapas tiene exactamente un modo no vacío»; (e) solo los ocho controles del plan o también los que vigilan lo que U09 agrega.
- **Decisión**: (a) `mart_labelled_orders` lleva `order_id`, `order_ts`, `order_date`, `regime`, `shipping_mode`, `scheduled_days`, `outcome_known_date`, `is_late` y `block`; las variables siguen saliendo de `stg_orders` y `stg_order_lines`, nunca de este mart. (b) `outside` para una fecha que ningún bloque contiene (antes de `train_start` o después de `post_break_end`); las purgas se llaman `purge_1` y `purge_2` y no pertenecen a ningún bloque del modelo. Los bordes salen de `[split]` por parámetros nombrados (`sql_params`). Un pedido entra en el mart solo si tiene un resultado en `stg_outcomes` (conocido en la fecha) y no está cancelado. (c) `rebuild_at(conn, as_of, cfg)` reconstruye las etapas y los marts para esa fecha en una transacción y con los mismos controles que la carga; es lo que usarán `fit_model`, `score` y la prueba de perturbación (una fecha anterior sobre un almacén que ya tiene todo cargado). (d) la tercera: la regla V07 se comprueba al leer, donde las líneas de un pedido están juntas, y aquí queda lo que se puede ver en lo ya cargado: un pedido sin modo o repetido en las etapas. (e) los ocho del plan más cuatro: `chk_main_line_one_per_order` (cada pedido de las etapas tiene exactamente una línea principal; un pedido sin líneas la viola), `chk_labelled_one_per_order`, `chk_labelled_has_a_label` (un pedido cancelado no entra en el mart y todo pedido del mart tiene etiqueta 0 o 1) y `chk_nothing_after_as_of` (nada de las etapas ni del mart está fechado después de la fecha de cálculo). `chk_label_definition` y `chk_outcome_lag_within_purge` leen las tablas `raw_*` completas, no solo las etapas. `mart_main_line` usa `ROW_NUMBER()` (mayor `sales`, menor `line_id` en el empate). La primera versión de la prueba de los bloques pasaba con una mutación de `<=` por `<` en el borde, hasta que se agregó una prueba con una fecha en cada borde y a cada lado.
- **Motivo**: un mart que no dependa de lo posterior a la fecha es lo que hace física la ausencia de información futura (D09): la prueba de U10 y las de los modelos lo reusan. Un control que se puede violar con datos reales (una línea sin pedido) y otro que ninguna carga puede violar a propósito (la unicidad de una clave primaria) se prueban en una base armada a mano. Sobre el archivo real los bloques coinciden con la tabla de `requirements.md`, sección 4: entrenamiento 29.894, purga 1 375, V1 4.649, V2 5.032, purga 2 377, prueba 14.527 y posterior al cambio 8.043 (62.897 en total), con 65.752 líneas principales; en el lanzamiento el mart tiene 29.894, 375, 4.649, 5.032 y 172 pedidos de la purga 2, y ninguno de la prueba.
- **Afecta**: `warehouse.py` (`OPS_MARTS`, `rebuild_at`); `sql/marts/ops/*`; `sql/checks/load/*`; `tests/integration/test_marts.py`; U10 (`rebuild_at` para la prueba de perturbación), U11 (`split.py` lee el mart y repite los bordes), U12-U14; AC12, AC15.
- **Quién**: agente de implementación (Sonnet).
- **Estado**: vigente.
