"""
Main application entry point.
Provides unified startup for all services.
"""

import asyncio
from typing import Optional

from loguru import logger

from app.config import get_settings, setup_logging as config_logging
from app.database import init_db, close_db


async def run_all_services() -> None:
    """Run scraper, bot and notifier concurrently."""
    from app.scraper.scheduler import SmartScheduler
    from app.telegram.bot import ApartmentBot
    from app.telegram.notifier import ChangeNotifier
    
    settings = get_settings()
    
    # Initialize database
    await init_db()
    
    # Create scheduler
    scheduler = SmartScheduler()
    
    # Start scheduler
    scheduler_task = asyncio.create_task(scheduler.start())
    
    # Start Telegram bot
    bot = ApartmentBot(scheduler)
    bot_task = asyncio.create_task(bot.start())
    
    # Start notification notifier (waits for bot to be ready)
    notifier_task = asyncio.create_task(_run_notifier(bot))
    
    logger.info(f"All services started")
    
    # Wait for all tasks (each handles own exceptions)
    tasks = [scheduler_task, bot_task]
    if notifier_task:
        tasks.append(notifier_task)
    
    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.error(f"Task {i} failed: {r}")
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await scheduler.stop()
        await bot.stop()
        if notifier_task and not notifier_task.done():
            notifier_task.cancel()
        await close_db()


async def _run_notifier(bot) -> None:
    """Run notifier when bot is available."""
    for _ in range(30):
        if bot.bot:
            break
        await asyncio.sleep(1)
    if not bot.bot:
        logger.warning("Bot not available after 30s, notifier disabled")
        return
    from app.telegram.notifier import ChangeNotifier
    notifier = ChangeNotifier(bot.bot)
    await notifier.start()


async def run_scraper_only() -> None:
    """Run only the scraper service."""
    from app.scraper.scheduler import SmartScheduler
    
    await init_db()
    scheduler = SmartScheduler()
    
    try:
        await scheduler.start()
    except KeyboardInterrupt:
        logger.info("Shutting down scraper...")
    finally:
        await scheduler.stop()
        await close_db()


async def run_bot_only() -> None:
    """Run only the Telegram bot."""
    from app.telegram.bot import ApartmentBot
    
    await init_db()
    bot = ApartmentBot()
    
    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Shutting down bot...")
    finally:
        await bot.stop()
        await close_db()