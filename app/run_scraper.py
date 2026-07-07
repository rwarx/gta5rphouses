"""
Entry point for running only the scraper service.
Usage: python -m app.run_scraper
"""

import asyncio
from app.config import setup_logging
from app.main import run_scraper_only

if __name__ == "__main__":
    setup_logging()
    asyncio.run(run_scraper_only())