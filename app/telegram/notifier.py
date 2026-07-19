"""
Change notification system for Telegram.
Monitors detected changes and sends notifications to admin users.
"""

import asyncio
from typing import Optional, List
from datetime import datetime, timezone

from loguru import logger
from aiogram import Bot

from app.config import get_settings
from app.database.session import DatabaseSession
from app.database.repository import (
    ChangeRepository,
    NotificationRepository,
    ScraperSettingsRepository,
    RealEstateRepository,
)
from app.telegram.bot import send_notification


class ChangeNotifier:
    """
    Monitors for unsent change notifications and delivers them to Telegram users.
    Runs as a background task to ensure timely delivery of alerts.
    """

    def __init__(self, bot: Bot):
        self.bot = bot
        self.settings = get_settings()
        self._running = False

    async def start(self) -> None:
        """Start the notification monitoring loop."""
        if not self.settings.telegram.bot_token:
            logger.warning("BOT_TOKEN not set, notifications disabled")
            return

        self._running = True
        logger.info("Change notifier started")

        while self._running:
            try:
                await self._process_pending_notifications()
                await asyncio.sleep(5)  # Check every 5 seconds
            except Exception as e:
                logger.error(f"Notification loop error: {e}")
                await asyncio.sleep(10)

    async def stop(self) -> None:
        """Stop the notification monitor."""
        self._running = False
        logger.info("Change notifier stopped")

    async def _process_pending_notifications(self) -> None:
        async with DatabaseSession.get_session_context() as session:
            change_repo = ChangeRepository(session)
            notification_repo = NotificationRepository(session)
            settings_repo = ScraperSettingsRepository(session)

            notify_free = await settings_repo.get("notify_free_found")
            if notify_free is None:
                await settings_repo.set("notify_free_found", "1")
                notify_free = "1"

            # Separate toggle for the "possibly freed" (Payday owner-change) signal.
            notify_possibly = await settings_repo.get("notify_possibly_freed")
            if notify_possibly is None:
                await settings_repo.set("notify_possibly_freed", "1")
                notify_possibly = "1"

            changes = await change_repo.get_unnotified()

            for change in changes:
                try:
                    # Skip apartment_freed if toggle is off
                    if change.field_name == "apartment_freed" and notify_free != "1":
                        await change_repo.mark_notified(change.id)
                        continue

                    message = self._build_change_message(change)

                    for user_id in self.settings.telegram.allowed_users:
                        success = await send_notification(self.bot, user_id, message)
                        if success:
                            await notification_repo.create(
                                change_id=change.id,
                                apartment_id=change.apartment_id,
                                message=message,
                            )

                    await change_repo.mark_notified(change.id)

                except Exception as e:
                    logger.error(f"Failed to process change {change.id}: {e}")
                    continue

            # Deliver realestate catalog events (freed / occupied / owner_changed / possibly_freed).
            await self._process_realestate_events(session, notify_free, notify_possibly)

    async def _process_realestate_events(
        self, session, notify_free: str, notify_possibly: str
    ) -> None:
        """Send pending events detected from the /realestate catalog."""
        realestate_repo = RealEstateRepository(session)
        events = await realestate_repo.get_unnotified_events()

        for event in events:
            try:
                # "freed" respects the same free-notification toggle as the map source.
                if event.event_type == "freed" and notify_free != "1":
                    await realestate_repo.mark_event_notified(event.id)
                    continue

                # "possibly_freed" (Payday owner change) has its own toggle.
                if event.event_type == "possibly_freed" and notify_possibly != "1":
                    await realestate_repo.mark_event_notified(event.id)
                    continue

                message = self._build_realestate_message(event)
                for user_id in self.settings.telegram.allowed_users:
                    await send_notification(self.bot, user_id, message)

                await realestate_repo.mark_event_notified(event.id)

            except Exception as e:
                logger.error(f"Failed to process realestate event {event.id}: {e}")
                continue

    def _build_realestate_message(self, event) -> str:
        """Render a realestate event as a Telegram message."""
        kind_ru = "Дом" if event.kind == "house" else "Квартира"
        name = event.name or f"{kind_ru} #{event.object_key.split(':')[-1]}"

        parts: List[str] = []
        if event.event_type == "freed":
            parts.append(f"🎉 <b>Освободилось: {kind_ru.lower()}!</b>")
            parts.append("━━━━━━━━━━━━━━━")
            parts.append(f"🏠 {name}")
            if event.class_name:
                parts.append(f"🏷 Класс: {event.class_name}")
            if event.building_name:
                parts.append(f"🏢 {event.building_name}")
            if event.price:
                parts.append(f"💰 Цена: {event.price:,}".replace(",", " "))
            if event.old_owner:
                parts.append(f"👤 Бывший владелец: {event.old_owner}")
            parts.append("\n⚡️ Можно покупать — успей первым!")

        elif event.event_type == "occupied":
            parts.append(f"🔴 <b>Занято: {kind_ru.lower()}</b>")
            parts.append("━━━━━━━━━━━━━━━")
            parts.append(f"🏠 {name}")
            if event.new_owner:
                parts.append(f"👤 Новый владелец: {event.new_owner}")

        else:  # owner_changed
            parts.append(f"🔄 <b>Смена владельца: {kind_ru.lower()}</b>")
            parts.append("━━━━━━━━━━━━━━━")
            parts.append(f"🏠 {name}")
            parts.append(f"• Было: {event.old_owner or '—'}")
            parts.append(f"• Стало: {event.new_owner or '—'}")

        return "\n".join(parts)

    def _format_timestamp(self, val: Optional[str]) -> str:
        """Convert any timestamp to readable 'DD.MM.YYYY HH:MM:SS'."""
        if not val or val == "None":
            return "—"
        from datetime import datetime
        for fmt in ["%d.%m.%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"]:
            try:
                return datetime.strptime(str(val), fmt).strftime("%d.%m.%Y %H:%M:%S")
            except ValueError:
                continue
        return str(val)

    def _build_change_message(self, change) -> str:
        apartment_name = change.apartment.name if change.apartment else "Неизвестно"

        # Special handling for freed apartment
        if change.field_name == "apartment_freed":
            free = change.apartment.free_apartments if change.apartment else 0
            total = change.apartment.total_apartments if change.apartment else 0
            msg = (
                f"🎉 <b>Квартира освободилась!</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🏠 {apartment_name}\n"
                f"🟢 Свободно: {free}/{total}\n"
            )
            if change.apartment and change.apartment.address:
                msg += f"📍 {change.apartment.address}\n"
            return msg

        emoji = "🔄"
        if "free" in change.field_name.lower():
            emoji = "🟢" if change.new_value and change.new_value not in ("None", "0") else "🔴"
        elif "occupied" in change.field_name.lower():
            emoji = "🔴"

        field_name_ru = self._translate_field(change.field_name)

        msg = f"{emoji} <b>Изменение: {apartment_name}</b>\n"
        msg += f"━━━━━━━━━━━━━━━\n"

        if field_name_ru == "Время обновления":
            old = self._format_timestamp(change.old_value)
            new = self._format_timestamp(change.new_value)
        else:
            old = change.old_value if (change.old_value and change.old_value != "None") else None
            new = change.new_value if (change.new_value and change.new_value != "None") else None

        msg += f"📋 <b>Изменилось:</b> {field_name_ru}\n"
        if old is not None:
            msg += f"• Было: {old}\n"
        if new is not None:
            msg += f"• Стало: {new}\n"

        if change.apartment:
            free = change.apartment.free_apartments
            total = change.apartment.total_apartments
            if free is not None and total is not None:
                status = "🟢 Есть свободные" if free > 0 else "🔴 Всё занято"
                msg += f"\n{status} ({free}/{total})"

        return msg

    def _translate_field(self, field_name: str) -> str:
        """Translate field name to Russian for display."""
        translations = {
            "free_apartments": "Свободно",
            "occupied_apartments": "Занято",
            "total_apartments": "Всего квартир",
            "last_updated": "Время обновления",
            "description": "Описание",
            "address": "Адрес",
            "wiki_url": "Ссылка на Wiki",
            "raw_content": "Содержимое карточки",
        }

        # Check for type fields
        if field_name.startswith("type_"):
            parts = field_name.split("_")
            if len(parts) >= 3:
                class_name = parts[1].capitalize()
                field_type = "свободно" if parts[2] == "free" else "занято"
                return f"{class_name} ({field_type})"

        if field_name.startswith("new_field_"):
            return f"Новое поле: {field_name[10:]}"

        if field_name.startswith("new_type_"):
            return f"Новый тип: {field_name[9:]}"

        return translations.get(field_name, field_name)