"""
Playwright scraper for GTA5RP wiki map.
Uses Seoul Towers carousel navigation to find and click apartment markers.
"""
import asyncio
import random
import re
from typing import Optional, List, Dict, Any
from datetime import datetime
from dataclasses import dataclass, field

from loguru import logger
from playwright.async_api import Page

from app.config import get_settings
from app.scraper.anti_detect import AntiDetectManager


@dataclass
class ApartmentTypeData:
    class_name: str
    total: Optional[int] = None
    free: Optional[int] = None
    occupied: Optional[int] = None


@dataclass
class ApartmentData:
    apartment_id: str = ""
    name: str = ""
    address: Optional[str] = None
    total_apartments: Optional[int] = None
    free_apartments: Optional[int] = None
    occupied_apartments: Optional[int] = None
    description: Optional[str] = None
    wiki_url: Optional[str] = None
    last_updated: Optional[str] = None
    apartment_types: List[ApartmentTypeData] = field(default_factory=list)
    raw_data: Dict[str, Any] = field(default_factory=dict)
    coordinates: Optional[Dict[str, float]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "address": self.address,
            "total_apartments": self.total_apartments,
            "free_apartments": self.free_apartments,
            "occupied_apartments": self.occupied_apartments,
            "description": self.description,
            "wiki_url": self.wiki_url,
            "last_updated": self._parse_updated_time(),
            "raw_data": self.raw_data,
            "coordinates": self.coordinates,
        }

    def _parse_updated_time(self) -> Optional[datetime]:
        if not self.last_updated:
            return None
        for fmt in ["%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"]:
            try: return datetime.strptime(self.last_updated, fmt)
            except ValueError: continue
        return None


class ApartmentScraper:
    """Parser for GTA5RP wiki map using Seoul carousel navigation."""

    def __init__(self, browser_manager: AntiDetectManager):
        self.browser_manager = browser_manager
        self.settings = get_settings()

    async def scrape_all_apartments(self) -> List[ApartmentData]:
        """Full scrape using Seoul popup nav (next/prev buttons inside popup)."""
        logger.info("=" * 50)
        logger.info("Starting map scraper (Seoul popup nav method)")

        page = await self.browser_manager.create_page()
        await page.set_viewport_size({"width": 1920, "height": 1080})

        try:
            await page.goto(self.settings.scraper.target_url, wait_until="domcontentloaded")
            await asyncio.sleep(2)

            logger.info("Waiting for map and server selection...")
            for i in range(30):
                ld = await page.evaluate(
                    "!document.querySelector('.map-loading') || "
                    "window.getComputedStyle(document.querySelector('.map-loading')).display === 'none'")
                ss = await page.query_selector(".map-server-screen")
                if ld and ss:
                    break
                await asyncio.sleep(1)

            target_server = self.settings.scraper.map_server
            logger.info(f"Selecting server {target_server}...")
            buttons = await page.query_selector_all("button.map-server-card")
            for b in buttons:
                t = await b.inner_text()
                if target_server.lower() in t.lower():
                    await b.click()
                    await asyncio.sleep(5)
                    break

            for i in range(10):
                closed = await page.evaluate("""() => {
                    const ss = document.querySelector('.map-server-screen');
                    if (!ss) return true;
                    const s = window.getComputedStyle(ss);
                    return s.display === 'none' || s.opacity === '0';
                }""")
                if closed:
                    break
                await asyncio.sleep(1)

            await asyncio.sleep(3)

            # Open Seoul Towers category
            total = await self._open_seoul_category(page)
            if not total:
                logger.warning("Could not open Seoul Towers category")
                await page.screenshot(path="debug_no_seoul.png")
                return []

            logger.info(f"Seoul category opened: {total} items")

            # Open first popup by clicking map center (keep popup open)
            first_data = await self._click_center_and_extract(page, 0, total, close_after=False)
            apartments = [first_data] if first_data else []

            # Navigate remaining items using popup "next" button (popup stays open)
            for i in range(1, total):
                try:
                    clicked = await page.evaluate("""() => {
                        const btn = document.querySelector('[data-popup-action="next"]');
                        if (!btn || btn.disabled) return false;
                        btn.click();
                        return true;
                    }""")
                    if not clicked:
                        logger.warning(f"[{i+1}/{total}] No next button available")
                        break

                    await asyncio.sleep(1.5)
                    data = await self._extract_popup_data(page)
                    if data:
                        apartments.append(data)
                except Exception as e:
                    logger.error(f"Error on item {i+1}/{total}: {e}")
                    continue

            # Close popup
            await self._close_popup(page)

            logger.info(f"Scraped {len(apartments)}/{total} apartments")
            return apartments

        except Exception as e:
            logger.error(f"Scraper error: {e}")
            return []
        finally:
            await page.close()

    async def _open_seoul_category(self, page: Page) -> Optional[int]:
        """Find Seoul Towers in nav sidebar, click it, return total item count."""
        # Expand nav panel if collapsed
        collapsed = await page.query_selector(".map-blips-nav__body--collapsed")
        if collapsed:
            surface = await page.query_selector(".map-blips-nav__surface")
            if surface:
                await surface.click()
                await asyncio.sleep(0.5)

        # Scroll Seoul Towers into view and click
        seoul_found = await page.evaluate("""() => {
            const items = document.querySelectorAll('.map-blips-nav__item');
            for (const item of items) {
                if ((item.textContent || '').includes('Seoul Towers')) {
                    const btn = item.querySelector('.map-blips-nav__label-btn');
                    if (btn) {
                        btn.scrollIntoView({block: 'center'});
                        setTimeout(() => btn.click(), 100);
                        return true;
                    }
                }
            }
            return false;
        }""")
        if not seoul_found:
            return None

        await asyncio.sleep(1.5)

        # Read total count from sidebar "1/35" or from popup counter
        count_text = await page.evaluate("""() => {
            const items = document.querySelectorAll('.map-blips-nav__item');
            for (const item of items) {
                const t = item.textContent || '';
                if (t.includes('Seoul Towers')) {
                    const nav = item.querySelector('.map-blips-nav__nav');
                    if (nav) return nav.textContent.trim();
                }
            }
            return '';
        }""")
        if not count_text:
            return None
        match = re.search(r'(\d+)$', count_text)
        return int(match.group(1)) if match else None

    async def _click_center_and_extract(
        self, page: Page, index: int, total: int, close_after: bool = True
    ) -> Optional[ApartmentData]:
        """Click viewport center (960, 540) where map marker is, extract popup."""
        center_x, center_y = 960, 540

        logger.info(f"[{index+1}/{total}] Clicking map center ({center_x}, {center_y})...")

        await asyncio.sleep(1)

        # Human-like mouse movement to center
        await page.mouse.move(
            random.randint(center_x - 300, center_x + 300),
            random.randint(center_y - 200, center_y + 200),
            steps=random.randint(5, 10)
        )
        await asyncio.sleep(random.uniform(0.3, 0.7))
        await page.mouse.move(center_x, center_y, steps=random.randint(3, 5))
        await asyncio.sleep(random.uniform(0.2, 0.4))
        await page.mouse.click(center_x, center_y)
        await asyncio.sleep(random.uniform(1, 2))

        # Check for popup
        popup = await page.query_selector(".map-blip-popup")
        if not popup or not await popup.is_visible():
            for dx, dy in [(30, 0), (-30, 0), (0, 30), (0, -30)]:
                if await page.query_selector(".map-blip-popup"):
                    break
                await page.mouse.click(center_x + dx, center_y + dy)
                await asyncio.sleep(0.8)
                popup = await page.query_selector(".map-blip-popup")
                if popup and await popup.is_visible():
                    break

            if not popup or not await popup.is_visible():
                logger.warning(f"[{index+1}/{total}] No popup appeared")
                return None

        await asyncio.sleep(0.5)
        data = await self._extract_popup_data(page)

        if close_after:
            await self._close_popup(page)

        return data

    async def _extract_popup_data(self, page: Page) -> Optional[ApartmentData]:
        """Extract apartment data from the visible popup using HTML structure."""
        popup = await page.query_selector(".map-blip-popup")
        if not popup:
            return None

        data = ApartmentData()
        try:
            text = await popup.inner_text()
            html = await popup.inner_html()
            data.raw_data["full_text"] = text
            data.raw_data["full_html"] = html

            # Parse name from title
            name_el = await popup.query_selector(".map-blip-popup__title")
            if name_el:
                data.name = (await name_el.inner_text()).strip()
                data.apartment_id = re.sub(r'[^a-z0-9]', '_', data.name.lower()).strip('_')[:50]

            # Parse stats from structured cells
            stats = await page.evaluate("""(sel) => {
                const popup = document.querySelector(sel);
                if (!popup) return {};
                const cells = popup.querySelectorAll('.map-stats__cell');
                const result = {};
                for (const cell of cells) {
                    const valEl = cell.querySelector('.map-stats__value');
                    const labelEl = cell.querySelector('.map-stats__label');
                    if (!valEl || !labelEl) continue;
                    const value = parseInt(valEl.textContent.trim());
                    const label = labelEl.textContent.trim().toLowerCase();
                    if (label.includes('всего')) result.total = value;
                    else if (label.includes('свободн')) result.free = value;
                    else if (label.includes('занят')) result.occupied = value;
                }
                return result;
            }""", ".map-blip-popup")

            data.total_apartments = stats.get("total")
            data.free_apartments = stats.get("free")
            data.occupied_apartments = stats.get("occupied")

            # Parse apartment type bars
            class_rows = await page.evaluate("""(sel) => {
                const popup = document.querySelector(sel);
                if (!popup) return [];
                const bars = popup.querySelectorAll('.map-stats__bar');
                const results = [];
                const kw = ['стандарт','комфорт','люкс','standard','comfort','luxury','эконом','бизнес','премиум','студия'];
                for (const bar of bars) {
                    const barText = bar.textContent.trim();
                    const barLines = barText.split('\\n').map(l => l.trim()).filter(Boolean);
                    for (let i = 0; i < barLines.length; i++) {
                        const line = barLines[i].toLowerCase();
                        if (kw.some(k => line.includes(k))) {
                            const name = barLines[i];
                            let nums = [];
                            for (let j = i; j < Math.min(i + 3, barLines.length); j++) {
                                const m = barLines[j].match(/\\d+/g);
                                if (m) nums.push(...m.map(Number));
                            }
                            if (nums.length >= 2) {
                                results.push({name, free: nums[0], total: nums[1], occupied: nums.length >= 3 ? nums[2] : nums[1] - nums[0]});
                            }
                            break;
                        }
                    }
                }
                return results;
            }""", ".map-blip-popup")

            for cr in class_rows:
                td = ApartmentTypeData(
                    class_name=cr["name"],
                    total=cr.get("total"),
                    free=cr.get("free"),
                    occupied=cr.get("occupied"),
                )
                data.apartment_types.append(td)

            # Parse updated time
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            for line in lines:
                lower = line.lower()
                if "обновлен" in lower:
                    parts = line.split(":", 1)
                    data.last_updated = parts[1].strip() if len(parts) > 1 else line

            link = await popup.query_selector("a[href*='wiki']")
            if link:
                data.wiki_url = await link.get_attribute("href")

            if data.name:
                logger.info(f"  {data.name}: {data.free_apartments}/{data.total_apartments} free")

        except Exception as e:
            logger.warning(f"Error extracting popup: {e}")

        return data if data.name else None

    async def _close_popup(self, page: Page) -> None:
        """Close popup by clicking outside it (on the overlay backdrop)."""
        try:
            viewport = page.viewport_size
            if viewport:
                # Click top-left corner outside popup
                await page.mouse.click(10, 10)
                await asyncio.sleep(0.3)
        except:
            pass
