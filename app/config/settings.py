"""
Application settings module using Pydantic Settings.
All configuration is loaded from environment variables with sensible defaults.
"""

import sys
import os
from typing import Optional
from pydantic import BaseModel, Field, field_validator
from pathlib import Path

from dotenv import load_dotenv

# Load .env file from project root (explicit path for robustness).
# override=False so real environment variables (set by the OS, the deploy
# platform, or the test harness) always win over the .env file.
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
if _env_path.exists():
    load_dotenv(dotenv_path=str(_env_path), override=False)
else:
    load_dotenv()  # fallback to default search


def env(key: str, default: str = "") -> str:
    """Get env var with fallback."""
    return os.getenv(key, default)


class DatabaseSettings:
    """Database connection settings."""
    database_url: str = env("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/apartment_checker")
    database_sync_url: str = env("DATABASE_SYNC_URL", "")
    redis_url: str = env("REDIS_URL", "redis://localhost:6379/0")

    def __init__(self):
        _root = Path(__file__).resolve().parent.parent.parent

        # Env values can arrive with stray whitespace/newlines (e.g. Railway
        # reference variables); strip them or the URL fails to parse.
        self.database_url = self.database_url.strip()
        self.database_sync_url = self.database_sync_url.strip()
        self.redis_url = self.redis_url.strip()

        # Railway provides postgresql:// but we need postgresql+asyncpg://
        if self.database_url.startswith("postgresql://") and "+" not in self.database_url.split("://")[0]:
            self.database_url = self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

        # Derive sync URL from async URL if not explicitly set
        if not self.database_sync_url:
            self.database_sync_url = self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

        for attr in ("database_url", "database_sync_url"):
            url = getattr(self, attr)
            if url.startswith("sqlite"):
                parts = url.split(":///", 1)
                if len(parts) == 2:
                    path = parts[1]
                    if path.startswith("."):
                        abs_path = (_root / path).resolve()
                        setattr(self, attr, f"{parts[0]}:///{abs_path.as_posix()}")


class ScraperSettings:
    """Web scraper configuration."""
    target_url: str = env("TARGET_URL", "https://wiki.gta5rp.com/map")
    headless: bool = env("HEADLESS", "true").lower() == "true"
    playwright_profile: Optional[str] = env("PLAYWRIGHT_PROFILE") or None
    check_interval: int = int(env("CHECK_INTERVAL", "60"))
    use_proxy: bool = env("USE_PROXY", "false").lower() == "true"
    proxy: Optional[str] = env("PROXY") or None
    # Gate the (expensive) browser scrape on the catalog's data-refresh marker:
    # only launch Playwright when the `/realestate` catalog reports new data
    # (`fetchedAtMs` advanced), instead of scraping on a blind timer. Requires a
    # resolvable REALESTATE_SERVER. Fails open (scrapes) if the marker can't be
    # read, so a source hiccup never causes a missed update.
    map_update_gate: bool = env("MAP_UPDATE_GATE", "true").lower() == "true"


class SmartModeSettings:
    """Smart monitoring mode settings for Payday detection."""
    smart_mode: bool = env("SMART_MODE", "true").lower() == "true"
    low_interval: int = int(env("LOW_INTERVAL", "600"))
    high_interval: int = int(env("HIGH_INTERVAL", "60"))
    payday_start_minute: int = int(env("PAYDAY_START_MINUTE", "59"))
    payday_end_minute: int = int(env("PAYDAY_END_MINUTE", "10"))

    def __init__(self):
        # Validate
        assert self.low_interval >= 1, "LOW_INTERVAL must be >= 1"
        assert self.high_interval >= 1, "HIGH_INTERVAL must be >= 1"
        assert 0 <= self.payday_start_minute <= 59, "PAYDAY_START_MINUTE must be 0-59"
        assert 0 <= self.payday_end_minute <= 59, "PAYDAY_END_MINUTE must be 0-59"


class TelegramSettings:
    """Telegram bot settings."""
    bot_token: str = env("BOT_TOKEN", "")
    allowed_user_ids: str = env("ALLOWED_USER_IDS", "")

    @property
    def allowed_users(self) -> list[int]:
        """Parse allowed user IDs from comma-separated string."""
        if not self.allowed_user_ids:
            return []
        return [
            int(uid.strip())
            for uid in self.allowed_user_ids.split(",")
            if uid.strip()
        ]


class LoggingSettings:
    """Logging configuration."""
    log_level: str = env("LOG_LEVEL", "INFO")
    log_format: str = env("LOG_FORMAT", 
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    )


class APISettings:
    """API server settings."""
    api_host: str = env("API_HOST", "0.0.0.0")
    api_port: int = int(env("API_PORT", "8000"))


class CrashDetectionSettings:
    """Crash detection settings."""
    enabled: bool = env("CRASH_DETECTION_ENABLED", "true").lower() == "true"
    threshold: float = float(env("CRASH_DETECTION_THRESHOLD", "0.8"))
    check_interval: int = int(env("CRASH_CHECK_INTERVAL", "60"))


class RealEstateSettings:
    """
    Settings for the `/realestate` catalog source.

    Unlike the map scraper (which drives a browser), this source fetches the
    full real-estate catalog for a single server over HTTP. It runs on its own
    interval alongside the map scraper.
    """
    # Enable the realestate HTTP source (independent of the map scraper).
    enabled: bool = env("REALESTATE_ENABLED", "false").lower() == "true"
    # Human-readable server name; mapped to its sid via SERVER_ORDER.
    # Kept as the "primary" server (used by the map-update gate and as the
    # default target for catalog commands that omit a server argument).
    server_name: str = env("REALESTATE_SERVER", "Murrieta")
    # Comma-separated list of servers to monitor, e.g. "Murrieta,Strawberry".
    # Falls back to the single REALESTATE_SERVER when unset, so existing configs
    # keep working. Each server is polled and diffed independently every tick.
    _servers_raw: str = env("REALESTATE_SERVERS", "")
    # Seconds between catalog fetches.
    interval: int = int(env("REALESTATE_INTERVAL", "300"))
    # Notify on newly freed objects (an occupied unit disappearing from the catalog).
    notify_freed: bool = env("REALESTATE_NOTIFY_FREED", "true").lower() == "true"
    # Notify on "possibly freed" objects: an owner nickname change during the
    # Payday window. The map/catalog does not always refresh instantly, so a nick
    # swap in Payday is treated as a signal the object may have just been freed.
    notify_possibly_freed: bool = env("REALESTATE_NOTIFY_POSSIBLY_FREED", "true").lower() == "true"

    def __init__(self):
        assert self.interval >= 5, "REALESTATE_INTERVAL must be >= 5"

    @property
    def server_names(self) -> list:
        """Servers to monitor, de-duplicated and order-preserving.

        Reads REALESTATE_SERVERS (comma list) and falls back to the single
        REALESTATE_SERVER. The primary `server_name` is always included first.
        """
        names = []
        for raw in [self.server_name, *self._servers_raw.split(",")]:
            name = raw.strip()
            if name and name.lower() not in {n.lower() for n in names}:
                names.append(name)
        return names


class Settings:
    """
    Main application settings.
    Simple class-based approach without pydantic_settings complexity.
    """
    
    def __init__(self):
        self.app_name: str = env("APP_NAME", "GTA5RP Apartment Checker")
        self.version: str = env("VERSION", "1.0.0")
        self.timezone: str = env("TIMEZONE", "Asia/Novosibirsk")
        
        self.database = DatabaseSettings()
        self.scraper = ScraperSettings()
        self.realestate = RealEstateSettings()
        self.smart_mode = SmartModeSettings()
        self.telegram = TelegramSettings()
        self.logging = LoggingSettings()
        self.api = APISettings()
        self.crash_detection = CrashDetectionSettings()


# Singleton
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get application settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def setup_logging() -> None:
    """Configure Loguru logging with settings."""
    from loguru import logger
    
    settings = get_settings()

    logger.remove()

    logger.add(
        sink=sys.stdout,
        format=settings.logging.log_format,
        level=settings.logging.log_level,
        colorize=True,
    )

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    logger.add(
        sink=str(log_dir / "apartment_checker_{time:YYYY-MM-DD}.log"),
        format=settings.logging.log_format,
        level="DEBUG",
        rotation="1 day",
        retention="30 days",
        compression="zip",
    )

    logger.info(f"Logging configured: level={settings.logging.log_level}")