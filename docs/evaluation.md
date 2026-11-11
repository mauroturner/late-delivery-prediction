# Evaluación final independiente

Qué se evaluó, quién lo evaluó, qué encontró y cómo se resolvió cada hallazgo. Es el control que el proyecto dejó para el final (plan, sección 25): lo hizo un agente que no escribió el código, sobre una copia aparte del commit `eef9bea`, y sus hallazgos se corrigieron después con la prueba primero.

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

- **A1, la suite rápida**: 151,99 s con lecturas al lado y 147,19 s en reposo, contra los 150 s. Se abarataron tres pruebas sin quitar ninguna regla (`6f3be98`, D91): en clones nuevos, de 162,26 s y 154,27 s antes a 149,21 s y 142,19 s después, dos corridas seguidas cada vez. AC36 se cumple; los 135 s que se fijaron para esta corrección no: **abierta**.
- **A2, un `LATEDEL_CONFIG` relativo**: se resolvía contra el directorio de trabajo. Corregido en `c1c1366` (D90): se toma de `LATEDEL_HOME` o de la carpeta del proyecto.
- **A3, las ocho advertencias de H9**: corregidas las cuatro que la evaluación reprodujo y la que no reprodujo (era barata), con prueba la rama de Windows, y rechazadas con su motivo las dos restantes (`7af35f2`, D87 y D90; `docs/review-advisories.md`, sección H9).
- **A4, `docs-sync` en modo rápido**: salía siempre con 2 y un «Errno». Corregido en `c1c1366` (D90): sigue saliendo con 2, sin escribir, y dice por qué.
- **A5, los `__pycache__` del modo rápido**: rechazado (D90): son la caché de importación de Python, ignorada por git.
- **A6, las cifras**: el pico de 991,5 MB de `PeakWorkingSetSize` de la evaluación contra los 921,6 MB declarados (`PeakWorkingSetSize`) es variación normal; la repetición real de las correcciones dio 961,4 MB de `PeakWorkingSetSize` y 2.854,5 MB de `PeakPagefileUsage` (el máximo de la tabla de corridas, con la limitación de D83). D81 y la ficha daban dos tiempos de la suite después de U25; D81 dice ahora cuál es el del commit (139,92 s).

## La repetición real con el código corregido

Las correcciones no cambiaron ninguna salida versionada, y no se volvió a versionar nada. Para comprobarlo, en una copia fuera del árbol con el código de `c1c1366` (el último commit de código que toca lo que la corrida escribe), el sello versionado y una copia de solo lectura del CSV, se corrió `replay`, la corrida sin entrega y `figures`: las tres terminaron con 0, dieciséis corridas (15 `ok` y una `no_delivery`) en unos 105 s. Comparadas con las versionadas: el sello byte a byte igual, las siete figuras byte a byte iguales, y los 20 Parquet, los cinco archivos del sitio, los informes de evaluación y de validación y el HTML iguales salvo las columnas y los textos de la corrida (clave, horas y picos de memoria) y las celdas de memoria del HTML.

## Comprobación

La comprobación de estas correcciones por un agente que no las hizo está pendiente.

## Lo que queda

La comprobación de las correcciones; A1 (la suite por encima de los 135 s fijados), que decide el dueño; la página del sitio; la corrida programada de Airflow sobre el archivo real; el `.pbix`, y lo que el plan deja al dueño (sección 26).
