"""Configuración del servicio, leída del entorno (inyectado por compose.yaml).

Fuente única de verdad para rutas, credenciales y parámetros de inferencia. Los
valores reales vienen del `.env` que vive **solo en mi-pc** (invariante 3); aquí
no hay ningún secreto, solo defaults seguros y la forma que deben tener.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    # --- PostgreSQL (Silver/Gold) ---
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "mod1_practica"
    postgres_user: str = "mod1"
    # Sin default: si falta, que falle al arrancar y no a mitad del batch.
    postgres_password: str

    # --- Inferencia contra el lab-ollama compartido (invariantes 5 y 7) ---
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_model_ner: str = "qwen3.5:9b"
    ollama_model_ma: str = "qwen3.5:9b"
    # Lotes de 8 llamadas concurrentes (PRD §2.1). Subir a 16 solo tras
    # comprobar margen de VRAM con `make gpu`.
    nlp_batch_size: int = Field(default=8, ge=1, le=32)
    # Ventana de contexto de cada llamada a Ollama. Fijada por la política
    # FinOps de CLAUDE.md para evitar truncamientos silenciosos. Se envía
    # EXPLÍCITAMENTE: sin ella Ollama aplica su default, que en este servidor es
    # 4096 — verificado el 2026-08-10 vía /api/ps.
    ollama_num_ctx: int = Field(default=16384, ge=2048)

    # --- Embeddings ---
    embedding_model: str = "intfloat/multilingual-e5-large"
    # "sentence_transformers" (torch local) u "ollama" (bge-m3 remoto).
    # Ambos producen 1024 dimensiones, compatibles con vector(1024).
    embedding_backend: str = "sentence_transformers"
    embedding_dim: int = 1024

    # --- Fuentes externas ---
    banxico_token: str = ""
    inegi_token: str = ""

    # --- Claude API: redacción del brief ejecutivo (F4) ---
    #
    # Única llamada a un modelo comercial del pipeline; todo el NLP masivo
    # sigue en local contra lab-ollama (política FinOps de CLAUDE.md).
    #
    # Se lee de un FICHERO montado como secreto de Docker, no de una variable
    # de entorno. La diferencia importa: una variable de entorno aparece en
    # `docker inspect`, en /proc/<pid>/environ, la heredan todos los procesos
    # hijo, y acaba en cualquier volcado de excepción que imprima el entorno.
    # Un fichero en /run/secrets (tmpfs) no sufre nada de eso.
    #
    # `..._file` + fallback a la variable directa es la convención de las
    # imágenes oficiales de PostgreSQL y MySQL; se sigue aquí para que el
    # patrón sea reconocible y para no romper un arranque sin secreto montado.
    anthropic_api_key: str = ""
    anthropic_api_key_file: Path | None = None
    anthropic_model_brief: str = "claude-opus-5"
    requests_cache_path: Path = Path("/app/data/cache/requests_cache.sqlite")
    cache_ttl_market_seconds: int = 86_400   # diario
    cache_ttl_macro_seconds: int = 604_800   # semanal

    # --- Rutas del medallón ---
    bronze_path: Path = Path("/app/data/bronze")
    silver_path: Path = Path("/app/data/silver")
    gold_path: Path = Path("/app/data/gold")
    faiss_index_path: Path = Path("/app/data/faiss/index.index")

    # --- Correlación temporal ---
    # XMEX = Bolsa Mexicana de Valores en pandas_market_calendars. Es lo que
    # resuelve el siguiente día hábil para el JOIN noticias↔precios (PRD §6.6).
    market_calendar: str = "XMEX"

    log_level: str = "INFO"

    @property
    def clave_anthropic(self) -> str:
        """Clave de la API de Claude, leída del secreto montado.

        Precedencia: el fichero de `anthropic_api_key_file` gana sobre la
        variable directa. Es deliberado — si el secreto está montado, esa es la
        fuente buena, y una variable heredada del entorno no debe pisarla.

        Devuelve cadena vacía si no hay ninguna de las dos, en vez de reventar:
        el brief es una etapa opcional, y el resto del pipeline debe seguir
        corriendo en un servidor donde nadie ha depositado la clave todavía.
        Quien la necesite comprueba y falla con un mensaje propio.
        """
        ruta = self.anthropic_api_key_file
        if ruta is not None:
            try:
                return ruta.read_text(encoding="utf-8").strip()
            except OSError:
                # Secreto declarado pero ilegible (no montado, permisos): se
                # cae a la variable, que casi siempre estará vacía y produce el
                # mismo "no configurada" que si no hubiera nada.
                pass
        return self.anthropic_api_key.strip()

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    """Instancia única. Cacheada para no releer el entorno en cada import."""
    return Settings()  # type: ignore[call-arg]
