from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    db_path: str = "./data/rss_wrapper.db"
    cache_max_items: int = 100
    http_timeout: float = 20.0
    prefer_playwright: bool = True
    proxy_pool: str = ""

    model_config = SettingsConfigDict(
        env_prefix="RSS_WRAPPER_",
        env_file=".env",
        extra="ignore",
    )

    def proxies(self) -> list[str]:
        return [x.strip() for x in self.proxy_pool.split(",") if x.strip()]
