# Evaluación final independiente

Qué se evaluó, quién lo evaluó, qué encontró y cómo se resolvió cada hallazgo. Es el control que el proyecto dejó para el final (plan, sección 25): lo hizo un agente que no escribió el código, sobre una copia aparte del commit `eef9bea`, y sus hallazgos se corrigieron después con la prueba primero. Otro agente comprobó esas correcciones («Comprobación»); lo que describe este documento está al día con `441cccd`, el último commit de código.

## Quién evaluó y cómo

Un agente de Claude Code con el modelo Opus, **distinto de los agentes que construyeron el proyecto** y sin su contexto de trabajo. Trabajó en solo lectura sobre el repositorio: clonó `eef9bea` con `git clone --no-hardlinks` en una carpeta temporal, usó diez copias de trabajo de ese clon para sus sondas y copió el CSV de `data/raw/`, cuya huella SHA-256 es la fijada (`fa6d022e…0aa6`). Armó su propio entorno con Python 3.11.1 desde `requirements-dev.txt` (`pip check` limpio). Corrió la suite rápida dos veces, `-m real_data`, `-m docker`, una repetición real completa con la corrida sin entrega, dos repeticiones reales más después del sello con otros costos y sondas en modo rápido. No leyó ningún archivo `.env` ni `.env.example`, no corrió revisiones nativas y no terminó ningún proceso por su nombre de imagen.

Recalculó cada cifra con un lector propio del CSV (biblioteca estándar), su propia tarea y sus bloques, sus propias tasas de B2 y B5, su propia elección de umbrales y un AUC por rangos. Los puntajes del modelo los tomó del `fact_order` versionado (prueba y tramo posterior al cambio) y, para V2, del artefacto congelado, cuya huella coincide con la del sello.

## Veredicto por especificación

| Especificación | Veredicto | Evidencia |
| --- | --- | --- |
| S2 | Cumple (contra el registro) | Investigación con Haiku (L28), plan con Opus (L29), Sonnet en U01-U24 y Opus desde L36 como pidió el dueño; diseño HTML en U19; pruebas; documentación en U26 y U27 |
| S3 | Cumple | La narrativa del README y sus 13 títulos (la prueba de documentación pasa) |
| S4 | Cumple | `dashboard/powerbi-design.html`; el `.pbix` es del dueño |
| S5 | **En parte** | B6 (SciPy) |
| S6 | No aplica | |
| S7 | Cumple | Solo herramientas gratuitas |
| S8 | Cumple (contra el registro) | |
| S9 | Cumple | 14 filas en `SITE-UPDATES.md`; cada cifra coincide |
| S10 | En parte | La estructura está; faltaban este documento y la página del sitio |
| S11 | Cumple (contra el registro) | L30 a L38 |
| S12 | En curso | |

## Veredicto por criterio de aceptación

«(suite)» quiere decir que lo verifican las pruebas del proyecto, que pasan, y no una sonda propia.

- **Cumplen**: AC01 (sonda de V01, salida 3); AC02, AC03, AC05 y AC07 (suite); AC04 (ningún valor centinela ni enmascarado fuera del accesorio); AC06 (180.519 líneas, 65.752 pedidos, 2.855 cancelados con 7.754 líneas, 62.897 para la tarea); AC08 (la etiqueta coincide con la marca de retraso en el 100 % de los pedidos; atraso máximo de 6 días); AC09 y AC10 (suite); AC11 (una corrida rechazada cambió solo su archivo de estado y sus informes de validación); AC12 (suite); AC13 y AC14 (sonda de fuga); AC15 (todos los conteos de los bloques; última etiqueta del entrenamiento 2016-07-06, de la validación 2017-01-06); AC16, AC17 y AC18 (suite); AC19 (cada una de las claves de `[costs]` cambia el resultado); AC21 (artefacto alterado rechazado, M03); AC22 (23.600 pedidos puntuados una vez cada uno; la repetición reprodujo cada riesgo); AC23; AC24 (9 relaciones, 0 problemas de claves foráneas); AC25 (130.972 bytes, ningún recurso externo, atribución presente); AC26 (61.727 bytes, sin identificadores, ninguna celda visible por debajo de 20); AC29 (con la advertencia A2); AC30 (A5); AC31 (Docker 2 de 2); AC32 (sonda del compose; la parte de las claves vacías descansa en la prueba del proyecto, porque no podía leer `.env.example`); AC33 y AC34; AC36 (por poco margen, A1).
- **En parte**: AC20 (B3, B4), AC27 (B2), AC28 (B1).
- **No cumple**: AC35 (B5).
- **RNF**: cumplen RNF01, RNF02 (105,2 s; el lanzamiento unos 30 s; las corridas mensuales de 3 a 7 s; la repetición rápida unos 23 s; Docker 80 s), RNF03 (991,5 MB de `PeakWorkingSetSize`, el pico del conjunto de trabajo), RNF06, RNF09, RNF10, RNF11, RNF12 (0 valores de calle y 0 de nombre completo en cualquier salida) y RNF13; en parte RNF04 (A2) y RNF07 (B1); no cumple RNF14 (B5, B6).

## Los criterios C01 a C10

Lo que dicen el resumen, el README, la ficha del modelo, D81, `SITE-UPDATES.md` y los archivos del sitio (que coinciden entre sí), contra lo que recalculó el evaluador.

| | Declarado | Recalculado | Veredicto |
| --- | --- | --- | --- |
| C01 | AUC 0,77305 contra 0,741955 | 0,7730503 contra 0,7419551 | Coincide, supera |
| C02 | 0,031095, intervalo de 0,026489 a 0,035966 | Idéntico con su bootstrap (semilla 0); con la semilla 1, de 0,02639 a 0,03588 | Coincide, supera |
| C03 | −0,000641, intervalo de −0,006106 a 0,005066 | Idéntico; con la semilla 1, de −0,00608 a 0,00476 | Coincide, no supera |
| C04 | 1.459,35 contra 1.323,05 por cada 1.000 (21.200 contra 19.220) | Igual | Coincide |
| C05 | 1.459,35 contra B5 a 0,80: 1.467,27 | Igual; su propia elección del umbral de B5 en V2 también es 0,80 | Coincide, no supera |
| C06 | ECE 0,015614, sesgo −0,006461 | Igual | Coincide |
| C07 | 0,176733 contra 0,188972 | Igual | Coincide |
| C08 | k = 4.998; 4.499 contra 4.421 | Igual | Coincide |
| C09 | 17 de 17 | 17; cada umbral de equilibrio y cada beneficio neto igual a `fact_cost_sensitivity` | Coincide |
| C10 | 0,7788 / 0,744727 / 0,776507; 37,7 %, 0,898, 0,588 | Igual | Coincide |

También coinciden V2 (AUC del modelo 0,775558 y beneficio neto 1.504,37 con 0,66; B2 0,743149; B5 0,780846; umbrales del modelo 0,66 y de B2 y B5 0,80), las tasas (B2 1,000 / 0,8017 / 0,4652 / 0,4013; B5 Same Day 0 de 853 antes del mediodía y 1 de 742 desde el mediodía), el seguimiento (23.600 / 8.840 / 8.421 / 7.538 / 12.915 / 5.377 / 12.800,2; 1.030 cancelados después, con 419 alertas), la demostración de la fuga con su propio modelo (0,7431 / 1,0 / 1,0 / 0,9985) y cada tamaño declarado (2.229.845 bytes de Parquet, 61.727 de los archivos del sitio, 130.972 del HTML, 4.202 del sello y 444.601 de las figuras).

## Lo que el evaluador sondeó

- **Fuga (AC14)**: alteró un accesorio después del 2017-03-31 (7 resultados todavía desconocidos en esa fecha, 409 resultados posteriores, 282 pedidos posteriores borrados, 275 modificados y 108 agregados) y corrió `replay --until M2017-03` en las dos copias: idénticos los bytes del artefacto, el registro (salvo `config_sha256`, que por diseño es la huella de la fuente), las tasas, los 213 puntajes y alertas, V2, la sensibilidad, la calibración, la curva ROC, el seguimiento, la importancia, la fuga y el sello del lanzamiento.
- **Opciones de la línea de comandos y claves de `[costs]`**: cada opción tuvo efecto, y cada una de las nueve claves de `[costs]` cambió el umbral, el beneficio neto o la tabla de sensibilidad (10 corridas en paralelo). Excepciones: A2 y A4.
- **Modo rápido en varias copias**: una copia limpia corrida desde otro directorio agregó archivos solo bajo `data/quick` (más 32 archivos `__pycache__`); doce corridas rápidas en paralelo no se interfirieron.
- **Corridas cerradas**: 142 llamadas (`--from`, `--only` y `task` para cada tarea sobre el lanzamiento, M2017-05, el cierre y la corrida sin entrega, y `--from` sin clave) salieron con 2 y no cambiaron ningún archivo. Con una clave de corrida vieja o desactualizada apareció B1.
- **El manejo de las fallas**: con el artefacto alterado y el archivo de estado, el SQLite y los archivos exportados de solo lectura, la corrida salió con 1 y «falló: ArtifactMismatchError: M03…», cada escritura fallida quedó en el registro y el estado siguió en `running`.
- **El sello después del sello**: con la misma configuración, los valores se reprodujeron y el sello quedó intacto; con otro costo o con otro umbral, lo nuevo quedó como posterior al sello y el sello byte a byte igual. Ahí aparecieron B3 y B4.
- **El compose sin claves**: sin claves, y con cada una de las cuatro ausente o vacía, `docker compose config` falló nombrando la que faltaba; los montajes de `src`, `sql`, `config`, `dags` y `plugins` son de solo lectura, las imágenes están fijadas por resumen y la interfaz escucha en 127.0.0.1.
- **La memoria y su contador**: su lectura en el proceso (`GetProcessMemoryInfo`: 991,5 MB de `PeakWorkingSetSize`, el conjunto de trabajo, y 2.846,6 MB de `PeakPagefileUsage`, la memoria comprometida) es igual al registro del flujo por tarea; la limitación de la fila de una corrida (D83) se reproduce (2.148,8 contra 2.158,2 MB de `PeakPagefileUsage`).
- **El bloqueo**: con un dueño vivo, cinco órdenes que escriben salieron con 2 y no cambiaron nada; un dueño terminado por su número dejó el bloqueo, informado como «ya no está en marcha» y no borrado; un `replay` matado a mitad de camino dejó el bloqueo y un estado `running`. Ahí aparecieron B2 y los ocho veredictos de A3.

## Los seis bloqueantes y cómo se corrigieron

- **B1, tareas de una corrida que nunca cargó su propia entrega**: `task export|render_dashboard|fit_model|score|track|evaluate --run-key <una corrida que tiene una entrega sin cargar, o cargada después por otra>` reescribían los Parquet, el HTML, los resúmenes y la tabla de corridas, y `task report` fallaba con `TypeError` después de que `_close_sqlite` creara una fila sin entrega. Corregido en `f8d09fe` (D86): `load` se rechaza si otra corrida cargó la entrega, y toda tarea posterior salvo que la haya cargado esta corrida, con salida 2 y antes de escribir; el cierre nunca crea la fila.
- **B2, soltar el bloqueo en Windows**: mientras cualquier proceso tenía abierto `pipeline.lock`, `release` levantaba `PermissionError [WinError 32]`; una corrida buena salía con 1, el error de una tarea quedaba reemplazado y el bloqueo quedaba. Corregido en `7af35f2` (D87): el borrado se reintenta durante dos segundos, nunca levanta y avisa si el archivo sigue ahí. Con un lector real que tiene el archivo abierto 1,2 s, la base salía con 1 y dejaba el bloqueo; ahora sale con 0 y lo borra.
- **B3, el resumen después del sello**: una repetición con otros costos dejaba el sello intacto pero escribía un resumen que decía pendiente la prueba sellada. Corregido en `1032732` (D88): un almacén sin la prueba sellada toma los criterios y las cifras del sello, lo dice, y muestra debajo el análisis posterior al sello con sus costos.
- **B4, claves que la huella del sello no cubre**: `[costs] sensitivity_step = 0.1` (o, en modo rápido, `threshold_min = 0.5` o `bootstrap_seed = 1`) se tomaba por la configuración sellada y fallaba en M2017-10 con `SealMismatchError`. Corregido en `b5b2c08` (D89), sin cambiar `config_sha256` ni el sello: una prueba que difiere del sello solo donde actúan esas claves es otra configuración, posterior al sello, con un aviso que nombra las claves; cualquier otra diferencia sigue siendo una reproducción fallida, con los valores nombrados. Sus alternativas y su límite están en D89.
- **B5, la documentación sin sincronizar**: las ocho advertencias de H9 no estaban en `docs/review-advisories.md`, la línea del README sobre las advertencias era falsa, el paso a paso omitía la revisión de H9 y la prueba de las advertencias abiertas que nombra el plan no existía. Corregido en el último commit de esta tanda (D92).
- **B6, SciPy**: es una dependencia directa (`baselines.py` importa `scipy.stats`), pero D27 la llamaba dependencia de scikit-learn y la fila de SciPy de `LANDING-EXPANSION.md` no nombraba al 03. Corregido en el mismo commit (D92).

## Las advertencias

- **A1, la suite rápida**: 151,99 s con lecturas al lado y 147,19 s en reposo, contra los 150 s. Se abarataron tres pruebas sin quitar ninguna regla (`6f3be98`, D91): en clones nuevos, de 162,26 s y 154,27 s antes a 149,21 s y 142,19 s después, dos corridas seguidas cada vez. AC36 se cumple; los 135 s que se fijaron para esta corrección no: **abierta**. Después: 142,99 s y 140,95 s en la comprobación (`52c7eeb`, equipo en reposo) y 147,15 s y 144,38 s en `441cccd` (clon nuevo, dos corridas seguidas, 1.298 pruebas), siempre por debajo de los 150 s y por encima de los 135 s.
- **A2, un `LATEDEL_CONFIG` relativo**: se resolvía contra el directorio de trabajo. Corregido en `c1c1366` (D90): se toma de `LATEDEL_HOME` o de la carpeta del proyecto.
- **A3, las ocho advertencias de H9**: corregidas las cuatro que la evaluación reprodujo y la que no reprodujo (era barata), con prueba la rama de Windows, y rechazadas con su motivo las dos restantes (`7af35f2`, D87 y D90; `docs/review-advisories.md`, sección H9).
- **A4, `docs-sync` en modo rápido**: salía siempre con 2 y un «Errno». Corregido en `c1c1366` (D90): sigue saliendo con 2, sin escribir, y dice por qué.
- **A5, los `__pycache__` del modo rápido**: rechazado (D90): son la caché de importación de Python, ignorada por git.
- **A6, las cifras**: el pico de 991,5 MB de `PeakWorkingSetSize` de la evaluación contra los 921,6 MB declarados (`PeakWorkingSetSize`) es variación normal; la repetición real de las correcciones dio 961,4 MB de `PeakWorkingSetSize` y 2.854,5 MB de `PeakPagefileUsage` (el máximo de la tabla de corridas, con la limitación de D83). D81 y la ficha daban dos tiempos de la suite después de U25; D81 dice ahora cuál es el del commit (139,92 s). La repetición real de la comprobación, con `52c7eeb`, dio 930,8 MB de `PeakWorkingSetSize` y 2.832,0 MB de `PeakPagefileUsage` en 103,26 s; los 921,6 MB, los 105,14 s y los 2.824,1 MB de la corrida sellada siguen siendo los de esa corrida.

## La repetición real con el código corregido

Las correcciones no cambiaron ninguna salida versionada, y no se volvió a versionar nada. Para comprobarlo, en una copia fuera del árbol con el código de `c1c1366` (el último commit de código que toca lo que la corrida escribe), el sello versionado y una copia de solo lectura del CSV, se corrió `replay`, la corrida sin entrega y `figures`: las tres terminaron con 0, dieciséis corridas (15 `ok` y una `no_delivery`) en unos 105 s. Comparadas con las versionadas: el sello byte a byte igual, las siete figuras byte a byte iguales, y los 20 Parquet, los cinco archivos del sitio, los informes de evaluación y de validación y el HTML iguales salvo las columnas y los textos de la corrida (clave, horas y picos de memoria) y las celdas de memoria del HTML.

## Comprobación

Otro agente de Claude Code con Opus, distinto del que evaluó y de los que corrigieron, rehizo la prueba de los seis bloqueantes en dos clones aparte (`git clone --no-hardlinks`), uno del commit evaluado (`eef9bea`) y otro del corregido (`52c7eeb`), con 38 copias de trabajo de ellos para las sondas. Armó un entorno con Python 3.11.1 desde `requirements-dev.txt` (`pip check` limpio) y copió el CSV, cuya huella SHA-256 es la fijada (`fa6d022e…0aa6`), en seis copias. Corrió cada repetición de los bloqueantes y de las advertencias en las dos copias, repeticiones reales con otros costos (dos por copia) y una completa con el código corregido, las pruebas `real_data` y `docker`, una comparación de 34 pasos de la línea de comandos en las dos copias y, al final, la suite rápida dos veces en un equipo en reposo. El repositorio original quedó intacto. No leyó ningún `.env` ni `.env.example`, no corrió revisiones nativas, terminó solo procesos propios y por su número, y no dejó ningún contenedor, volumen ni red. Es una comprobación de las correcciones y de que no rompieran nada, no una evaluación nueva.

**Veredicto: los seis bloqueantes están cerrados; las correcciones no causaron ningún bloqueante nuevo ni una diferencia que nadie pidiera.**

- **B1, cerrado.** Primera repetición: `replay --until M2017-02`, y después `run --only fetch_source deliver` (la corrida A tiene M2017-03). En `eef9bea`, `task export --run-key A` sale con 1 pero después de reescribir los 20 Parquet (22 archivos cambiados), `render_dashboard` sale con 0 y su HTML dice «Datos de la corrida A, con fecha de cálculo 2017-03-31», y `report` falla con `TypeError: '>=' … datetime.date and 'float'` y deja una fila `(A, failed, entrega NULL)`. En `52c7eeb`, las tres salen con 2 («tiene la entrega M2017-03, pero no la cargó…») y no cambian ningún archivo. Segunda repetición: un `replay` rápido matado con `os._exit` justo después de `validate` de M2017-08, el bloqueo viejo borrado a mano y una corrida que carga M2017-08 y cierra `ok`. En `eef9bea`, `task fit_model|score|track|evaluate --run-key <la matada>` salen con 0 y escriben (6, 6, 6 y 2 archivos cambiados), `render_dashboard` sale con 0 y `report` con 1 por el mismo `TypeError`; `export` sale con 1 (`chk_star_foreign_keys: 32 filas`) y `load` con 1 (`DeliveryAlreadyLoadedError`). En `52c7eeb`, las ocho tareas salen con 2, nombran la corrida que cargó la entrega y no cambian ningún archivo.
- **B2, cerrado.** Con `lock_reader.py` (abre el bloqueo para leerlo apenas existe) sobre una corrida mensual rápida, que tiene el bloqueo 1,14 s. Un lector de 2,2 s (retrasa la liberación unos 1,1 s, dentro de los 2 s de reintento): `eef9bea` sale con 1 y `PermissionError [WinError 32]` mientras `report` dice `run_status=ok`, deja el bloqueo y la corrida siguiente sale con 2 por bloqueo viejo; `52c7eeb` sale con 0, sin aviso, borra el bloqueo y la corrida siguiente sale con 0. Un lector de 6 s (unos 4,9 s después de la liberación): `eef9bea` sale con 1 igual; `52c7eeb` sale con 0 y avisa «could not remove the lock … after 2.0 s», deja el bloqueo y la corrida siguiente sale con 2 como bloqueo viejo, que es el límite que declara D87. Con una tarea que falla (el artefacto alterado, M03) y el lector de 2,2 s: `eef9bea` sale con 1 y quien llama ve el `PermissionError` en lugar del error de M03; `52c7eeb` sale con 1 con `ArtifactMismatchError: M03…` y borra el bloqueo.
- **B3, cerrado.** Repetición real con `[costs] alert_cost = 6.0`: las dos copias salen con 0 y el sello queda en `c354eb76…`, intacto. `eef9bea` escribe «La prueba se evalúa con la entrega de 2017-10…», con `state: pending` y 0 criterios. `52c7eeb` lo escribe `evaluated`, con 9 criterios (C03 y C05 fallan, como está declarado), `sealed_from: model_seal.json`, las filas C01 a C09 idénticas a las de `reports/evaluation_summary.md` versionado y un bloque posterior al sello con «Costos de esta configuración: alerta 6,0…». La repetición rápida con `alert_cost = 6` se comporta igual: pendiente en `eef9bea`, evaluado en `52c7eeb`.
- **B4, cerrado.** Repetición real con `sensitivity_step = 0.1`: `eef9bea` sale con 1 en M2017-10 por `SealMismatchError`; `52c7eeb` sale con 0, lo guarda como posterior al sello con un aviso que nombra las claves de C09, deja el sello igual y toma los criterios sellados del sello. Repeticiones rápidas desde cero sobre un sello rápido con `bootstrap_seed = 1`, `threshold_min = 0.5` y `sensitivity_step = 0.1`: las tres salen con 1 por `SealMismatchError` en `eef9bea`; en `52c7eeb` las tres salen con 0 después de 15 corridas `ok`, con 9 criterios del sello y 10 posteriores al sello, el sello byte a byte igual y el aviso con las claves.
- **B5, cerrado salvo lo que esta sincronización cerraba.** `docs/review-advisories.md` tenía la sección de H9 con sus ocho resoluciones, la de la evaluación y el encabezado con H9; la línea del README cubría la evaluación; `docs/paso-a-paso.md` contaba la revisión de H9 en la sección 11 y la evaluación en la 14; `test_open_advisories_match_review_file` existía y pasaba, y «Advertencias abiertas: `A1`» del README coincidía con la única resolución **abierta**. Quedaban, como se sabía, las 1.294 pruebas declaradas contra las 1.296 recolectadas (la única prueba que fallaba en la suite rápida), los tiempos medidos en `6f3be98`, las advertencias de las revisiones de las correcciones y esta sección.
- **B6, cerrado.** D27 dice que SciPy es una dependencia directa (`baselines.py`, línea 24; `chi2` y `chi2_contingency`) y la fila de SciPy de `LANDING-EXPANSION.md` nombra al 03.

**Las advertencias corregidas.**

- **A2, corregida.** Corrido desde la carpeta de una copia Y con `LATEDEL_CONFIG=config/pipeline.quick.toml` y `PYTHONPATH` en una copia X: `eef9bea` manejó el `data/quick` de Y y dejó X intacta; `52c7eeb` manejó X y dejó Y intacta.
- **A4, corregida.** Las dos copias salen con 2 y no escriben nada; `eef9bea` da un `[Errno 2]` pelado y `52c7eeb` dice que el modo rápido no tiene README y dónde queda su resumen.
- **A3, las advertencias de H9, corregidas.**

| Advertencia | `eef9bea` | `52c7eeb` |
| --- | --- | --- |
| `R4-pid-reuse-false-alive` (dueño matado cuyo número se reescribió a un proceso vivo) | «Otro proceso escribe… Espere» | bloqueo viejo: «el número … lo tiene ahora otro proceso, que empezó después» |
| `R4-replay-noop-requires-write` (`replay` sin nada pendiente mientras otro proceso tiene el bloqueo) | sale con 2 | sale con 0, «No hay entregas pendientes», 0 archivos |
| `R4-replay-lock-run-key-granularity` (bloqueo de un `replay` matado) | `"command": "replay", "run_key": null` | `"command": "run"` con la corrida en curso y `started` |
| `R3-003` (un segundo hilo) | rechazado como `alive` | rechazado como `thread` |
| `R4-lock-toctou-false-unreadable` (3.000 intentos disputados) | 1 `unreadable` (su mensaje dice «bórrelo») y 4 `PermissionError` al soltar | 0 y 0 |

  La prueba de Windows de `R3-001` existe y pasa. La comprobación reprodujo en `eef9bea`, una vez, el caso del `R4-lock-toctou-false-unreadable` que la evaluación no había reproducido en 6.000 intentos.
- **Las advertencias de las revisiones de las correcciones que reprodujo.** `R3-001` de la revisión de B4 (las filas de V2 se guardan después del sello, así que un `SealMismatchError` genuino las pierde): solo cambia el código, no se pierde nada. Con `b2_mode.pr_auc` del sello rápido alterado en +0,01, las dos copias salen con 1 en M2017-10 (`52c7eeb` nombra ahora `b2_mode.pr_auc` en el error) y las tablas de V2 son idénticas por huella (95 filas de métricas, 30 de ROC, 16 de calibración y 51 de sensibilidad), porque V2 queda fijo después del lanzamiento y cada corrida anterior ya guardó las mismas filas. `R3-load-allow-branch-unproved` de la revisión de B1: no se reprodujo; `load` sigue corriendo y termina bien (`run --run-key A --from validate` sale con 0, carga 86 pedidos con `resumed=False` y la corrida cierra `ok`; el paso 16 de la comparación, `run --from load` sobre una corrida que solo validó, también sale con 0).

**Las suites y la repetición real.**

| | Resultado | Tiempo |
| --- | --- | --- |
| Suite rápida, corrida 1 (copia nueva, equipo en reposo) | 1.294 pasaron, 1 saltada, 1 falló, 31 descartadas | 142,99 s |
| Suite rápida, corrida 2 | igual | 140,95 s |
| `-m real_data` (copia nueva, solo el CSV) | 28 pasaron, 1 falló | 182,79 s |
| `-m docker` | 2 pasaron; ningún contenedor, volumen ni red quedó | 74,50 s |
| `replay` real con `52c7eeb` | salida 0, 15 corridas; 930,8 MB de `PeakWorkingSetSize`, 2.832,0 MB de `PeakPagefileUsage` | 103,26 s |
| `run` sin entrega / `figures` | salida 0 / salida 0 | 2,25 s / 3,1 s |

- **La suite rápida** tuvo una sola falla, `test_stated_test_count_matches_collection` (1.294 declaradas contra 1.296 recolectadas), la sincronización pendiente que esta documentación cierra; la saltada es la del modo 0o600 del sello en Windows. Las dos corridas cumplen los 150 s de AC36.
- **La falla de `real_data`** es `test_real_counts_seal_and_summary_consistency`. En una copia nueva falla porque falta el archivo de estado de la corrida original, ignorado por git; después de la repetición real falla solo en las claves de corrida, como dice el README. Esa prueba no cambió.
- **La repetición real contra lo versionado**: el sello y las siete figuras, byte a byte iguales; los 20 Parquet, iguales salvo las columnas de la corrida (solo en `dim_pipeline_run`, `fact_order` y `fact_validation`); los 9 JSON y Markdown, iguales salvo las claves de corrida y las horas, y el HTML salvo las celdas de memoria; `manifest.json` solo cambia en el SHA-256 de esos archivos.
- **La comparación de 34 pasos** (`run`, `task`, `replay`, `docs-sync`, `figures` y rechazos con un dueño del bloqueo vivo y con uno matado): 26 son idénticos; 5 cambian en lo que las correcciones pedían (B1 `export` 1→2 y `report` 1→2, el mensaje de A4 y dos `replay` sin nada pendiente con el bloqueo tomado, 2→0); 3 cambian solo en los bytes impresos de la exportación y del HTML, de 1 a 19, que son telemetría de memoria. Las salidas finales de las dos copias son iguales salvo las columnas de la corrida, las claves, las horas y las celdas de memoria. Ninguna diferencia que nadie pidiera.

**Lo que encontró nuevo.** Ningún bloqueante y tres hallazgos:

- **El hueco de `task load`, real, anterior a las correcciones y no documentado.** Igual en las dos copias: `replay --until M2017-02`, `run --only fetch_source deliver` (corrida A), y `task load --run-key A` escribe los 86 pedidos y la fila de `deliveries` (cargada por A) antes de leer el informe de validación; después sale con 2 y un `[Errno 2] … validation/history/A.json`. `task score --run-key A` escribe 86 puntajes (40 alertas) y sale igual. `run --run-key A --from validate` se rechaza con 3 (V20 y V22: la entrega ya está cargada), una corrida nueva sale con 1 por el `RunStateError` de R4 que apunta a A, y `run --run-key A --from load` sale con 0: la corrida queda `ok` aunque su informe de validación dice «Rechazado: hay reglas bloqueantes que fallan». El runbook R3 decía lo contrario (una entrega rechazada no carga nada; «un rechazo o una falla anterior a la carga no dejan nada a medias») y el plan (sección 5) pone `validate` antes de `load`. Era una advertencia y no un bloqueante porque se reproduce igual en `eef9bea`; la corrección mínima que propuso es rechazar `load`, con salida 2 y antes de escribir, salvo que el `validate` de la corrida haya terminado bien.
- **D1, dos números de sección equivocados** en `docs/review-advisories.md`: la evaluación está en la sección 14 de `docs/paso-a-paso.md` y no en la 12 (que es el README, U26). Corregidos en esta sincronización.
- **Una comprobación del sello más estricta**, informativa: `report` lee ahora el sello en cada corrida de una repetición desde cero, y un sello cuya parte de la prueba no devuelve sus propios criterios falla en el lanzamiento con `SealError` («no da su propio C01/C07»), donde `eef9bea` fallaba en M2017-10. Falla del lado seguro, D88 lo declara y no necesita corrección.

**La corrección del hueco.** La hizo la sesión que orquestaba, en línea y con la prueba primero (`441cccd`, D93): `load` se rechaza con salida 2 y antes de escribir si no existe el informe de validación de la corrida o si una regla bloqueante falla en él, y el runbook R3 lo dice. Las dos pruebas nuevas fallaron antes (`load` escribía y fallaba después con `FileNotFoundError`, y una entrega rechazada se cargaba), una mutación que quita el rechazo cae por su prueba en una copia completa del proyecto, y las pruebas del flujo, del endurecimiento, de la línea de comandos y del bloqueo pasaron (136). Su revisión nativa (`52c7eeb..441cccd`, ítem 1) aprobó con tres advertencias, resueltas en `docs/review-advisories.md`. No se repitió la repetición real con `441cccd`: su cambio solo suma un rechazo antes de escribir, que una corrida con la validación aprobada no toca. La suite rápida en `441cccd`, en un clon nuevo y dos veces seguidas: 1.298 pruebas, 147,15 s y 144,38 s (AC36: contra 150 s).

## Lo que queda

A1 (la suite por encima de los 135 s fijados), que decide el dueño; las otras cuatro advertencias abiertas de las revisiones de las correcciones (`R3-retrying-exc-info-cleared`, `R3-seal-mismatch-untested`, `R3-pending-why-branch-untested` y `R3-002` de B4: una prueba o un aviso que faltan, en `docs/review-advisories.md`); la página del sitio; la corrida programada de Airflow sobre el archivo real; el `.pbix`, y lo que el plan deja al dueño (sección 26).
