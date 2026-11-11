# Paso a paso: cómo se hizo este proyecto

La cronología real, desde el registro del documento de seguimiento (`odd/tasks/portfolio-projects.md`, L28 a L37) y desde `git log`. Las horas son las de los commits, del 2026-11-10 y el 2026-11-11; cada sección cita los suyos.

## Cómo se organizó el trabajo

El proyecto siguió las fases que el dueño fijó para todos los del portafolio (S2): investigación, requerimientos y plan, programación, diseño del tablero en HTML, pruebas y documentación, con la estructura del proyecto 01 (S10, S12). Cada fase la hizo un agente con el modelo que el dueño pidió para su tarea (S8): Haiku para la investigación, Opus para el plan y Sonnet para la programación; desde la reanudación del 2026-11-11 (L36) el dueño pidió Opus también para las pruebas y la implementación. Una sesión orquestaba: lanzaba a cada agente con su unidad, revisaba lo que devolvía, corría la revisión nativa y anotaba todo en el registro.

Cada unidad de trabajo terminó con su prueba escrita primero y vista fallar (la RED), sus pruebas en verde, su documentación y un commit propio. Cada unidad que tocaba un punto de riesgo alto (el entorno, el archivo no confiable, la descarga, el artefacto del modelo, el sello, un contrato que otros leen, el orden de las escrituras, la concurrencia) fue en su commit solo, para que la revisión nativa de Gentle AI la tomara sola. El dueño pidió aceptar de forma automática los pedidos de consentimiento de esas revisiones (S11), y sus advertencias se resolvieron en unidades de endurecimiento (H1 a H9), con su registro en [review-advisories.md](review-advisories.md).

## 1. Investigación (Haiku, 15:50) `2a8eb45`

El agente descargó el CSV de Mendeley sin cuenta (95.910.149 bytes), comprobó que su SHA-256 era el que publica la API de archivos, lo perfiló (180.519 filas, 53 columnas, 65.752 pedidos, cp1252 con CRLF) y midió la fuga: `Delivery Status` es la etiqueta en el 100 % de las filas, los días reales son exactamente los programados más el retraso, la fecha de envío da los días reales y, con el modo, un AUC de 0,9985 en la prueba, y `Order Status` no se conoce al crear el pedido. Encontró el cambio de forma de octubre de 2017 (una línea por pedido), propuso la partición y las líneas base, y una primera mirada del modelo dio un AUC de 0,736 contra 0,740 del modo de envío con los días programados. Anotó diez diferencias con la página del sitio. La sesión que orquestaba reemplazó en `research.md` una dirección de ejemplo por su forma.

## 2. Requerimientos y plan (Opus, 16:37) `0235a90`

Antes de cualquier código: `requirements.md` (RF01-RF25, RNF01-RNF14, AC01-AC36 y los criterios C01-C10), `plan.md` (27 secciones) y `decisions.md` (D01-D36, cada una con sus siete campos). Con conteos y solo en el bloque de entrenamiento, el agente encontró que la fecha de envío es la del pedido más 12, 48 o de 48 a 144 horas según el modo, que los Same Day creados antes del mediodía llegaron todos a tiempo (853) y los creados desde el mediodía todos tarde (742), y que dentro de Second y Standard Class ninguna otra columna mostraba señal. Por eso agregó una regla más fuerte, B5 (modo por franja del pedido), y escribió lo que esperaba antes de medir: C03 y C05, el modelo contra B5, probablemente no se cumplirían. Corrigió tres cosas de la investigación (la señal no buscada en la hora, el 97,2 % que era un artefacto del redondeo y unas cifras por línea que debían ser por pedido).

## 3. Entorno, accesorio, lectura, descarga y entregas (U01-U05, Sonnet, 16:47-17:20) `4357cae` `e41b604` `c468901` `5e6db9d` `d3a354c`

- **U01** (solo, ítem 5): entorno, versiones fijadas y configuración con rutas contra la carpeta del proyecto. Las versiones se instalan en la imagen `apache/airflow:3.4.0` con `pip check` limpio. El código se escribió antes que la prueba: se apartó, se vio la RED contra un esqueleto y se restauró (D37).
- **U02**: el accesorio sintético con la mecánica medida, valores centinela en las 15 columnas personales y una variante malformada por regla.
- **U03** (solo, ítem 2): la lectura del CSV como archivo no confiable, byte a byte con `csv` (porque `pandas.read_csv` completa una fila corta sin avisar), y las columnas personales descartadas en la lectura; también con la RED contra un esqueleto (D39).
- **U04** (solo, ítem 2): la descarga con las redirecciones seguidas a mano, solo a hosts permitidos, con un tope de bytes, un archivo de preparación y la huella fijada. Una descarga real de Mendeley redirigió a un depósito de Amazon S3.
- **U05**: los pedidos y los resultados publicados en dos flujos, cada uno por su fecha: quince entregas, cada pedido, línea y resultado exactamente una vez.

En el archivo real: 2.855 pedidos cancelados (7.754 líneas), 62.897 pedidos para la tarea, 8.403 posteriores al cambio de forma. Revisión nativa: U01, U03 y U04 aprobadas con advertencias; U02 y U05, medianas y bajo el presupuesto, sin revisión.

## 4. Validación, almacén, marts y guardas de fuga (U06-U10 y H1, Sonnet, 17:52-18:41) `5fc777a` `1e355a5` `1521529` `14f47cd` `58053f2` `77a7dbe`

- **U06** y **U07**: las reglas de la fuente (V01-V18) y de la entrega (V20-V24), la interfaz de las del modelo y las informativas.
- **U08**: el almacén SQLite con su esquema, una carga en una transacción y la capa de staging. Una mutación sobrevivió primero y ganó una prueba de reconstrucción en una fecha anterior.
- **U09**: los marts operativos y doce controles de la carga.
- **U10**: las variables del momento del pedido detrás de cuatro guardas contra la fuga, probadas por perturbación: cambiar los 65.752 resultados no cambió la huella de las variables.
- **H1**: las advertencias de U01, U03 y U04: la lista de hosts pasó a `data.mendeley.com` y el depósito exacto de S3, un manifiesto de huellas para los archivos intermedios, un tiempo máximo para toda la descarga y `time.perf_counter()` porque `time.monotonic()` avanza de a 15,6 ms en Windows con Python 3.11.

En el archivo real, las quince entregas pasan en orden y se cargan en 15,8 s; 36.048 de los 62.897 pedidos llegaron tarde (57,3 %). Suite rápida: 598 pruebas en 98,76 s. La revisión del tramo U05-U10 superó el presupuesto de contexto y se partió por commit: U07 aprobada con advertencias; H1 también.

## 5. Partición, modelo, umbral y sello (U11-U14 y H2, Sonnet, 19:14-20:16) `7f42145` `a57065b` `1a8881d` `9f0fd30` `53f605b`

- **U11**: la partición en el tiempo con purgas y las líneas base B0-B5 con tasas solo del entrenamiento. La región en Same Day parecía tener efecto (p = 0,0025) y con la hora controlada no lo tiene (p = 1).
- **U12** (solo, ítem 2): la grilla, la selección en V1, la calibración isotónica con `FrozenEstimator` y el artefacto cargado solo si sus bytes tienen el SHA-256 registrado. Una mutación (calibrar con medio V1) sobrevivió primero y ganó su prueba. La negativa a deserializar se probó con un archivo que crea una marca si se carga: no se cargó.
- **U13**: el umbral por beneficio neto en V2 y su sensibilidad al costo: 0,66 para el modelo.
- **U14** (solo, ítem 1): el lanzamiento congelado, el sello escrito una vez por un enlace duro y los puntajes de solo inserción. Repetir el lanzamiento en otro almacén dio la misma huella de la configuración, los mismos puntajes y los mismos bytes del artefacto.
- **H2**: las advertencias de U07 y H1 (entre ellas, tres de diez archivos dañados daban `KeyError` dentro de las reglas y siete se validaban sobre la tabla equivocada).

Solo en el entrenamiento y la validación: en V1 el modelo elegido (F2) tuvo pérdida logarítmica 0,5005 y AUC 0,7757; en V2, AUC del modelo 0,7756, de B2 0,7431 y de B5 0,7808. Suite rápida: 822 pruebas en 121,4 s. Revisión nativa: U12 y U14 aprobadas con advertencias.

## 6. Evaluación, seguimiento y modelo estrella (U15-U17 y H3, Sonnet, 21:06-21:41) `85f0a98` `13f5cf1` `a535920` `a220030`

- **U15**: la evaluación contra las reglas simples con un remuestreo pareado, los criterios C01-C10 con veredictos fijos y un resumen que dice «no supera» cuando corresponde; el `docs-sync` del README se construyó y se probó sobre un archivo temporal (D58).
- **U16**: el seguimiento mensual de las alertas cerradas, cada fecha leída solo con lo registrado y conocido hasta ella.
- **U17** (solo, ítem 3): el modelo estrella de 20 tablas y nueve relaciones uno a varios, con sus controles dentro de la transacción que lo construye.
- **H3**: las advertencias de U12 y U14. Cada afirmación se comprobó primero: `roc_auc_score` con una sola clase devuelve NaN con un aviso en scikit-learn 1.9.1 y la trampa M01 pasaba en silencio con NaN (ahora es un error propio, D65); D54 tenía mal el orden del sello y el registro (D62).

Todavía ninguna métrica de la prueba real. Suite rápida: 940 pruebas en 134,6 s, cerca del tope de 150 s. Revisión nativa: U17 y H3 aprobadas con advertencias.

## 7. Velocidad de la suite, medidas DAX, diseño del tablero y archivos del sitio (U18-U20 y H4, Sonnet, 22:24-23:13) `9b78989` `e3e84f9` `f96f108` `7a75c0e` `b27ec4d`

- **Velocidad**: los almacenes cargados y las lecturas de archivos dañados que las pruebas solo leen se construyen una vez por sesión (D66): de 134,7 s a 107,0 s, también en orden inverso y aleatorio.
- **U18**: 31 medidas DAX y cinco páginas sobre una grilla de 12 por 9, comprobadas contra las columnas del modelo; no corrió ningún motor de DAX.
- **U19**: el diseño del tablero como una página HTML autocontenida, sin recursos externos ni scripts, igual byte a byte en dos dibujos, con un producto, una región y un mercado hostiles mostrados como texto.
- **U20** (solo, ítem 3): los cinco archivos del sitio desde los marts del modelo, con las celdas chicas ocultas. Una mutación expuso una celda vacía visible y un error en una división, que se corrigieron.
- **H4**: las advertencias de U17 y H3, con las 20 tablas del modelo estrella iguales fila a fila antes y después, en el accesorio y en el archivo real.

Suite rápida: 1.041 pruebas y 1 saltada en 123,9 s. Incidente: mientras perfilaba, el agente terminó dos procesos de Python por su nombre de imagen, uno suyo y otro desconocido; desde entonces las instrucciones prohíben terminar procesos por nombre. Revisión nativa: U20 y H4 aprobadas con advertencias (la salida de una revisora se rechazó dos veces al admitirla y el mismo lugar, ofrecido de nuevo, se volvió a correr).

## 8. Tareas, línea de comandos, Airflow y figuras (U21-U24, Sonnet, 2026-11-11 00:06-00:45) `b6eb897` `a2da3df` `07564d8` `273b82f` `6666d09`

- **U21** (solo, ítem 4): las once tareas en una tupla, el estado de la corrida, un cierre en cinco escrituras ordenadas y los rechazos antes de cualquier escritura (D72).
- **U22**: la línea de comandos con cada opción probada y el modo rápido, que solo escribe en `data/quick/` (D73).
- **U23** (solo, ítem 5): el DAG, la imagen y el compose con cada secreto desde el entorno, también la contraseña de la base de metadatos (D74).
- **U24**: siete figuras de la documentación desde los archivos del sitio y el comando `figures` (D75).

El dueño cortó el trabajo por el día antes de H5 y H6 («podrías cortar el agente y seguimos mañana?»). El agente se detuvo antes de devolver su informe, así que la evidencia de RED, mutación y tiempos de estas unidades está solo en D72-D75.

## 9. Reanudación: las revisiones pendientes, la corrección de U23, la velocidad y H5-H8 (06:24-08:25) `686de92` `0ce62dd` `eab3c48` `cf07539` `10de7e2` `8f1650d` `c01fc61`

El dueño pidió seguir de forma autónoma, aceptar las revisiones de Gentle AI y lanzar Opus para la arquitectura, el planeamiento, las pruebas y la implementación. Las revisiones pendientes:

- **U21**, aprobada con 5 advertencias.
- **U23**, `correction_required` por un hallazgo crítico determinista: el script de inicialización de Airflow no tenía `set -e`, así que una migración fallida salía con 0 y dejaba arrancar a los demás servicios. Se corrigió con la prueba primero dentro de la revisión (RED: salida 0 después de la migración fallida), la validación dirigida lo aprobó, y se trajo a la rama como `686de92`.
- U22 y U24, medianas y bajo el presupuesto, sin revisión.

Un agente con Opus construyó después:

- **Velocidad** `0ce62dd` (D76): de 144,9 s a 131,9 s; no llegó a los 110 s buscados, y `pytest-xdist` se descartó porque es una dependencia nueva.
- **H5** `eab3c48` (solo, ítem 3; D77): las 8 advertencias de U20: un ejemplo del sitio se publica solo si comparte lo que muestra con al menos 20 pedidos de la prueba, una supresión secundaria y un manifiesto de los cinco archivos.
- **H6** `cf07539` (D78): las 7 advertencias de H4.
- **H7** `10de7e2` (D79): las 5 advertencias de U21, entre ellas que una falla al guardar el resultado de una tarea se registra como falla de la tarea.
- **H8** `8f1650d` (D80): las advertencias abiertas de U23: los complementos de Airflow de solo lectura, el servicio de depuración y `AIRFLOW_UID` documentados.

La revisión de H5 aprobó sin hallazgos; la de H8, con 4 advertencias; H6 y la velocidad, medianas, sin revisión. La de H7 terminó escalada, sin aprobación, por un hallazgo crítico de causalidad desconocida: que un disco lleno haría fallar también el registro de la falla y taparía el error. La sesión que orquestaba lo comprobó contra el código (cada escritura del registro de la falla va por separado y se registra si falla) y lo fijó con una prueba, `c01fc61`, que una mutación hace caer. Suite rápida tras H8: 1.235 pruebas y 1 saltada en 140,8 s; `-m real_data` 28 de 28; `-m docker` 2 de 2.

## 10. La corrida real y el sello (U25, Opus, 09:00) `1d84a78`

La prueba se escribió antes de la corrida y falló porque faltaba `outputs/powerbi/dim_date.parquet`. Con los costos supuestos tal como estaban (el dueño no eligió otros), `python -m latedel replay` y después `python -m latedel run`, una sola vez: 105,14 s contra 10 minutos, el lanzamiento en 30 s, cada mensual de 3 a 7 s, y un pico del conjunto de trabajo de 921,6 MB (`PeakWorkingSetSize`) contra 1,5 GB. Un primer intento sin `PYTHONPATH` salió en 0,08 s y no escribió nada. El sello quedó escrito y las salidas versionadas: 20 Parquet, cinco archivos del sitio y su manifiesto, el HTML, los reportes y siete figuras; la búsqueda de valores personales en cada archivo nuevo no encontró ninguno.

En la prueba: AUC del modelo 0,77305, de B2 0,741955 y de B5 0,773692. El modelo supera 7 de los 9 criterios con umbral y no supera C03 ni C05, como el plan escribió antes de medir. Nada se ajustó después de ver la prueba. Tres pruebas que exigían que el sello no existiera pasaron a comprobar que no cambia (D81). La revisión nativa del commit entero superó el presupuesto de contexto (4.440 líneas) y se hizo en tres partes, en commits de una copia aparte cuyo árbol final es el de `1d84a78`: la primera aprobó con 3 advertencias, la segunda sin hallazgos y la tercera quedó bajo el presupuesto.

## 11. Un escritor a la vez y las últimas advertencias (H9, Opus, 09:43) `9e5f5da`

Las advertencias de H8 y U25 y el registro del escalado de H7 (D82, D83). La principal: el servicio de depuración de Airflow podía escribir el almacén junto al planificador y solo un comentario lo advertía. Ahora cada punto de entrada del flujo toma un bloqueo exclusivo de la carpeta de datos antes de escribir, y un segundo proceso se rechaza sin escribir nada; un bloqueo que deja un proceso matado se informa y no lo borra ningún otro proceso. La RED fueron 13 pruebas contra la interfaz del bloqueo sin comportamiento, con un segundo proceso real; dos mutaciones (el archivo sin `O_EXCL` y una tarea sin el bloqueo) cayeron por las pruebas con nombre, y los comandos que no compiten dieron la misma salida que antes. La prueba del modo rápido compara ahora la huella de los 39 archivos versionados antes y después, y la cifra de memoria de la fila de una corrida quedó anotada como limitación. Suite rápida: 1.253 pruebas y 1 saltada en 142,78 s; `-m real_data` 29 de 29 en 180,58 s.

La revisión nativa de H9 (`d230676..9e5f5da`, riesgo, resiliencia y confiabilidad) aprobó con ocho advertencias sobre el bloqueo: un número de proceso que el sistema reutiliza hacía parecer vivo un bloqueo abandonado, un bloqueo soltado entre la creación y la lectura se informaba como ilegible, un `replay` sin nada pendiente pasaba a tomar el bloqueo, durante un `replay` el bloqueo no decía qué corrida estaba en curso, la rama de Windows no tenía prueba, el hueco entre dos tareas del planificador (ya declarado), el registro del proceso sin guarda de hilos y la cifra fija de 39 archivos versionados. Quedaron registradas para después de la evaluación, que reprodujo cuatro; su resolución está en `docs/review-advisories.md` (sección H9) y en la sección 14.

## 12. README y ficha del modelo (U26, Opus) `95f23e1`

El README con los títulos del plan y el bloque de resultados copiado por `python -m latedel docs-sync` del resumen sellado, y la ficha del modelo con cada veredicto como lo escribe el resumen, también los dos «no supera» (D84). «Con IA, de punta a punta» cuenta el proceso desde este registro y no desde la sección 23 del plan, escrita antes del trabajo. Cada comando de «Cómo correrlo de punta a punta» se corrió en una copia aparte: el entorno, las pruebas, el modo rápido, la corrida real (que dio el sello y las siete figuras byte a byte iguales a las versionadas) y las figuras.

## 13. Diccionario, runbook, este documento y las filas de la raíz (U27, Opus)

El diccionario de datos con las 53 columnas de la fuente y su destino, y las tablas de los 20 Parquet generadas por `docs-sync` desde el modelo semántico; el runbook R1-R13 con todo lo que las revisiones dejaron para él; este paso a paso; las filas del proyecto 03 en `SITE-UPDATES.md` con las cifras de la corrida real, y «03» en las filas de `LANDING-EXPANSION.md` de las tecnologías que usa (D27). Las pruebas de la documentación comprueban que cada decisión tiene sus siete campos, que el número de pruebas que dicen los documentos es el de la colección y que cada cifra de memoria nombra su contador (D85).

## 14. La evaluación independiente, sus correcciones y su comprobación (Opus, 2026-11-11 10:28-14:06) `f8d09fe` `7af35f2` `1032732` `b5b2c08` `c1c1366` `6f3be98` `9bb8e75` `8263735` `441cccd`

Un agente con Opus que no construyó el proyecto evaluó `eef9bea` en un clon aparte, en solo lectura ([evaluation.md](evaluation.md)): recalculó cada cifra de C01 a C10 con un lector propio y dio lo mismo, sondeó la fuga, las opciones de la línea de comandos, el modo rápido, las corridas cerradas, el bloqueo y el sello, y repitió la corrida real, que reprodujo todas las salidas versionadas. Encontró seis bloqueantes y seis advertencias. Otro agente con Opus los corrigió con la prueba primero, vista fallar por la razón que daba la evaluación, una mutación por regla en una copia fuera del árbol y, en el código compartido, una comparación de las órdenes con `3abd982`:

- `f8d09fe` (B1, D86): `load` y toda tarea posterior se rechazan, con salida 2 y antes de escribir, sobre una entrega que la corrida no cargó; el cierre nunca crea la fila de una corrida.
- `7af35f2` (B2 y las advertencias de H9, D87): soltar el bloqueo reintenta un momento y nunca levanta; el bloqueo dice cuándo empezó su dueño y qué corrida está en curso; un `replay` sin nada pendiente no lo toma; otro hilo se rechaza.
- `1032732` (B3, D88): después del sello, un almacén sin la prueba sellada toma los criterios y las cifras del sello y muestra debajo el análisis posterior.
- `b5b2c08` (B4, D89): una prueba que difiere del sello solo donde actúan claves que la huella no cubre es otra configuración, no una reproducción fallida.
- `c1c1366` (A2 y A4, D90): un `LATEDEL_CONFIG` relativo se toma de la carpeta del proyecto; `docs-sync` sin README dice por qué.
- `6f3be98` (A1, D91): tres pruebas lentas más baratas; la suite bajó de 162,26 s y 154,27 s a 149,21 s y 142,19 s, sin llegar a los 135 s fijados.
- `9bb8e75` (B5, B6 y A6, D92, 12:39): esta sección, la de H9, `review-advisories.md`, el README, D27, la fila de SciPy y la prueba de las advertencias abiertas.

Una repetición real completa con el código corregido, en una copia fuera del árbol, reprodujo el sello byte a byte, las siete figuras byte a byte y los demás archivos versionados salvo las claves de corrida, las horas y las celdas de memoria; ninguna salida versionada se regeneró ni se volvió a versionar.

**Las revisiones nativas de las correcciones** (S11: el consentimiento se aceptó de forma automática). Cada corrección que tocaba un punto de riesgo alto se revisó sola, con los carriles que cada una pedía:

- B1 (`3abd982..f8d09fe`, ítem 1, riesgo y confiabilidad): aprobó y se reconoció, con tres advertencias.
- B3 (`7af35f2..1032732`, ítem 1) aprobó con cuatro; B4 (`1032732..b5b2c08`, ítem 1) con dos; A2 (`b5b2c08..c1c1366`, ítem 3) con dos.
- B2 (`f8d09fe..7af35f2`, ítem 4, riesgo, resiliencia y confiabilidad) terminó `correction_required` por dos hallazgos deterministas: la toma anidada que fallaba al reescribir dejaba su registro en `_HELD` y el bloqueo quedaba para siempre (bloqueante), y la liberación anidada podía levantar un `OSError` aunque dice que nunca levanta (crítico). La sesión que orquestaba los corrigió con la prueba primero en la copia de la revisión (5c4b417, 70 líneas; la RED fue el bloqueo que quedaba y el `OSError` de la liberación), la validación dirigida aprobó con seis advertencias, se reconoció, y la corrección se llevó a la rama como `8263735` (13:13) con el mismo identificador de parche. Las pruebas del bloqueo, del endurecimiento y del compose pasaron (67).
- Los tramos `9e5f5da..3abd982` y `c1c1366..9bb8e75` quedaron medianos y bajo el presupuesto de la revisión, sin revisión.

Las advertencias de todas esas revisiones y su resolución están en `review-advisories.md`; quedan abiertas cuatro (`R3-retrying-exc-info-cleared`, `R3-seal-mismatch-untested`, `R3-pending-why-branch-untested` y `R3-002` de la revisión de B4) además de la A1.

**La comprobación de las correcciones (Opus).** Otro agente con Opus, que no evaluó ni corrigió, rehízo las sondas de B1 a B6 en dos clones, `eef9bea` (evaluado) y `52c7eeb` (corregido), con 38 copias de trabajo: cada bloqueante se reproduce en el primero y no en el segundo, las advertencias corregidas también, una repetición real con el código corregido reprodujo el sello y las figuras byte a byte, y una comparación de 34 pasos de la línea de comandos no mostró ninguna diferencia que nadie pidiera. Suite rápida 142,99 s y 140,95 s en reposo (una falla: la cifra de pruebas, pendiente de esta sincronización); `-m docker` 2 de 2 en 74,50 s. Su informe está resumido en la «Comprobación» de [evaluation.md](evaluation.md). Encontró un hueco que las correcciones no habían causado: `task load` cargaba una entrega que su corrida no había validado, o que su validación había rechazado.

**El rechazo de `load` sin validación** (`441cccd`, 14:01, la sesión que orquestaba, en línea, con la prueba primero; D93). `load` se rechaza con salida 2 y antes de escribir si no existe el informe de validación de la corrida o si una regla bloqueante falla en él. La RED fueron las dos pruebas nuevas: `load` escribía y después fallaba con `FileNotFoundError`, y una entrega rechazada se cargaba; una mutación que quita el rechazo cae por su prueba. Su revisión nativa (`52c7eeb..441cccd`, ítem 1, riesgo y confiabilidad) aprobó con tres advertencias. La suite rápida en `441cccd`: 1.298 pruebas, 147,15 s y 144,38 s en dos corridas seguidas en un clon nuevo.

**Esta sincronización** (Sonnet): el commit de documentación que sigue a `441cccd` llena la «Comprobación», agrega D93, las resoluciones de las advertencias de las revisiones de las correcciones y de la comprobación, el runbook R3 y las cifras (1.298 pruebas y los dos tiempos), sin tocar código, pruebas ni ninguna salida versionada.

## Lo que falta

- **Las advertencias abiertas**: A1 (la suite rápida por encima de los 135 s fijados para la corrección, D91), que decide el dueño, y las cuatro de las revisiones de las correcciones que piden una prueba o un aviso (`review-advisories.md`).
- **La página del sitio** con la estructura del proyecto 01, en marca-personal, con las filas de `SITE-UPDATES.md` que el dueño apruebe.
- **Lo que decide el dueño** (plan, sección 26): los costos supuestos (después del sello, un cambio es un análisis nuevo), el texto del sitio, el `.pbix`, el número de pedido en los Parquet (D32), si el sitio dice que los datos parecen generados, una API de puntuación con FastAPI, la estrategia de entrega de los PR, si el README instala el paquete en lugar de usar `PYTHONPATH`, y la línea de `AIRFLOW_UID` en `airflow/.env.example`.
- **La corrida programada de Airflow sobre el archivo real**: Airflow se probó en Docker con la fuente sintética; la corrida real se hizo con la línea de comandos.

Después de la sincronización, al armar la página del proyecto en el sitio, el escritor de la página encontró que `validation_rules.json` sumaba I02 e I04 sobre las quince corridas aunque el seguimiento de cada una ya incluye los meses anteriores (66.761 alertas en vez de 8.840). La sesión que orquestaba lo corrigió en línea, con la prueba primero, y regeneró los archivos del sitio desde una copia del almacén sellado: cambiaron solo `validation_rules.json` y su manifiesto (D94).
