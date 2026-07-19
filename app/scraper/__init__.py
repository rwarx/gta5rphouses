"""Web scraper module for GTA5RP Apartment Checker."""

from app.scraper.anti_detect import (
    AntiDetectManager,
    BrowserType,
    create_browser_context,
    get_human_delay,
)
from app.scraper.playwright_scraper import ApartmentScraper, ApartmentData
from app.scraper.change_detector import ChangeDetector
from app.scraper.scheduler import SmartScheduler
from app.scraper.realestate_client import (
    RealEstateClient,
    RealEstateSnapshot,
    RealEstateUnit,
    server_name_to_sid,
    sid_to_server_name,
    resolve_servers,
)
from app.scraper.realestate_detector import RealEstateDetector
from app.scraper.realestate_scheduler import RealEstateScheduler