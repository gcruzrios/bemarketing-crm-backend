from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "BeMarketing CRM"
    APP_ENV: str = "development"

    DATABASE_URL: str

    SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    CORS_ORIGINS: str = "http://localhost:5173"
    COOKIE_SECURE: bool = True
    COOKIE_SAMESITE: str = "strict"  # strict | lax | none

    DEFAULT_TAX_RATE: float = 0.13
    DEFAULT_CURRENCY: str = "USD"
    TIMEZONE: str = "America/Costa_Rica"

    # Completitud de leads (§8.2). Configurable, no vive en el agente.
    COMPLETENESS_THRESHOLD: int = 80

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_dev(self) -> bool:
        return self.APP_ENV == "development"

    @property
    def async_database_url(self) -> str:
        """Acepta postgresql:// de Neon y lo convierte a asyncpg (sin sslmode/channel_binding)."""
        parts = urlsplit(self.DATABASE_URL)
        query = [(k, v) for k, v in parse_qsl(parts.query) if k not in ("sslmode", "channel_binding")]
        return urlunsplit(("postgresql+asyncpg", parts.netloc, parts.path, urlencode(query), ""))

    @property
    def db_ssl(self) -> bool:
        return "localhost" not in self.DATABASE_URL and "127.0.0.1" not in self.DATABASE_URL


# Pesos de completitud: (campo, peso). "contact_channel" = email o whatsapp/phone.
COMPLETENESS_WEIGHTS: list[tuple[str, int]] = [
    ("company_name", 20),
    ("contact_first_name", 10),
    ("contact_channel", 20),
    ("company_type", 10),
    ("city", 5),
    ("interest_summary", 20),
    ("interested_services", 10),
    ("contact_job_title", 5),
]
REQUIRED_FOR_COMPLETE = ("company_name", "contact_channel", "interest_summary")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
