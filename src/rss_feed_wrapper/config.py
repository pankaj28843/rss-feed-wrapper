from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    db_path: str = "./data/rss_wrapper.db"
    cache_max_items: int = 100
    http_timeout: float = 20.0
    prefer_playwright: bool = True
    proxy_pool: str = ""
    proxy_pools: str = ""

    model_config = SettingsConfigDict(
        env_prefix="RSS_WRAPPER_",
        env_file=".env",
        extra="ignore",
    )

    def proxies(self) -> list[str]:
        return [x.strip() for x in self.proxy_pool.split(",") if x.strip()]

    def proxy_pools_map(self) -> dict[str, list[str]]:
        pools: dict[str, list[str]] = {}

        # Backward-compatible single pool.
        single_pool = self.proxies()
        if single_pool:
            pools["default"] = single_pool

        # Multiple pools format:
        # RSS_WRAPPER_PROXY_POOLS="pool1=http://a:1,http://b:2;pool2=http://c:3"
        raw = self.proxy_pools.strip()
        if not raw:
            return pools

        for chunk in raw.split(";"):
            token = chunk.strip()
            if not token:
                continue
            if "=" not in token:
                continue
            name, members = token.split("=", 1)
            pool_name = name.strip()
            proxy_list = [x.strip() for x in members.split(",") if x.strip()]
            if pool_name and proxy_list:
                pools[pool_name] = proxy_list

        return pools
