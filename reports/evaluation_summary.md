Evaluación al 2018-02-07 (corrida `20261111T113954030322Z`).

El modelo supera 7 de 9 criterios con umbral y no supera C03 ni C05.

Costos supuestos y declarados, en unidades genéricas: alerta 5,0, retraso 20,0, efecto 0,50 (punto de equilibrio 0,50). Los criterios y sus umbrales se fijaron antes de construir (`docs/requirements.md`, sección 8.1).

| Criterio | Qué mide | Veredicto |
| --- | --- | --- |
| C01 | Discriminación frente a la regla por modo | El modelo supera a la regla por modo: AUC 0,773 frente a 0,742. |
| C02 | Mejora fuera del ruido frente a la regla por modo | El modelo supera a la regla por modo fuera del ruido: la diferencia de AUC es 0,031, con un intervalo del 95 % de 0,026 a 0,036. |
| C03 | Mejora fuera del ruido frente a la regla de dos variables | El modelo no supera a la regla de dos variables: la diferencia de AUC es −0,001, con un intervalo del 95 % de −0,006 a 0,005. |
| C04 | Beneficio neto frente a la regla de la investigación (B2 a 0,5) | El modelo supera a la regla de la investigación (B2 a 0,5) en beneficio neto: 1.459,4 frente a 1.323,1 u. por cada 1.000 pedidos. |
| C05 | Beneficio neto frente a la regla de dos variables con su propio umbral | El modelo no supera a la regla de dos variables en beneficio neto con su propio umbral (0,80): 1.459,4 frente a 1.467,3 u. por cada 1.000 pedidos. |
| C06 | Calibración en la prueba | El modelo supera el control de calibración: ECE 0,016 frente a un máximo de 0,03 y sesgo −0,006 frente a un máximo de 0,02 en valor absoluto. |
| C07 | Brier frente a la regla por modo | El modelo supera a la regla por modo en Brier: 0,1767 frente a 0,1890 (menor es mejor). |
| C08 | Con el mismo número de alertas que B2 a 0,5 | Con el mismo número de alertas que B2 a 0,5 (k = 4.998), el modelo supera a B2: 4.499 pedidos tarde frente a 4.421. |
| C09 | Sensibilidad a los costos | El modelo supera o iguala a B2 en beneficio neto en los 17 puntos de equilibrio (0,10 a 0,90). |
| C10 | Tramo posterior al cambio de forma (informativo) | Informativo, sin umbral: en el tramo posterior al cambio de forma (8.043 pedidos) el AUC del modelo es 0,779, el de B2 0,745 y el de B5 0,777; el modelo marca el 37,7 % de los pedidos con precisión 0,898 y recall 0,588. |

**Prueba (2017-01-08 a 2017-09-30)**

| Método | AUC | Brier | ECE | Marcados (%) | Precisión | Recall | BN por 1.000 pedidos (u.) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Modelo | 0,773 | 0,1767 | 0,016 | 37,1 | 0,894 | 0,581 | 1.459,4 |
| B2 tasa de retraso por modo | 0,742 | 0,1890 | 0,003 | 34,4 | 0,885 | 0,534 | 1.323,1 |
| B5 tasa por modo y hora del pedido | 0,774 | 0,1751 | 0,006 | 37,3 | 0,893 | 0,585 | 1.467,3 |

**Tramo posterior al cambio de forma (informativo)**

| Método | AUC | Brier | ECE | Marcados (%) | Precisión | Recall | BN por 1.000 pedidos (u.) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Modelo | 0,779 | 0,1747 | 0,007 | 37,7 | 0,898 | 0,588 | 1.501,3 |
| B2 tasa de retraso por modo | 0,745 | 0,1875 | 0,001 | 35,0 | 0,889 | 0,540 | 1.362,1 |
| B5 tasa por modo y hora del pedido | 0,777 | 0,1737 | 0,003 | 37,9 | 0,898 | 0,590 | 1.505,7 |

**Validación (V2), no es la prueba**

| Método | AUC | Brier | ECE | Marcados (%) | Precisión | Recall | BN por 1.000 pedidos (u.) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Modelo | 0,776 | 0,1731 | 0,011 | 37,7 | 0,899 | 0,588 | 1.504,4 |
| B2 tasa de retraso por modo | 0,743 | 0,1865 | 0,001 | 35,3 | 0,892 | 0,545 | 1.381,2 |
| B5 tasa por modo y hora del pedido | 0,781 | 0,1728 | 0,003 | 37,7 | 0,899 | 0,588 | 1.504,4 |
