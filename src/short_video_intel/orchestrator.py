from .config import AppConfig


class Orchestrator:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def bootstrap(self) -> dict[str, str]:
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        return {
            "workspace": str(self.config.workspace),
            "data_dir": str(self.config.data_dir),
            "database_url": self.config.database_url,
        }
