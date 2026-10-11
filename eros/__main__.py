"""Docker entry point with configuration at a stable, mounted location."""

from pathlib import Path

from .settings import Settings


def load_settings(path: Path = Path("/data/settings.json")) -> Settings:
    settings = Settings.load(path) if path.is_file() else Settings()
    if settings.metadata_directory is None:
        settings.metadata_directory = Path("/data/metadata")
    return settings


def main() -> None:
    import uvicorn

    from .api import create_app
    from .directories import Directories

    Path("/data/metadata").mkdir(parents=True, exist_ok=True)
    uvicorn.run(
        create_app(
            load_settings(),
            settings_path=Path("/data/settings.json"),
            directories=Directories([Path("/media")], Path("/data/metadata")),
        ),
        host="0.0.0.0",
        port=9307,
    )


if __name__ == "__main__":
    main()
