"""El calendario operativo y el oficial no pueden divergir sin que nos enteremos.

El pipeline decide si hay mercado con XMEX, vía `pandas_market_calendars`,
porque se actualiza con la librería en vez de a mano. Pero el calendario que
manda para el sector financiero mexicano es el que publica la CNBV, y son dos
cosas distintas: uno es el de la Bolsa Mexicana de Valores y otro el de las
entidades supervisadas.

Para 2026 coinciden en los diez días. Si algún año dejaran de hacerlo, sin esta
prueba la divergencia se descubriría el día que el pipeline corriera —o dejara
de correr— cuando no tocaba.
"""

from __future__ import annotations

import pytest

from src.config.calendario_cnbv import INHABILES_CNBV_2026
from src.pipeline.calendario import es_dia_habil


@pytest.mark.parametrize("fecha,motivo", INHABILES_CNBV_2026)
def test_xmex_tambien_considera_inhabil_cada_dia_de_la_cnbv(fecha, motivo: str):
    """Ninguno de los días que la CNBV declara inhábiles puede aparecer como
    hábil en el calendario que usa el pipeline."""
    assert not es_dia_habil(fecha), (
        f"XMEX considera hábil el {fecha} ({motivo}), que la CNBV declara inhábil"
    )


def test_la_lista_cubre_el_ano_completo():
    """Once días en 2026. Si alguien recorta la lista para 'limpiarla', el
    contraste dejaría de cubrir parte del año sin avisar."""
    assert len(INHABILES_CNBV_2026) == 11
    assert {f.year for f, _ in INHABILES_CNBV_2026} == {2026}


def test_un_dia_habil_normal_sigue_siendo_habil():
    """Control en la otra dirección: si el calendario se rompiera y devolviera
    inhábil siempre, las pruebas anteriores pasarían igual y el pipeline dejaría
    de correr en silencio."""
    from datetime import date

    assert es_dia_habil(date(2026, 9, 14))   # lunes ordinario
    assert es_dia_habil(date(2026, 6, 10))   # miércoles ordinario


# --- Los avisos no pueden tumbar una corrida --------------------------------


def test_sin_token_los_avisos_se_apagan_en_silencio(monkeypatch):
    """Un pipeline que revienta porque no pudo avisar de un fallo habría
    convertido la vigilancia en una causa de caídas."""
    import src.pipeline.avisos as a

    monkeypatch.setattr(a, "configurado", lambda: False)
    assert a.enviar("algo", "detalle") is False


def test_un_fallo_de_red_no_propaga_excepcion(monkeypatch):
    """Telegram caído, sin DNS, sin salida a internet: se devuelve False y la
    corrida sigue."""
    import src.pipeline.avisos as a

    monkeypatch.setattr(a, "configurado", lambda: True)

    class S:
        token_telegram = "x"
        telegram_chat_id = "1"

    monkeypatch.setattr(a, "get_settings", lambda: S())
    import requests

    monkeypatch.setattr(requests, "post", lambda *x, **k: (_ for _ in ()).throw(OSError("sin red")))
    assert a.enviar("algo") is False


def test_el_mensaje_respeta_el_limite_de_telegram(monkeypatch):
    """La API rechaza por encima de 4096 caracteres; un log largo no puede
    hacer que el aviso se pierda justo cuando más falta hace."""
    import src.pipeline.avisos as a

    enviado = {}

    class S:
        token_telegram = "x"
        telegram_chat_id = "1"

    monkeypatch.setattr(a, "configurado", lambda: True)
    monkeypatch.setattr(a, "get_settings", lambda: S())
    import requests

    class R:
        status_code = 200

    monkeypatch.setattr(requests, "post",
                        lambda *x, **k: (enviado.update(k["json"]), R())[1])
    a.enviar("titulo", "y" * 10_000)
    assert len(enviado["text"]) <= a.LIMITE_TELEGRAM


# --- El guard de horario solo aplica cuando hay sesión ----------------------


def test_en_dia_inhabil_se_puede_correr_a_cualquier_hora():
    """El guard existe para no archivar una vela a medio formar, así que solo
    tiene sentido cuando hay sesión. El 16-sep-2026 —Día de la Independencia—
    el @reboot recuperó la franja de las 08:00 y el batch la abortó diciendo
    que la BMV seguía abierta. Ese día no había BMV abierta."""
    import importlib.util
    from datetime import datetime
    from pathlib import Path

    from src.config.tiempo import TZ_MERCADO

    spec = importlib.util.spec_from_file_location(
        "batch", Path(__file__).resolve().parent.parent / "scripts" / "batch.py"
    )
    batch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(batch)

    # Miércoles 16 de septiembre de 2026 a las 11:28: inhábil, en plena
    # "sesión" si solo se mirara el reloj.
    inhabil = datetime(2026, 9, 16, 11, 28, tzinfo=TZ_MERCADO)
    assert batch._mercado_cerrado(inhabil), "en inhábil debe poder correr"

    # Y el control: un martes ordinario a la misma hora sí debe abortar.
    habil = datetime(2026, 9, 15, 11, 28, tzinfo=TZ_MERCADO)
    assert not batch._mercado_cerrado(habil), "en día hábil a media sesión debe abortar"


@pytest.mark.parametrize("hora,minuto,cerrado,por_que", [
    (7, 59, True,  "antes de abrir: precios de ayer, completos"),
    (8,  0, True,  "la franja de las 08:00 — el fallo del 17-sep"),
    (8, 29, True,  "un minuto antes de la campana"),
    (8, 30, False, "apertura: desde aquí la vela del día está a medio formar"),
    (12, 0, False, "media sesión"),
    (14, 59, False, "un minuto antes del cierre"),
    (15, 0, True,  "cierre: la vela ya está completa"),
    (20, 0, True,  "noche"),
])
def test_la_sesion_tiene_apertura_y_cierre(hora: int, minuto: int, cerrado: bool, por_que: str):
    """El guard conocía solo la hora de cierre, así que trataba las 08:00 como
    sesión en curso y abortaba la primera franja del día. La BMV opera de 8:30
    a 15:00: antes de abrir, los precios son los de ayer y son definitivos."""
    import importlib.util
    from datetime import datetime
    from pathlib import Path

    from src.config.tiempo import TZ_MERCADO

    spec = importlib.util.spec_from_file_location(
        "batch", Path(__file__).resolve().parent.parent / "scripts" / "batch.py"
    )
    batch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(batch)

    # Jueves 17 de septiembre de 2026: día hábil ordinario.
    cuando = datetime(2026, 9, 17, hora, minuto, tzinfo=TZ_MERCADO)
    assert batch._mercado_cerrado(cuando) is cerrado, por_que
