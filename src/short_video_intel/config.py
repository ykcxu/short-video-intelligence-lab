from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class AppConfig:
    workspace: Path
    data_dir: Path
    database_url: str = "sqlite:///data/app.db"


def default_config() -> AppConfig:
    workspace = Path.cwd()
    return AppConfig(
        workspace=workspace,
        data_dir=workspace / "data",
    )
