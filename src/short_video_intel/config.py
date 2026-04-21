from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import os

try:
    import yaml
except ImportError as exc:  # pragma: no cover - dependency is expected at runtime
    yaml = None  # type: ignore[assignment]
    _YAML_IMPORT_ERROR = exc
else:  # pragma: no cover - import side effect only
    _YAML_IMPORT_ERROR = None


DEFAULT_CONFIG_NAME = "config.yaml"
EXAMPLE_CONFIG_NAME = "config.yaml.example"


@dataclass(slots=True)
class AppSettings:
    name: str = "short-video-intelligence-lab"
    debug: bool = False
    timezone: str = "Asia/Shanghai"


@dataclass(slots=True)
class DatabaseConfig:
    url: str = "sqlite:///data/app.db"
    echo: bool = False


@dataclass(slots=True)
class BrowserConfig:
    engine: str = "playwright"
    headless: bool = True
    timeout_ms: int = 30_000
    locale: str = "zh-CN"
    user_agent: str | None = None
    storage_state: Path | None = None
    user_data_dir: Path | None = None


@dataclass(slots=True)
class PathsConfig:
    workspace: Path
    config_path: Path
    data_dir: Path
    artifacts_dir: Path
    state_dir: Path
    downloads_dir: Path


@dataclass(slots=True)
class ConcurrencyConfig:
    download_workers: int = 4
    browser_workers: int = 1
    analysis_workers: int = field(default_factory=lambda: max(1, (os.cpu_count() or 2) - 1))
    max_tasks: int = 8
    retry_count: int = 3
    request_timeout_sec: float = 30.0


@dataclass(slots=True)
class AppConfig:
    app: AppSettings
    database: DatabaseConfig
    browser: BrowserConfig
    paths: PathsConfig
    concurrency: ConcurrencyConfig

    @property
    def workspace(self) -> Path:
        return self.paths.workspace

    @property
    def config_path(self) -> Path:
        return self.paths.config_path

    @property
    def data_dir(self) -> Path:
        return self.paths.data_dir

    @property
    def artifacts_dir(self) -> Path:
        return self.paths.artifacts_dir

    @property
    def state_dir(self) -> Path:
        return self.paths.state_dir

    @property
    def downloads_dir(self) -> Path:
        return self.paths.downloads_dir

    @property
    def database_url(self) -> str:
        return self.database.url

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


def default_config(workspace: str | Path | None = None) -> AppConfig:
    resolved_workspace = _resolve_workspace(workspace)
    return _build_default_config(resolved_workspace, config_path=resolved_workspace / DEFAULT_CONFIG_NAME)


def load_config(path: str | Path | None = None, workspace: str | Path | None = None) -> AppConfig:
    resolved_workspace = _resolve_workspace(workspace)
    default_config_path = resolved_workspace / DEFAULT_CONFIG_NAME
    config_path = _resolve_config_path(path, resolved_workspace, default_config_path)

    if config_path.exists():
        raw = _read_yaml_file(config_path)
    elif path is None:
        example_path = resolved_workspace / EXAMPLE_CONFIG_NAME
        raw = _read_yaml_file(example_path) if example_path.exists() else {}
    else:
        raise FileNotFoundError(config_path)

    return _build_config_from_mapping(
        raw,
        workspace=resolved_workspace,
        config_path=config_path,
    )


def _build_default_config(workspace: Path, config_path: Path) -> AppConfig:
    paths = PathsConfig(
        workspace=workspace,
        config_path=config_path,
        data_dir=workspace / "data",
        artifacts_dir=workspace / "artifacts",
        state_dir=workspace / "state",
        downloads_dir=workspace / "downloads",
    )
    return AppConfig(
        app=AppSettings(),
        database=DatabaseConfig(url=_normalize_sqlite_url(DatabaseConfig().url, workspace)),
        browser=_default_browser_config(paths),
        paths=paths,
        concurrency=ConcurrencyConfig(),
    )


def _build_config_from_mapping(data: dict[str, Any], workspace: Path, config_path: Path) -> AppConfig:
    base = _build_default_config(workspace, config_path)

    app_section = _section(data, "app")
    database_section = _section(data, "database")
    browser_section = _section(data, "browser")
    paths_section = _section(data, "paths")
    concurrency_section = _section(data, "concurrency")

    app = AppSettings(
        name=str(app_section.get("name", base.app.name)),
        debug=bool(app_section.get("debug", base.app.debug)),
        timezone=str(app_section.get("timezone", base.app.timezone)),
    )

    paths = PathsConfig(
        workspace=workspace,
        config_path=config_path,
        data_dir=_resolve_path(paths_section.get("data_dir"), workspace, base.paths.data_dir),
        artifacts_dir=_resolve_path(paths_section.get("artifacts_dir"), workspace, base.paths.artifacts_dir),
        state_dir=_resolve_path(paths_section.get("state_dir"), workspace, base.paths.state_dir),
        downloads_dir=_resolve_path(paths_section.get("downloads_dir"), workspace, base.paths.downloads_dir),
    )

    browser_defaults = _default_browser_config(paths)
    browser = BrowserConfig(
        engine=str(browser_section.get("engine", browser_defaults.engine)),
        headless=bool(browser_section.get("headless", browser_defaults.headless)),
        timeout_ms=int(browser_section.get("timeout_ms", browser_defaults.timeout_ms)),
        locale=str(browser_section.get("locale", browser_defaults.locale)),
        user_agent=_optional_str(browser_section.get("user_agent", browser_defaults.user_agent)),
        storage_state=_resolve_path(
            browser_section.get("storage_state"),
            workspace,
            browser_defaults.storage_state,
        ),
        user_data_dir=_resolve_path(
            browser_section.get("user_data_dir"),
            workspace,
            browser_defaults.user_data_dir,
        ),
    )

    database = DatabaseConfig(
        url=_normalize_sqlite_url(
            str(database_section.get("url", base.database.url)),
            workspace,
        ),
        echo=bool(database_section.get("echo", base.database.echo)),
    )

    concurrency = ConcurrencyConfig(
        download_workers=int(concurrency_section.get("download_workers", base.concurrency.download_workers)),
        browser_workers=int(concurrency_section.get("browser_workers", base.concurrency.browser_workers)),
        analysis_workers=int(concurrency_section.get("analysis_workers", base.concurrency.analysis_workers)),
        max_tasks=int(concurrency_section.get("max_tasks", base.concurrency.max_tasks)),
        retry_count=int(concurrency_section.get("retry_count", base.concurrency.retry_count)),
        request_timeout_sec=float(
            concurrency_section.get("request_timeout_sec", base.concurrency.request_timeout_sec)
        ),
    )

    return AppConfig(
        app=app,
        database=database,
        browser=browser,
        paths=paths,
        concurrency=concurrency,
    )


def _default_browser_config(paths: PathsConfig) -> BrowserConfig:
    return BrowserConfig(
        storage_state=paths.state_dir / "browser_state.json",
        user_data_dir=paths.state_dir / "browser_profile",
    )


def _resolve_workspace(workspace: str | Path | None) -> Path:
    if workspace is None:
        return Path.cwd().resolve()
    return Path(workspace).expanduser().resolve()


def _resolve_config_path(path: str | Path | None, workspace: Path, default_config_path: Path) -> Path:
    if path is None:
        return default_config_path
    config_path = Path(path).expanduser()
    if not config_path.is_absolute():
        config_path = workspace / config_path
    return config_path.resolve()


def _read_yaml_file(path: Path) -> dict[str, Any]:
    if yaml is None:  # pragma: no cover - graceful fallback for bootstrapping
        return {}

    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}

    if not isinstance(loaded, dict):
        raise ValueError(f"Configuration file must contain a mapping: {path}")

    return loaded


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Configuration section '{key}' must be a mapping")
    return value


def _resolve_path(value: Any, workspace: Path, fallback: Path) -> Path:
    if value in (None, ""):
        return fallback

    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = workspace / path
    return path.resolve()


def _optional_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _normalize_sqlite_url(url: str, workspace: Path) -> str:
    if not url.startswith("sqlite:///") or url.startswith("sqlite:////"):
        return url

    raw_path = url[len("sqlite:///") :]
    if not raw_path:
        return url

    sqlite_path = Path(raw_path)
    if sqlite_path.is_absolute():
        return url

    resolved = _resolve_path(sqlite_path, workspace, workspace / sqlite_path).as_posix()
    return f"sqlite:///{resolved}"


def _serialize(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_serialize(item) for item in value)
    return value
