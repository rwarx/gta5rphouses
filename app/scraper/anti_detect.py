"""
Anti-detection module for bypassing Cloudflare and bot detection.
Provides pluggable architecture for different bypass strategies.
Uses Strategy pattern to allow switching between methods.
"""

import asyncio
import random
from typing import Optional, Protocol, Dict, Any, List
from enum import Enum
from pathlib import Path

from loguru import logger
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)


class BrowserType(Enum):
    """Supported browser types for automation."""
    CHROMIUM = "chromium"
    FIREFOX = "firefox"
    WEBKIT = "webkit"


class AntiDetectStrategy(Protocol):
    """
    Protocol for anti-detection strategies.
    Each strategy must implement setup_context and setup_page methods.
    """

    async def setup_context(self, context: BrowserContext) -> None:
        """Apply anti-detection measures to browser context."""
        ...

    async def setup_page(self, page: Page) -> None:
        """Apply anti-detection measures to individual page."""
        ...


class PlaywrightStealthStrategy:
    """
    Uses Playwright Stealth to hide automation痕迹.
    Most effective for basic bot detection.
    """

    async def setup_context(self, context: BrowserContext) -> None:
        """Apply basic stealth settings to context."""
        await context.add_init_script("""
            // Override navigator.webdriver
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });

            // Override navigator.plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });

            // Override navigator.languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['ru-RU', 'ru', 'en-US', 'en']
            });

            // Override chrome.runtime
            window.chrome = {
                runtime: {}
            };

            // Override permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
        """)

    async def setup_page(self, page: Page) -> None:
        """Apply any additional page-level stealth."""
        # Set realistic viewport
        await page.set_viewport_size({
            "width": random.randint(1366, 1920),
            "height": random.randint(768, 1080)
        })

        # Randomize timezone to Russia
        await page.add_init_script("""
            // Override timezone
            Object.defineProperty(Intl.DateTimeFormat, 'resolvedOptions', {
                value: () => ({
                    locale: 'ru-RU',
                    calendar: 'gregory',
                    numberingSystem: 'latn',
                    timeZone: 'Asia/Novosibirsk'
                })
            });
        """)


class CloudscraperStrategy:
    """
    Uses cloudscraper for bypassing Cloudflare challenges.
    Useful when direct browser access is blocked.
    """

    async def setup_context(self, context: BrowserContext) -> None:
        """Set up context for cloudscraper fallback."""
        logger.info("Cloudscraper strategy configured for fallback")

    async def setup_page(self, page: Page) -> None:
        """No page setup needed for cloudscraper."""
        pass


class PersistentProfileStrategy:
    """
    Uses persistent browser profile to reuse sessions.
    This is the most reliable method for Cloudflare,
    as it uses a real user profile that has passed challenges.
    """

    def __init__(self, profile_path: Optional[str] = None):
        self.profile_path = profile_path

    async def setup_context(self, context: BrowserContext) -> None:
        """Context is already persistent, no setup needed."""
        pass

    async def setup_page(self, page: Page) -> None:
        """Apply minimal stealth."""
        pass


class AntiDetectManager:
    """
    Manages anti-detection strategies and browser lifecycle.
    Implements Strategy pattern for pluggable bypass methods.
    """

    def __init__(
        self,
        browser_type: BrowserType = BrowserType.CHROMIUM,
        headless: bool = True,
        proxy: Optional[str] = None,
        profile_path: Optional[str] = None,
        use_stealth: bool = True,
    ):
        self.browser_type = browser_type
        self.headless = headless
        self.proxy = proxy
        self.profile_path = profile_path
        self.use_stealth = use_stealth

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

        # Select strategy based on configuration
        self._strategies: List[AntiDetectStrategy] = self._select_strategies()

    def _select_strategies(self) -> List[AntiDetectStrategy]:
        """Select appropriate anti-detection strategies."""
        strategies = []

        if self.profile_path:
            logger.info(f"Using persistent profile: {self.profile_path}")
            strategies.append(PersistentProfileStrategy(self.profile_path))
        elif self.use_stealth:
            logger.info("Using Playwright Stealth strategy")
            strategies.append(PlaywrightStealthStrategy())
        else:
            strategies.append(CloudscraperStrategy())

        return strategies

    async def _get_launch_options(self) -> Dict[str, Any]:
        """Get browser launch options based on configuration."""
        options: Dict[str, Any] = {
            "headless": self.headless,
        }

        if self.proxy:
            options["proxy"] = {"server": self.proxy}

        # Additional args to avoid detection
        options["args"] = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-infobars",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            f"--window-size={random.randint(1366, 1920)},{random.randint(768, 1080)}",
        ]

        if self.profile_path:
            options["user_data_dir"] = self.profile_path

        return options

    async def start(self) -> BrowserContext:
        """
        Start browser and create context with anti-detection measures.

        Returns:
            BrowserContext with anti-detection applied.

        Raises:
            RuntimeError: If browser fails to start.
        """
        try:
            self._playwright = await async_playwright().start()

            browser_launcher = getattr(
                self._playwright, self.browser_type.value
            )

            launch_options = await self._get_launch_options()

            if self.profile_path:
                self._context = await browser_launcher.launch_persistent_context(
                    **launch_options
                )
                self._browser = self._context.browser
            else:
                self._browser = await browser_launcher.launch(**launch_options)
                self._context = await self._browser.new_context(
                    locale="ru-RU",
                    timezone_id="Asia/Novosibirsk",
                    user_agent=random.choice([
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
                    ]),
                    viewport={
                        "width": random.randint(1366, 1920),
                        "height": random.randint(768, 1080),
                    },
                    device_scale_factor=1,
                    is_mobile=False,
                    has_touch=False,
                )

            # Apply all strategies
            for strategy in self._strategies:
                await strategy.setup_context(self._context)

            logger.info(
                f"Browser started: {self.browser_type.value}, "
                f"headless={self.headless}, "
                f"strategies={len(self._strategies)}"
            )
            return self._context

        except Exception as e:
            logger.error(f"Failed to start browser: {e}")
            await self.stop()
            raise RuntimeError(f"Browser startup failed: {e}") from e

    async def create_page(self) -> Page:
        """Create a new page with anti-detection applied."""
        if not self._context:
            raise RuntimeError("Browser not started. Call start() first.")

        page = await self._context.new_page()

        # Apply page-level strategies
        for strategy in self._strategies:
            await strategy.setup_page(page)

        return page

    async def stop(self) -> None:
        """Stop browser and cleanup resources."""
        if self._context:
            try:
                await self._context.close()
            except Exception as e:
                logger.warning(f"Error closing context: {e}")
            self._context = None

        if self._browser:
            try:
                await self._browser.close()
            except Exception as e:
                logger.warning(f"Error closing browser: {e}")
            self._browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.warning(f"Error stopping playwright: {e}")
            self._playwright = None

        logger.info("Browser stopped and resources cleaned")

    async def __aenter__(self) -> "AntiDetectManager":
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, *args) -> None:
        """Async context manager exit."""
        await self.stop()

    @property
    def is_running(self) -> bool:
        """Check if browser is running."""
        return self._browser is not None and self._browser.is_connected()


def get_human_delay(min_ms: int = 200, max_ms: int = 1500) -> float:
    """
    Get random human-like delay in seconds.

    Args:
        min_ms: Minimum delay in milliseconds.
        max_ms: Maximum delay in milliseconds.

    Returns:
        Delay in seconds.
    """
    return random.randint(min_ms, max_ms) / 1000


async def create_browser_context(
    headless: bool = True,
    proxy: Optional[str] = None,
    profile_path: Optional[str] = None,
    use_stealth: bool = True,
) -> AntiDetectManager:
    """
    Convenience function to create and start AntiDetectManager.

    Args:
        headless: Run browser in headless mode.
        proxy: Proxy URL to use.
        profile_path: Path to persistent browser profile.
        use_stealth: Use stealth techniques.

    Returns:
        Started AntiDetectManager instance.
    """
    manager = AntiDetectManager(
        headless=headless,
        proxy=proxy,
        profile_path=profile_path,
        use_stealth=use_stealth,
    )
    await manager.start()
    return manager