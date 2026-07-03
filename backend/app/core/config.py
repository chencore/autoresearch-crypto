from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./dev.db"
    cors_origins: list[str] = ["http://localhost:5173"]

    binance_api_key: str = ""
    binance_api_secret: str = ""

    okx_api_key: str = ""
    okx_api_secret: str = ""
    okx_passphrase: str = ""

    nado_private_key: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
