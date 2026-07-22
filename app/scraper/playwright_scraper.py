"""
Playwright scraper for GTA5RP wiki map.
Opens the sidebar, finds an apartment building item, and iterates the popup carousel.
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
    """Parser for GTA5RP wiki map using sidebar + popup carousel navigation."""

    def __init__(self, browser_manager: AntiDetectManager):
        self.browser_manager = browser_manager
        self.settings = get_settings()

    async def scrape_all_apartments(self) -> List[ApartmentData]:
        """Full scrape: expand sidebar, find apartment popup, iterate carousel."""
        logger.info("=" * 50)
        logger.info("Starting map scraper (sidebar + popup carousel)")

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

            total, apartments = await self._open_and_scrape_carousel(page)
            if total is None or total == 0:
                logger.warning("Could not open apartment carousel")
                await page.screenshot(path="debug_no_carousel.png")
                return []

            logger.info(f"Scraped {len(apartments)}/{total} apartments")
            return apartments

        except Exception as e:
            logger.error(f"Scraper error: {e}")
            return []
        finally:
            await page.close()

    async def _open_and_scrape_carousel(self, page: Page) -> tuple:
        """
        Expand sidebar, find an apartment-building nav item, click it to open the
        popup carousel, then iterate through all items. Returns (total_count, data_list).
        """
        # Expand sidebar by clicking the head button
        head = await page.query_selector(".map-blips-nav__head")
        if head:
            expanded = await head.get_attribute("aria-expanded")
            if expanded == "false":
                await head.click()
                await asyncio.sleep(1)

        # Scan nav items until we find one whose popup has apartment stats
        items = await page.query_selector_all(".map-blips-nav__item")
        apartment_item = None
        for item in items:
            lbl = await item.query_selector(".map-blips-nav__label-btn")
            if not lbl:
                continue
            await lbl.click()
            await asyncio.sleep(2)

            popup = await page.query_selector(".map-blip-popup")
            if popup:
                stats = await popup.query_selector(".map-blip-popup__stats")
                if stats:
                    apartment_item = item
                    break

            # Close popup and try next
            await self._close_popup(page)
            await asyncio.sleep(0.5)

        if apartment_item is None:
            logger.warning("No apartment-building nav item found")
            return None, []

        # Read carousel total from popup
        count_el = await page.query_selector(".map-blip-popup__nav-count")
        total = 0
        if count_el:
            count_text = (await count_el.inner_text()).strip()
            match = re.search(r'/(\d+)$', count_text)
            if match:
                total = int(match.group(1))

        logger.info(f"Apartment carousel opened: {total} items")

        # Extract first item
        data = await self._extract_popup_data(page)
        apartments = [data] if data else []

        # Navigate remaining items
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

        await self._close_popup(page)
        return total, apartments

    async def _extract_popup_data(self, page: Page) -> Optional[ApartmentData]:
        """Extract apartment data from the visible popup."""
        popup = await page.query_selector(".map-blip-popup")
        if not popup:
            return None

        data = ApartmentData()
        try:
            html = await popup.inner_html()
            data.raw_data["full_html"] = html

            # Parse name from title
            name_el = await popup.query_selector(".map-blip-popup__title")
            if name_el:
                data.name = (await name_el.inner_text()).strip()
                data.apartment_id = re.sub(r'[^a-z0-9]', '_', data.name.lower()).strip('_')[:50]

            # Parse stats from structured cells (same selectors as before)
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

            # Parse apartment type bars (new structure with nested spans)
            class_rows = await page.evaluate("""(sel) => {
                const popup = document.querySelector(sel);
                if (!popup) return [];
                const bars = popup.querySelectorAll('.map-stats__bar');
                const results = [];
                for (const bar of bars) {
                    const nameEl = bar.querySelector('.map-stats__bar-name');
                    const freeEl = bar.querySelector('.map-stats__bar-free');
                    const totalEl = bar.querySelector('.map-stats__bar-total');
                    if (nameEl && freeEl && totalEl) {
                        const name = nameEl.textContent.trim();
                        const free = parseInt(freeEl.textContent.trim()) || 0;
                        const total = parseInt(totalEl.textContent.trim()) || 0;
                        results.push({name, free, total, occupied: total - free});
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

            # Parse updated time from dedicated element
            updated_el = await popup.query_selector(".map-blip-popup__updated")
            if updated_el:
                text = await updated_el.inner_text()
                parts = text.split(":", 1)
                data.last_updated = parts[1].strip() if len(parts) > 1 else text

            link = await popup.query_selector("a[href*='wiki']")
            if link:
                data.wiki_url = await link.get_attribute("href")

            if data.name:
                logger.info(f"  {data.name}: {data.free_apartments}/{data.total_apartments} free")

        except Exception as e:
            logger.warning(f"Error extracting popup: {e}")

        return data if data.name else None

    async def _close_popup(self, page: Page) -> None:
        """Close popup by clicking its close button or outside it."""
        try:
            close_btn = await page.query_selector(".map-popup__close")
            if close_btn:
                await close_btn.click()
                await asyncio.sleep(0.3)
                return

            viewport = page.viewport_size
            if viewport:
                await page.mouse.click(10, 10)
                await asyncio.sleep(0.3)
        except:
            pass
