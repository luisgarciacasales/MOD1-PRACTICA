"""Pruebas del saneamiento de la salida del LLM.

Lo que se prueba aquí no es el modelo sino la **defensa contra el modelo**: un
LLM de 9B alucina símbolos, se contradice y devuelve tipos inesperados. Estas
reglas son las que impiden que eso llegue a Gold.
"""

from __future__ import annotations

from src.pipeline.enrich import Enriquecida, aplicar_ma, aplicar_ner
from src.pipeline.ollama import _despegar_vallas

FINTECHS = {"Nu", "Stori", "Klar"}


def nueva() -> Enriquecida:
    return Enriquecida(guid="a" * 64)


# --- NER --------------------------------------------------------------------


def test_descarta_tickers_alucinados():
    """Regresión: el modelo inventó "BANR" para Banorte. Un ticker inventado no
    falla de forma visible, simplemente no hace JOIN y la noticia desaparece."""
    e = nueva()
    aplicar_ner(e, {"tickers": ["GFNORTEO.MX", "BANR", "AAPL"]}, FINTECHS)
    assert e.ner_tickers == ["GFNORTEO.MX"]


def test_descarta_sectores_fuera_del_vocabulario():
    e = nueva()
    aplicar_ner(e, {"sectores": ["banca_consumo", "criptomonedas_lunares"]}, FINTECHS)
    assert e.ner_sectors == ["banca_consumo"]


def test_sentimiento_invalido_cae_a_neutral():
    e = nueva()
    aplicar_ner(e, {"sentimiento": "muy bueno"}, FINTECHS)
    assert e.sentiment_label == "neutral"


def test_confianza_se_acota_al_rango():
    for entrada, esperado in [(1.7, 1.0), (-3, 0.0), ("0.8", 0.8), ("alta", None), (None, None)]:
        e = nueva()
        aplicar_ner(e, {"confianza_sentimiento": entrada}, FINTECHS)
        assert e.sentiment_score == esperado, entrada


def test_listas_con_basura_se_limpian():
    e = nueva()
    aplicar_ner(e, {"personas": ["Carlos Slim", "", None, 42, "Carlos Slim", "  "]}, FINTECHS)
    assert e.ner_persons == ["Carlos Slim"]


def test_campos_ausentes_no_revientan():
    e = nueva()
    aplicar_ner(e, {}, FINTECHS)
    assert e.ner_tickers == [] and e.sentiment_label == "neutral"


# --- M&A y fintech ----------------------------------------------------------


def test_coherencia_ma_se_impone():
    """El CHECK de la tabla exige que si no hay evento, el tipo sea 'none'."""
    e = nueva()
    aplicar_ma(e, {"es_evento_ma": False, "tipo_ma": "merger"}, FINTECHS)
    assert e.is_ma_event is False and e.ma_event_type == "none"


def test_tipo_ma_sin_evento_tampoco_cuela_al_reves():
    e = nueva()
    aplicar_ma(e, {"es_evento_ma": True, "tipo_ma": "none"}, FINTECHS)
    assert e.is_ma_event is False and e.ma_event_type == "none"


def test_evento_ma_valido_se_conserva():
    e = nueva()
    aplicar_ma(e, {"es_evento_ma": True, "tipo_ma": "acquisition", "confianza_ma": 0.9}, FINTECHS)
    assert e.is_ma_event is True
    assert e.ma_event_type == "acquisition"
    assert e.ma_confidence == 0.9


def test_tipo_ma_inventado_cae_a_none():
    e = nueva()
    aplicar_ma(e, {"es_evento_ma": True, "tipo_ma": "spin-off"}, FINTECHS)
    assert e.ma_event_type == "none" and e.is_ma_event is False


def test_fintech_flag_se_deriva_de_lo_verificado():
    """El modelo afirmaba menciona_fintech=true citando fintechs inexistentes."""
    e = nueva()
    aplicar_ma(e, {"menciona_fintech": True, "fintechs": ["BancoInventado"]}, FINTECHS)
    assert e.fintechs_identified == []
    assert e.fintech_flag is False


def test_fintech_reconocida_activa_la_bandera():
    e = nueva()
    aplicar_ma(e, {"menciona_fintech": False, "fintechs": ["Nu", "Klar"]}, FINTECHS)
    assert set(e.fintechs_identified) == {"Nu", "Klar"}
    assert e.fintech_flag is True


def test_sector_afectado_debe_estar_en_el_mapeo_de_proxy():
    """Si no está, `correlate` no podría resolver el ticker proxy."""
    e = nueva()
    aplicar_ma(e, {"sector_afectado": "banca_consumo"}, FINTECHS)
    assert e.sector_affected == "banca_consumo"

    e2 = nueva()
    aplicar_ma(e2, {"sector_afectado": "turismo espacial"}, FINTECHS)
    assert e2.sector_affected is None


# --- Cliente Ollama ---------------------------------------------------------


def test_despega_vallas_de_markdown():
    """qwen3.5 envuelve el JSON pese a format: json."""
    assert _despegar_vallas('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _despegar_vallas('```\n{"a": 1}\n```') == '{"a": 1}'
    assert _despegar_vallas('{"a": 1}') == '{"a": 1}'


def test_respaldo_lexico_de_sector_para_proxy():
    """Si hay fintech sin sector, la noticia no correlaciona con ningún precio.
    El prompt lo pide como regla obligatoria, pero un 9B la incumple."""
    e = nueva()
    aplicar_ma(
        e,
        {"fintechs": ["Nu"], "sector_afectado": None},
        FINTECHS,
        texto="Nu lanza una tarjeta de crédito sin anualidad",
    )
    assert e.fintechs_identified == ["Nu"]
    assert e.sector_affected == "banca_consumo"


def test_el_respaldo_no_pisa_la_respuesta_del_modelo():
    e = nueva()
    aplicar_ma(
        e,
        {"fintechs": ["Nu"], "sector_afectado": "pagos_digitales"},
        FINTECHS,
        texto="Nu lanza una tarjeta de crédito",
    )
    assert e.sector_affected == "pagos_digitales"


def test_sin_fintech_no_se_inventa_sector():
    e = nueva()
    aplicar_ma(e, {"fintechs": []}, FINTECHS, texto="tarjeta de crédito de Banorte")
    assert e.sector_affected is None


# --- Política FinOps (CLAUDE.md) ---------------------------------------------


def test_toda_llamada_declara_num_ctx():
    """La política exige ventana de contexto explícita. Sin ella Ollama usa su
    default —4096 en este servidor— y un prompt mayor se trunca EN SILENCIO:
    sin error, sin señal en la respuesta y sin nada en el log.

    Esta prueba existe para que la política no pueda regresar sin que falle algo.
    """
    import inspect

    from src.pipeline import ollama

    fuente = inspect.getsource(ollama.ClienteOllama.chat_json)
    assert '"num_ctx"' in fuente, "chat_json ya no declara num_ctx"


def test_el_num_ctx_por_defecto_es_el_de_la_politica():
    from src.config import get_settings

    assert get_settings().ollama_num_ctx == 16384


def test_no_se_envia_historial_de_conversacion():
    """La política prohíbe mandar historiales masivos. Cada llamada lleva
    exactamente dos mensajes: sistema y usuario."""
    import inspect

    from src.pipeline import ollama

    fuente = inspect.getsource(ollama.ClienteOllama.chat_json)
    assert fuente.count('"role"') == 2, "se están enviando más de dos mensajes"
