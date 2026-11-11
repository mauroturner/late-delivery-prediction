# Runbook: qué hacer cuando algo no sale como se esperaba

Todos los comandos son para Windows (PowerShell), dentro de la carpeta `03-Logistics-Late-Deliveries/` y con el entorno del proyecto. El paquete no está instalado en el entorno: una vez por sesión,

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python -m latedel --help
```

Códigos de salida de la CLI: `0` terminó bien (también una corrida sin entrega pendiente), `1` falló una tarea, `2` uso o configuración incorrectos (también una tarea rechazada porque su corrida está cerrada, porque otro proceso tiene la carpeta de datos o porque se pidió `load` de una entrega que su corrida no validó o que su validación rechazó, R3), `3` la entrega o la fuente fueron rechazadas por la validación. Cada corrida deja su estado en `data/interim/runs/<run_key>.json` (con la traza completa si falló; la CLI la muestra con `-v`) y, si llegó a cargar datos, una fila en `pipeline_runs` del almacén (`data/warehouse/latedel.sqlite`). Cada validación deja su informe en `reports/validation/latest.md` y, por corrida, en `reports/validation/history/<run_key>.md` (no se versiona).

Tres principios valen para todo el documento:

- **Un rechazo o una falla anterior a la carga no dejan nada a medias.** Si algo falla antes de `load`, el almacén, los Parquet, los JSON del sitio, el HTML y el sello quedan byte a byte como estaban (AC11); si falla después, la corrida queda `failed` en SQLite y en lo exportado, y se retoma con su misma clave: `python -m latedel run --run-key <run_key> --from <tarea>`. Si el estado de una corrida trae `exports_stale`, la corrida terminó como dice SQLite pero no se pudieron reescribir los archivos exportados (el aviso del registro trae la causa); la corrida siguiente los vuelve a escribir.
- **Una corrida cerrada no se vuelve a abrir.** Cualquier tarea de una corrida `ok` o `no_delivery` se rechaza con código `2` antes de escribir nada (AC28): la entrega siguiente es otra corrida.
- **El sello no se toca.** `reports/model_seal.json` guarda la configuración del lanzamiento y el primer resultado de la prueba; ninguna tarea lo reescribe con otros valores, y una evaluación con otra configuración se informa aparte, como posterior al sello (D17). Nada de este documento pide editarlo a mano.

## R1 La huella de la fuente cambió

**Síntoma.** `fetch_source` termina con código `3` y un mensaje como «el CSV … tiene la huella SHA-256 X y la aceptada es Y; no se cambió nada (runbook R1)». La regla V01 figura como fallida en `reports/validation/latest.md`. En modo local, el mismo mensaje dice «la registrada» en lugar de «la aceptada».

**Qué significa.** El archivo que hay en `data/raw/`, o el que llegó por la dirección configurada, no es el que se evaluó. Mendeley publica el SHA-256 de cada archivo en su API de archivos (`fa6d022e…0aa6` para la versión 5); la descarga se escribe en un archivo de preparación propio y solo se renombra si la huella coincide, así que no se tocó nada: ni el CSV aceptado, ni los archivos intermedios, ni el almacén.

**Qué hacer.**

1. Calcular la huella del archivo y compararla con la aceptada (`[source] accepted_sha256` en `config/pipeline.toml`): `Get-FileHash -Algorithm SHA256 data\raw\DataCoSupplyChainDataset.csv`.
2. Si el archivo está cortado o dañado, borrarlo y volver a correr: el flujo lo descarga de nuevo (R2).
3. Si es una versión nueva y legítima del conjunto (R11), **no cambiar la huella sin más**: los criterios, los recuentos y el sello son de la versión 5. Registrar una decisión nueva en `docs/decisions.md`, cambiar `accepted_sha256`, reconstruir desde cero (R10) y tratar el resultado como un análisis nuevo, posterior al sello.
4. En modo local (la fuente sintética de las pruebas y del modo rápido) la primera huella se registra en `source_manifest.json` y las siguientes deben coincidir: el mensaje quiere decir que se cambió el archivo local. Se borra `data/quick/` y se vuelve a empezar.

## R2 Descarga fallida o redirección rechazada

**Síntoma.** `fetch_source` falla con un error de descarga: un `DownloadDeadlineError`, «La descarga superó el tiempo máximo de 900 s (source.max_seconds) tras recibir N bytes de …; se interrumpió y no se guardó nada», «… respondió HTTP 5xx», «la descarga anuncia N bytes, por encima del límite de …», o con código `3` y V02: «el host '…' de … no está en la lista de hosts permitidos […] ([source] allowed_hosts)», «la dirección … no es HTTPS» o «la descarga pide más de 3 redirecciones».

**Qué pasó.** La descarga sigue cada redirección a mano: cada salto debe ser HTTPS, ir a un host de `[source] allowed_hosts` (`data.mendeley.com` y el depósito exacto de Amazon S3 al que Mendeley redirige hoy) y no pasar de tres redirecciones ni de 128 MiB. Se escribe en un archivo de preparación de nombre propio (`…csv.<16 caracteres>.staging`) y, ante cualquier falla, se borra; el CSV, los archivos intermedios y el manifiesto no cambian.

**Qué hacer.**

1. Reintentar. Airflow reintenta `fetch_source` tres veces con 10 minutos de espera; en la CLI, `python -m latedel run`. La descarga normal tarda unos 20 s.
2. **El tiempo máximo es también un piso de velocidad** (D49, D57): `source.max_seconds = 900` exige 149 kB/s para el tope de 128 MiB y 107 kB/s para el archivo fijado de 95.910.149 bytes. En un enlace más lento se sube `max_seconds` (segundos = bytes / velocidad sostenida, con margen). El error nombra el límite, los bytes recibidos y el host en curso (o el salto de la redirección que se esperaba).
3. **Un host fuera de la lista** puede querer decir que Mendeley cambió el depósito de S3 al que redirige: el nombre del host nuevo está en el mensaje. Se agrega a `allowed_hosts` solo después de comprobar con la API de archivos de Mendeley que la huella publicada sigue siendo `fa6d022e…0aa6` (R11); la huella fijada protege el contenido aunque el host cambie.
4. Si el error no habla de la red sino del disco o los permisos (`No space left on device`, `PermissionError`), es una falla local: el error conserva su propio tipo y no dice que se cortó la conexión. Reintentar no sirve; liberar espacio o corregir el permiso de `data/raw/`.
5. Si ya se tiene el archivo completo, copiarlo a `data\raw\DataCoSupplyChainDataset.csv`: el flujo solo descarga si falta y siempre comprueba la huella (V01).
6. Un `HTTP 404` es otro caso: ver R11.

## R3 Entrega rechazada

**Síntoma.** La corrida termina con código `3` en `fetch_source` o en `validate`, con «rechazada: …» y las reglas bloqueantes que fallaron. En `data/interim/runs/<run_key>.json` el estado es `rejected` y la entrega queda `rejected` en `data/raw/deliveries/manifest.json`. No hay fila nueva en `pipeline_runs`. Hay un segundo síntoma, con código `2` y sin escribir nada: `task load` o `run --from load` dicen «La corrida … tiene la entrega …, pero no se validó» o «La validación de la corrida … rechazó la entrega … (…): la tarea load no carga una entrega rechazada».

**Qué significa.** La entrega o la fuente no cumplen el contrato y no se cargó nada. Bloquean V01 a V09 (la fuente: huella, descarga, codificación, encabezado, campos y tipos, correo y contraseña enmascarados, coherencia del pedido, etiqueta y plazo del resultado) y V20 a V24 (la entrega: es la siguiente del plan y empieza el día siguiente a la anterior; cada pedido y cada resultado caen en su período; ninguno está ya cargado; la entrega no está vacía y sus filas y su SHA-256 son los del manifiesto), además de las reglas del modelo M01 a M05.

**Qué hacer.**

1. Leer el motivo en `reports/validation/latest.md`: cada regla trae los recuentos de filas y de pedidos afectados.
2. La entrega es una derivación de los archivos intermedios, así que volver a correr la publica idéntica y se rechaza otra vez hasta que se corrija la causa: V01 a V09 son de la fuente (R1, R2), V20 a V24 apuntan a un manifiesto o a archivos intermedios que no coinciden (R4).
3. No se edita el almacén ni el manifiesto para que pase: una entrega `rejected` sigue siendo la pendiente y se vuelve a intentar completa cuando se corrige la causa.
4. **Los archivos intermedios se comprueban en cada corrida** (D50): el manifiesto de `data/interim/` lleva el SHA-256 de los tres archivos y se compara con lo que hay en disco; son 5 ms sobre los 5,8 MB reales, al ritmo del SHA-256, así que no es un costo que haya que evitar ni se reemplaza por una lectura de metadatos. Si no coinciden, se reconstruyen desde la fuente (R10).
5. **`load` no carga una entrega que no pasó su validación** (D93): se rechaza, con código `2` y antes de escribir nada, si no existe el informe de validación de la corrida (`reports/validation/history/<run_key>.json`: `validate` no corrió, por ejemplo después de `run --only fetch_source deliver`) o si una regla bloqueante falla en él. El almacén, los Parquet y el manifiesto quedan como estaban. Si no se validó, se corre `python -m latedel run --run-key <run_key> --from validate`, que valida, carga y sigue. Si la validación rechazó la entrega, se lee el motivo en el mensaje y en `reports/validation/latest.md`, se corrige la causa (puntos 2 y 3) y la entrega se vuelve a intentar completa; correr `load` otra vez no la carga. El rechazo cubre los dos usos, `task load` y `run --from load`.
6. Las advertencias (V10 a V14 de la fuente, y M05 entre las del modelo) no rechazan: la entrega se carga y el informe cuenta las filas y los pedidos de cada una. En la corrida real advirtieron V11 (los 2.855 pedidos cancelados, 7.754 líneas) y V14 (1.774 líneas con espacios finales en el nombre del producto); las informativas V15 a V18 cuentan lo que se descartó o se apartó.

## R4 Entregas fuera de orden o faltantes

**Síntoma.** Un error que termina en «R4»: «La entrega … ya está cargada en el almacén por la corrida R (estado …), pero el manifiesto no la marca cargada», un rechazo de V20 (la entrega no es la siguiente del plan) o de V24 con su motivo: `empty`, `rows_differ_from_manifest`, `sha256_differs_from_manifest` o `unreadable_files`.

**Qué significa.** El plan de quince entregas (el lanzamiento `L2017-01-07`, `M2017-01` a `M2018-01` y el cierre `C2018-02`) no se salta ni se reordena. El caso más común es una corrida que cargó y murió antes de cerrar: el almacén ya tiene la entrega, pero el manifiesto todavía no la marca `loaded` (eso lo hace `report`, en el último paso del cierre). V24 informa por separado una entrega vacía, filas o una huella distintas de las del manifiesto y archivos que no se pueden leer; un Parquet que se lee pero no tiene las columnas ni los tipos del contrato es `unreadable_files`, y el informe es el de V24 sola, con el nombre del archivo (D56).

**Qué hacer.**

1. Si el mensaje nombra la corrida que cargó la entrega, retomarla en lugar de empezar otra: `python -m latedel run --run-key <esa corrida> --from <la tarea que falta>` (normalmente `report`). Repetir `report` termina el cierre desde cualquier paso.
2. **Retomar una corrida cuyo guardado falló** (D79): si la escritura del resultado de una tarea falla (un disco lleno), la corrida queda `failed` con esa tarea y se retoma desde ella con su misma entrega; una corrida nueva se rechaza mientras tanto si la entrega ya está en el almacén. Si la tarea ya había cerrado la corrida (`report`, o `deliver` sin entregas), nada dice `failed`: el registro dice qué correr, y repetir esa tarea registra el final.
3. `retries`, `retry_delay_min` y `timeout_min` de cada tarea solo los aplica Airflow; la línea de comandos corre cada tarea una vez. Ctrl+C en una tarea deja la corrida `failed` y abierta para retomarla.
4. Si el manifiesto se tocó a mano o se borró una entrega publicada, no se arregla a mano: se reconstruye desde cero (R10).

## R5 La trampa de fuga detuvo el lanzamiento

**Síntoma.** El lanzamiento falla en `fit_model` con «M01: el AUC del candidato elegido en V1 es X, igual o mayor que el umbral de 0.95: posible fuga de información (el conjunto …). El lanzamiento se detiene (docs/runbook.md, R5)», o con `LeakageError` al armar las variables, o con `BlockLabelError`: «el bloque … tiene una sola clase de etiqueta (todos llegaron tarde / ninguno llegó tarde, N pedidos)».

**Qué significa.** M01 es la segunda línea de defensa detrás de las guardas de las variables (D36): con lo que se sabe al crear un pedido, el AUC en V1 fue 0,776, y uno de 0,95 o más quiere decir que una columna posterior al pedido llegó a la matriz. Un `LeakageError` de `build_features` quiere decir lo mismo, antes de ajustar nada. Un bloque con una sola clase (D65) no es una fuga sino un problema de la partición o de los datos: el mensaje nombra el bloque (`train`, `valid_v1` o `valid_v2`) y si todos llegaron tarde o ninguno.

**Qué hacer.**

1. No subir el umbral de M01 ni quitar la guarda. Ver qué conjunto se eligió y qué columnas tiene la matriz (`FEATURE_SETS` en `src/latedel/features.py`) y comparar con `schema.py`: ninguna de las ocho columnas de `LEAKING_COLUMNS` puede estar.
2. Para un bloque con una sola clase, revisar las fechas de `[split]` y la fuente antes de cambiar nada: con el archivo real los siete bloques tienen las dos clases.
3. No se registró nada: el sello, el registro de modelos y los puntajes quedan como estaban, y la corrida se repite completa una vez corregida la causa.

## R6 El artefacto no coincide con su huella

**Síntoma.** `fit_model` o `score` fallan con «M03: el artefacto … tiene la huella X y la registrada es Y: no se deserializó».

**Qué significa.** El archivo del modelo en `data/models/` no es el que registró el lanzamiento. El modelo se guarda con `pickle`, que ejecuta código al cargarse, así que el flujo compara el SHA-256 de los bytes con el registrado **antes** de deserializarlo (D30) y no lo carga.

**Qué hacer.**

1. No cargar el archivo a mano para «ver qué tiene».
2. Restaurar el archivo registrado si hay una copia de confianza (su huella está en `reports/model_seal.json`, `artifact_sha256`, y en `model_registry`).
3. Si no la hay, reconstruir desde cero (R10): el lanzamiento con la misma configuración vuelve a dar los mismos bytes del artefacto y la misma huella (D54).

## R7 La evaluación no reproduce el sello

**Síntoma.** `fit_model` o `evaluate` fallan con `SealMismatchError`: «la evaluación de la prueba con la configuración sellada no reproduce los valores sellados, redondeados a 6 decimales (docs/runbook.md, R7)», «la evaluación sellada ya guardada en … no coincide con la que se acaba de calcular», o «otra escritura reemplazó la parte de la prueba mientras se escribía la de esta corrida».

**Qué significa.** Una repetición con la configuración sellada dio otros valores: es un fallo de determinismo (las versiones, la fuente o el código cambiaron), no algo que se resuelva solo. El tercer mensaje quiere decir que dos corridas escribieron resultados distintos con la misma configuración (D64): el archivo conserva lo que dejó la última escritura, el registro nombra las dos corridas (la que escribió y la que quedó en el archivo, D71) y el reintento de la que fue pisada vuelve a fallar mientras el archivo conserve el otro resultado. Eso es lo buscado: se investiga antes de seguir.

**Qué hacer.**

1. No editar el sello. Comprobar que las versiones son las de `requirements.txt` (`pip check`, `pip freeze`), que la huella de la fuente es la aceptada y que el código es el del commit sellado.
2. Repetir desde cero en una copia aparte (R10) y comparar con el sello: con la misma configuración, la repetición da `match`.
3. **El modo del archivo del sello** (D71, D78): en POSIX el sello se crea solo para su dueño (0o600); en Windows, crearlo registra un aviso que dice que el modo 0o600 no es allí un control de acceso y nombra la carpeta (`reports/`) cuya lista de acceso decide quién lo lee. Es lo esperado, no un error: lo que se revisa es esa lista de acceso.
4. En un sistema de archivos sin enlaces duros el sello se crea de forma exclusiva, y una escritura interrumpida no deja un medio sello (D64).

**Después de la evaluación independiente (D89).** `SealMismatchError` nombra ahora los valores que no se reproducen («… redondeados a 6 decimales: model.auc, C01.passed …»): esos valores solo dependen de la configuración que cubre la huella, de los datos y del modelo, así que es una reproducción fallida de verdad. Si la diferencia está solo en valores que cambian claves que la huella no cubre (`[costs] threshold_*` y `sensitivity_*`, `[evaluation] bootstrap_*` y `calibration_bins`, `[features] noon_hour`), la corrida no falla: se guarda como posterior al sello y el registro lo avisa con las claves («another configuration, not a failed reproduction»). Si ninguna de esas claves cambió, ese aviso es en realidad una reproducción fallida (un cambio de código en esos cálculos): compare la configuración con la sellada antes de seguir.

## R8 Airflow no arranca

**Síntoma.** `docker compose up` falla, el servicio `airflow-init` sale con un código distinto de 0 o la interfaz no aparece en `http://localhost:8080`.

**Qué pasó.** El compose exige cada secreto desde el entorno, sin valor por defecto (`${VAR:?}`): `FERNET_KEY`, `AIRFLOW__API_AUTH__JWT_SECRET`, `_AIRFLOW_WWW_USER_PASSWORD` y `LATEDEL_DB_PASSWORD`, la contraseña de la base de metadatos (en el servicio de PostgreSQL y dentro de la cadena de conexión). Sin ellas, Compose se detiene y dice cuál falta. El script de inicialización empieza con `set -euo pipefail`: una migración fallida lo detiene con un código distinto de 0 y los demás servicios no arrancan (`686de92`).

**Qué hacer.**

```powershell
cd airflow
copy .env.example .env
# completar en .env las cuatro claves, que no tienen valor por defecto
docker compose up airflow-init
docker compose up -d
```

1. **Montajes** (D80): `./dags` y `./plugins` se montan de solo lectura; `./config` (Airflow crea allí `airflow.cfg`) y `./logs` son los únicos escribibles de `airflow/`, y `../src`, `../config` y `../sql` entran de solo lectura.
2. **`AIRFLOW_UID`**: en Linux va en `airflow/.env` con el id propio (`id -u`), o las carpetas que crean los montajes quedan del uid 50000; Docker Desktop en Windows y macOS no lo necesita. La línea de `AIRFLOW_UID` en `airflow/.env.example` queda pendiente del dueño.
3. **El servicio de depuración**: `docker compose --profile debug run --rm airflow-cli airflow dags test latedel_monthly` corre fuera del planificador y de su guarda de una corrida y una tarea activas. Cada tarea toma el bloqueo de la carpeta de datos mientras corre (R9, D82), pero entre dos tareas de una corrida del planificador nadie lo tiene: se corre con el planificador detenido (`docker compose stop airflow-scheduler`, y `docker compose start airflow-scheduler` después), o habría dos escritores del almacén SQLite.
4. `pytest -m docker` prueba esta parte de punta a punta en modo rápido, con claves de usar y tirar: construye la imagen, corre `pip check` y dos `airflow dags test` y deja todo como estaba.

## R9 `database is locked` o la carpeta de datos tomada por otro proceso

**Síntoma.** Una tarea falla con `sqlite3.OperationalError: database is locked`, o se rechaza con código `2` y un `RunLockedError`: «Otro proceso escribe esta carpeta de datos: …», «El bloqueo … quedó de un proceso que ya no está en marcha: …», «Un proceso de otro equipo o contenedor tiene esta carpeta de datos: …» o «El bloqueo … existe pero no se puede leer quién lo tiene».

**Qué significa.** El almacén es un archivo SQLite con un solo escritor (D20, D61). Cada punto de entrada del flujo toma `data/interim/pipeline.lock` (`data/quick/interim/` en modo rápido) antes de su primera escritura y lo suelta al terminar, sea cual sea el final (D82); el archivo dice el proceso, el equipo, el comando, la corrida y desde cuándo. Un segundo proceso se rechaza sin escribir nada. Solo un proceso matado sin aviso (un `kill -9`, un corte de luz) deja el bloqueo, y nadie más que una persona lo borra: un contenedor y el equipo que lo corre comparten la carpeta pero no sus procesos. `database is locked` quiere decir que otro programa (un visor de SQLite, un antivirus, una copia de respaldo) tiene el archivo abierto.

**Qué hacer.**

1. Si el mensaje dice que el proceso sigue en marcha, esperar a que termine y volver a correr. Nunca se borra el bloqueo de un proceso que sigue.
2. Si dice que ya no está, o que es de un contenedor que ya terminó (`docker compose ps` y los registros de la tarea), comprobar que nada más escribe la carpeta (tampoco un contenedor que la monte), borrar `data\interim\pipeline.lock` y retomar la corrida que quedó a medias: `python -m latedel run --run-key <corrida> --from <tarea>`.
3. Para `database is locked`, cerrar el programa que tiene abierto `data\warehouse\latedel.sqlite` y retomar la corrida desde la tarea que falló.
4. Con Airflow, el planificador corre una corrida y una tarea a la vez; `airflow dags test` del servicio de depuración se corre con el planificador detenido (R8).

**Después de la evaluación independiente (D87).** El bloqueo dice también cuándo empezó su proceso (`started`): si el número de proceso está vivo pero lo tiene otro proceso que empezó después, el mensaje lo dice como un bloqueo que quedó de un proceso que ya no está en marcha. Durante un `replay` el bloqueo nombra la corrida en curso (`run` y su clave). Otro hilo del mismo proceso se rechaza como otro proceso («Otro hilo de este mismo proceso escribe esta carpeta de datos»). Un `replay` sin entregas pendientes no toma el bloqueo y termina con 0. Si al terminar no se pudo borrar el bloqueo porque otro proceso tenía el archivo abierto, la corrida termina con su resultado y el registro avisa «could not remove the lock … remove it by hand once nothing writes the folder»: es el mismo caso que el de un proceso matado, y se resuelve igual (pasos 1 y 2).

## R10 Reconstruir desde cero, con el sello

**Síntoma.** Se quiere repetir la corrida completa, el almacén lo creó una versión anterior («La tabla … no coincide con su definición en sql/schema/…: … se reconstruye desde cero»), los archivos intermedios no coinciden con su manifiesto o una partición de `[split]` se rechaza.

**Qué hacer.**

```powershell
# con Airflow apagado (docker compose down) y ningún otro proceso del flujo en marcha
Remove-Item -Recurse -Force data\interim, data\warehouse, data\models, data\raw\deliveries
# el CSV (data\raw\DataCoSupplyChainDataset.csv) se conserva: es la fuente
.venv\Scripts\python -m latedel replay
.venv\Scripts\python -m latedel run
```

1. **El sello versionado se conserva**: la repetición con la misma configuración da `match` en la parte del lanzamiento y en la de la prueba y no lo reescribe; con otra configuración, el resultado se guarda como posterior al sello. Los Parquet, los JSON del sitio, el HTML y los reportes se vuelven a escribir con el mismo contenido, salvo las columnas de la corrida (RNF01). Una repetición en una copia aparte, el 2026-11-11, dio el sello y las siete figuras byte a byte iguales y el mismo resumen salvo la clave de la corrida.
2. **Un lanzamiento interrumpido** deja uno de tres estados (solo el sello; el sello y el artefacto; todo), y repetirlo termina el mismo lanzamiento con el sello byte a byte igual (D62); un sello perdido con el modelo registrado se vuelve a escribir desde el registro en la corrida del lanzamiento. La tarea `score` puede relanzarse mientras otra todavía corre: inserta lo que falta y respeta lo que la otra puntuó (D63).
3. **Los bordes de la partición** para el modelo estrella se cargan de `[split]` en la tabla `ref_block` cada vez que se exporta (D70): un borde se mueve en la configuración y en ningún archivo SQL del modelo estrella (el mart operativo `mart_labelled_orders.sql` sí los lleva, D46). Una partición con un bloque sin días o con dos bloques que comparten un día se rechaza antes de construir nada (`ValueError` que nombra los bloques, D78), y el almacén y los Parquet quedan como estaban.
4. **`SqliteFeatureError`** quiere decir que el SQLite de ese Python no tiene `json_array` y `json_extract` (hace falta 3.38 o posterior, o JSON1): se usa el Python 3.11 del proyecto o la imagen de Airflow (D78).
5. **Los archivos del sitio** (D77): `outputs/site/manifest.json` nombra los cinco archivos de una exportación con su tamaño y su SHA-256, y se escribe al final. Si `verify_site` (o la tarea `export`, o `python -m latedel figures`) dice que un archivo no es el que nombra el manifiesto, la carpeta quedó a medias o alguien la tocó: se corre de nuevo la tarea `export` de la corrida (`python -m latedel run --run-key <corrida> --from export`), que escribe los cinco y el manifiesto otra vez; nunca se copia al sitio una carpeta que no verifica. Una celda con `suppressed: true` puede tener pedidos suficientes (está oculta para que otra no se deduzca por resta), y un detalle `null` en un ejemplo quiere decir que publicarlo dejaría el ejemplo con menos de `min_cell_orders` pedidos.
6. La comprobación del manifiesto de los archivos intermedios en cada corrida es la de R3, punto 4.
7. Mover un borde de `[split]`, cambiar costos o variables después del sello es un análisis nuevo (R13).

## R11 La URL de Mendeley cambió o hay una versión nueva

**Síntoma.** `fetch_source` falla con «No se encontró la fuente en … (HTTP 404). El archivo pudo haberse movido o renombrado: ver docs/runbook.md, R11», o un host nuevo de S3 aparece rechazado por V02 (R2).

**Qué hacer.**

1. Buscar el conjunto por su DOI (10.17632/8gx2fvg2k6) en Mendeley Data y comprobar su versión. La dirección del archivo de la versión 5 está en `[source] url`.
2. Si el archivo sigue siendo el mismo (su SHA-256 en la API de archivos de Mendeley es `fa6d022e…0aa6`), actualizar solo la dirección o el host y volver a correr: la huella protege el contenido.
3. Si hay una versión nueva, es otra fuente: R1, punto 3.
4. Si la dirección del archivo desaparece y solo queda el zip de todo el conjunto, usarlo exige una decisión nueva con una lista de miembros permitida, como la del proyecto 02 (D02): un solo miembro esperado, sin rutas y con tope descomprimido.

## R12 El modo rápido dentro del contenedor y las rutas

**Síntoma.** Se quiere probar el flujo sin el archivo real, o una corrida rápida escribió donde no debía, o una ruta relativa no se encuentra.

**Qué significa.** Toda ruta relativa de la configuración y de la CLI se resuelve contra la carpeta del proyecto, nunca contra el directorio de trabajo (D24); dentro del contenedor la carpeta del proyecto es `/opt/latedel` (`LATEDEL_HOME`). El modo rápido (`config/pipeline.quick.toml`) usa una fuente sintética local y escribe todo en `data/quick/`: no toca el almacén, los Parquet, el HTML, los reportes, el sello ni el README de la corrida real (AC30; una prueba compara la huella de cada archivo versionado antes y después).

**Qué hacer.**

```powershell
.venv\Scripts\python -m latedel make-fixture data\quick\dataco_fixture.csv
.venv\Scripts\python -m latedel replay --config config\pipeline.quick.toml
# al terminar
Remove-Item -Recurse -Force data\quick
```

1. En el contenedor, `LATEDEL_CONFIG=/opt/latedel/config/pipeline.quick.toml` hace que el DAG use el modo rápido; la prueba de Docker lo usa así.
2. `data/quick/` no se versiona; la prueba del modo rápido se niega a correr si quedó de otra corrida.

**Después de la evaluación independiente (D90).** Un `LATEDEL_CONFIG` relativo se toma de `LATEDEL_HOME` o, sin ella, de la carpeta del proyecto, nunca del directorio de trabajo. `python -m latedel docs-sync --config config\pipeline.quick.toml` sale con 2 sin escribir nada y lo explica: el modo rápido no tiene README, y su resumen queda en `data/quick/reports/evaluation_summary.md`.

## R13 Cambiar los costos supuestos o hacer una versión 2 del modelo

**Síntoma.** Se quiere otro umbral, otros costos, otras variables u otro modelo.

**Qué significa.** El resultado de la prueba está sellado con la configuración del lanzamiento (costos 5, 20 y 0,5; D14). Una evaluación con otra configuración no reemplaza el resultado sellado: `fact_method_metrics` y `fact_evaluation_summary` la guardan con `seal_state = post_seal` y el resumen la informa aparte (M05). Elegir otros costos es una decisión del dueño (plan, sección 26, pendiente 1).

**Qué hacer.**

1. Para ver el efecto de otros costos sin tocar nada, leer la sensibilidad: `fact_cost_sensitivity` y la figura `docs/figures/cost_curve.png` dan el beneficio neto del modelo, B2 y B5 en cada punto de equilibrio de 0,10 a 0,90, con el umbral elegido en V2.
2. Para usar otros costos: registrar una decisión nueva, cambiar `[costs]` en una configuración propia y correr en una copia aparte; el resultado es posterior al sello y se informa así en el resumen, el README y el sitio.
3. Una versión 2 del modelo (otras variables, otro clasificador, reentrenar con 2017) es otro lanzamiento: una `model_version` nueva, un sello propio y una prueba nueva que no haya mirado nadie. Los datos de este archivo ya no tienen una prueba sin mirar: un lanzamiento nuevo necesita datos nuevos.

**Después de la evaluación independiente (D88).** En una repetición desde cero con otra configuración, el resumen (`reports/evaluation_summary.md` y `.json`, y el bloque del README que copia `docs-sync`) muestra primero los criterios C01 a C09 y las cifras de la prueba tal como constan en el sello, con los costos sellados, y debajo, como posterior al sello, lo que calculó esa configuración con sus costos. El sello solo se lee.
