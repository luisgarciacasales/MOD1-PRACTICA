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

    # --- Embeddings ---
    embedding_model: str = "intfloat/multilingual-e5-large"
    # "sentence_transformers" (torch local) u "ollama" (bge-m3 remoto).
    # Ambos producen 1024 dimensiones, compatibles con vector(1024).
    embedding_backend: str = "sentence_transformers"
    embedding_dim: int = 1024

    # --- Fuentes externas ---
    banxico_token: str = ""
    inegi_token: str = ""
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
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    """Instancia única. Cacheada para no releer el entorno en cada import."""
    return Settings()  # type: ignore[call-arg]
