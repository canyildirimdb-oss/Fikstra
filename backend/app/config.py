from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = ""
    cron_secret: str = "change-me-to-a-strong-secret"
    gemini_api_key: str = ""
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:admin@example.com"
    the_odds_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_public_url: str = "https://fikstra.onrender.com"
    telegram_topic_coupons: str = ""
    telegram_topic_singles: str = ""
    telegram_topic_value: str = ""
    telegram_topic_results: str = ""
    telegram_single_min_conf: float = 70.0
    telegram_single_daily_cap: int = 6
    telegram_value_daily_cap: int = 4

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def resolved_database_url(self) -> str:
        url = self.database_url.strip()
        if not url:
            return "sqlite:///./iddaa_radar.db"
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        elif url.startswith("postgresql+psycopg2://"):
            url = url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
        elif url.startswith("postgresql://") and "+psycopg" not in url:
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url


settings = Settings()
