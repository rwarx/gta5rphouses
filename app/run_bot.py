"""
Entry point for running only the Telegram bot.
Usage: python -m app.run_bot
"""

import asyncio
from app.config import setup_logging
from app.main import run_bot_only

if __name__ == "__main__":
    setup_logging()
    asyncio.run(run_bot_only())