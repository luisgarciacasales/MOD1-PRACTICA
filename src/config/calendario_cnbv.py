"""Días inhábiles que publica la CNBV, para contrastar contra XMEX.

**Esta lista no la usa el pipeline.** El calendario operativo es XMEX, vía
`pandas_market_calendars`, porque se actualiza con la librería en lugar de a
mano. Lo que hace esta lista es servir de control: son dos calendarios
distintos —uno es el de la Bolsa Mexicana y otro el de las entidades
supervisadas por la Comisión Nacional Bancaria y de Valores— y para 2026
coinciden en los diez días, verificado uno a uno el 14-sep-2026.

Si algún año divergen, el test que las contrasta lo dirá. Sin ese test, la
divergencia se descubriría el día que el pipeline corriera —o dejara de
correr— cuando no tocaba.

Fuente: «Días inhábiles 2026 aplicables a entidades financieras sujetas a la
supervisión de la CNBV», Secretaría de Hacienda y Crédito Público. El PDF es
una imagen sin capa de texto, así que se transcribió a mano.
"""

from __future__ import annotations

from datetime import date

INHABILES_CNBV_2026: tuple[tuple[date, str], ...] = (
    (date(2026, 1, 1), "Año Nuevo"),
    (date(2026, 2, 2), "En conmemoración del 5 de febrero"),
    (date(2026, 3, 16), "En conmemoración del 21 de marzo"),
    (date(2026, 4, 2), "Semana Santa — jueves"),
    (date(2026, 4, 3), "Semana Santa — viernes"),
    (date(2026, 5, 1), "Día Internacional del Trabajo"),
    (date(2026, 9, 16), "Día de la Independencia"),
    (date(2026, 11, 2), "Día de muertos"),
    (date(2026, 11, 16), "En conmemoración del 20 de noviembre"),
    # Cae sábado, así que no altera un calendario de lunes a viernes. Se
    # mantiene porque la lista debe reflejar el documento, no lo que conviene.
    (date(2026, 12, 12), "Día del Empleado Bancario"),
    (date(2026, 12, 25), "Navidad"),
)
