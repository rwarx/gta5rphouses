"""
Telegram bot for GTA5RP Apartment Checker.
Provides commands for viewing apartment status, history, and notifications.
"""

import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

from loguru import logger
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from app.config import get_settings
from app.database.session import DatabaseSession
from app.database.repository import (
    ApartmentRepository,
    ChangeRepository,
    ScraperLogRepository,
    ApartmentHistoryRepository,
)
from app.scraper.scheduler import SmartScheduler


class ApartmentBot:
    """Telegram bot for apartment monitoring system."""

    def __init__(self, scheduler: Optional[SmartScheduler] = None):
        self.settings = get_settings()
        self.scheduler = scheduler
        self.bot: Optional[Bot] = None
        self.dp: Optional[Dispatcher] = None

    async def start(self) -> None:
        """Initialize and start the bot."""
        if not self.settings.telegram.bot_token:
            logger.warning("BOT_TOKEN not set, Telegram bot disabled")
            return

        logger.info("Starting Telegram bot...")
        self.bot = Bot(token=self.settings.telegram.bot_token)
        self.dp = Dispatcher(storage=MemoryStorage())

        # Register handlers
        self._register_handlers()

        # Start polling
        logger.info("Telegram bot started polling")
        await self.dp.start_polling(self.bot, skip_updates=True)

    async def stop(self) -> None:
        """Stop the bot."""
        if self.bot:
            await self.bot.session.close()
            logger.info("Telegram bot stopped")

    def _register_handlers(self) -> None:
        """Register command handlers."""
        if not self.dp:
            return

        # Register commands
        self.dp.message.register(self.cmd_start, CommandStart())
        self.dp.message.register(self.cmd_help, Command("help"))
        self.dp.message.register(self.cmd_list, Command("list"))
        self.dp.message.register(self.cmd_search, Command("search"))
        self.dp.message.register(self.cmd_status, Command("status"))
        self.dp.message.register(self.cmd_free, Command("free"))
        self.dp.message.register(self.cmd_occupied, Command("occupied"))
        self.dp.message.register(self.cmd_history, Command("history"))
        self.dp.message.register(self.cmd_last_update, Command("last_update"))
        self.dp.message.register(self.cmd_stats, Command("stats"))
        self.dp.message.register(self.cmd_scrape, Command("scrape"))
        self.dp.message.register(self.cmd_crash_status, Command("crash_status"))
        self.dp.message.register(self.cmd_crash_on, Command("crash_on"))
        self.dp.message.register(self.cmd_crash_off, Command("crash_off"))
        self.dp.message.register(self.cmd_map_check, Command("map_check"))
        self.dp.message.register(self.cmd_free_notify, Command("free_notify"))
        self.dp.message.register(self.cmd_crashday, Command("crashday"))

        # Register callback queries
        self.dp.callback_query.register(self._on_callback)

    def _is_admin(self, user_id: int) -> bool:
        """Check if user is an admin."""
        allowed = self.settings.telegram.allowed_users
        if not allowed:
            return True  # No restrictions
        return user_id in allowed

    def _get_keyboard(self, user_id: int = 0) -> InlineKeyboardMarkup:
        is_admin = self._is_admin(user_id) if user_id else False
        buttons = [
            [
                InlineKeyboardButton(text="🏠 Список", callback_data="list"),
                InlineKeyboardButton(text="✅ Свободные", callback_data="free"),
            ],
            [
                InlineKeyboardButton(text="🔍 Поиск", callback_data="search"),
                InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
            ],
            [
                InlineKeyboardButton(text="🔄 Обновление", callback_data="last_update"),
                InlineKeyboardButton(text="📉 Слёты", callback_data="crashday"),
            ],
        ]
        if is_admin:
            buttons.append([
                InlineKeyboardButton(text="🔔 Гос. уведомления", callback_data="free_notify_toggle"),
                InlineKeyboardButton(text="❓ Помощь", callback_data="help"),
            ])
        else:
            buttons.append([
                InlineKeyboardButton(text="❓ Помощь", callback_data="help"),
            ])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    async def _on_callback(self, query: CallbackQuery) -> None:
        data = query.data
        is_admin = self._is_admin(query.from_user.id)

        if data == "free_notify_toggle":
            if not is_admin:
                await query.answer("⛔ Только для админов", show_alert=True)
                return
            await query.answer()
            await self._toggle_free_notify(query.message)
            return

        handlers = {
            "list": self.cmd_list,
            "free": self.cmd_free,
            "search": self._callback_search,
            "stats": self.cmd_stats,
            "last_update": self.cmd_last_update,
            "crashday": self.cmd_crashday,
            "help": self.cmd_help,
        }
        handler = handlers.get(data)
        if handler and data == "search":
            await query.answer()
            await query.message.answer("🔍 Введите /search <b>название</b> для поиска квартиры", parse_mode="HTML")
        elif handler:
            await query.answer()
            await handler(query.message)
        else:
            await query.answer("Неизвестная команда", show_alert=True)

    async def _callback_search(self, message: Message) -> None:
        pass

    async def _get_free_notify_setting(self) -> str:
        async with DatabaseSession.get_session_context() as session:
            from app.database.repository import ScraperSettingsRepository
            repo = ScraperSettingsRepository(session)
            val = await repo.get("notify_free_found")
            if val is None:
                await repo.set("notify_free_found", "1")
                return "1"
            return val

    async def _toggle_free_notify(self, message: Message) -> None:
        current = await self._get_free_notify_setting()
        new_val = "0" if current == "1" else "1"
        async with DatabaseSession.get_session_context() as session:
            from app.database.repository import ScraperSettingsRepository
            repo = ScraperSettingsRepository(session)
            await repo.set("notify_free_found", new_val)
        status = "🔔 Включены" if new_val == "1" else "🔕 Выключены"
        await message.answer(f"Уведомления о свободных квартирах: {status}", parse_mode="HTML")

    async def cmd_free_notify(self, message: Message) -> None:
        if not self._is_admin(message.from_user.id):
            return
        current = await self._get_free_notify_setting()
        status = "🔔 Включены" if current == "1" else "🔕 Выключены"
        text = (
            f"<b>Уведомления о свободных квартирах</b>\n\n"
            f"Статус: {status}\n\n"
            f"/free_notify — показать статус\n"
            f"Кнопка «Гос. уведомления» — переключить"
        )
        await message.answer(text, parse_mode="HTML")

    async def cmd_start(self, message: Message) -> None:
        uid = message.from_user.id
        text = (
            "<b>🏠 GTA5RP · Murrieta</b>\n"
            "┗ Мониторинг квартир\n\n"
            "📌 <b>Команды:</b>\n"
            "  /list — список всех квартир\n"
            "  /free — свободные\n"
            "  /search <i>текст</i> — поиск\n"
            "  /status <i>id</i> — статус\n"
            "  /stats — статистика\n"
            "  /crashday — слёты за сегодня\n"
            "  /last_update — последний запуск\n\n"
            "<i>Также используйте кнопки ниже</i> 👇"
        )
        await message.answer(text, parse_mode="HTML", reply_markup=self._get_keyboard(uid))

    async def cmd_help(self, message: Message) -> None:
        text = (
            "📖 <b>Помощь</b>\n\n"
            "  /list — список всех квартир\n"
            "  /search <i>текст</i> — поиск по названию\n"
            "  /status <i>id</i> — инфо о квартире\n"
            "  /free — свободные квартиры\n"
            "  /occupied — занятые квартиры\n"
            "  /history <i>id</i> — история изменений\n"
            "  /last_update — время обновления\n"
            "  /stats — статистика системы\n"
            "  /crashday — слёты за сегодня"
        )
        await message.answer(text, parse_mode="HTML")

    async def cmd_list(self, message: Message) -> None:
        uid = message.from_user.id
        async with DatabaseSession.get_session_context() as session:
            repo = ApartmentRepository(session)
            apartments = await repo.get_all()
            stats = await repo.get_statistics()

        if not apartments:
            await message.answer("🏠 Нет данных о квартирах.")
            return

        free_count = sum(1 for a in apartments if a.free_apartments and a.free_apartments > 0)
        total_count = len(apartments)
        lines = [
            "<b>🏠 Все квартиры · Murrieta</b>\n",
            f"🟢 Свободно: {stats['total_free']} | 🔴 Занято: {stats['total_occupied']} | 📦 Всего: {stats['total_units']}\n",
            f"┃ 🏠 Зданий со свободными: {free_count}/{total_count}\n",
        ]
        for i, apt in enumerate(apartments, 1):
            if apt.free_apartments and apt.free_apartments > 0:
                icon = "🟢"
                status = "свободно"
            else:
                icon = "🔴"
                status = "занято"
            free = apt.free_apartments or 0
            total = apt.total_apartments or 0
            lines.append(f"{icon} {i:02d}. {apt.name} — {status} | {free}/{total}")

        kb = self._get_keyboard(uid)
        text = "\n".join(lines)
        if len(text) > 4000:
            for i in range(0, len(text), 4000):
                await message.answer(text[i:i+4000], parse_mode="HTML", reply_markup=kb)
        else:
            await message.answer(text, parse_mode="HTML", reply_markup=kb)

    async def cmd_status(self, message: Message) -> None:
        args = message.text.replace("/status", "").strip()
        if not args:
            await message.answer("Укажите ID или название. Пример: /status 1")
            return

        async with DatabaseSession.get_session_context() as session:
            repo = ApartmentRepository(session)
            try:
                apt_id = int(args)
                apartment = await repo.get_with_types(apt_id)
            except ValueError:
                results = await repo.search(args)
                apartment = results[0] if results else None

        if not apartment:
            await message.answer(f"Квартира '{args}' не найдена.")
            return

        free = apartment.free_apartments or 0
        total = apartment.total_apartments or 0
        occupied = apartment.occupied_apartments or 0
        bar_len = 12
        free_blocks = round(free / max(total, 1) * bar_len) if total > 0 else 0
        bar = "🟩" * free_blocks + "⬛" * (bar_len - free_blocks)

        text = (
            f"<b>🏠 {apartment.name}</b>\n"
            f"┣ 📍 {apartment.address or '—'}\n"
            f"┣ {bar}\n"
            f"┣ 🟢 Свободно: {free}  |  🔴 Занято: {occupied}\n"
            f"┗ 📊 Всего: {total}\n"
        )

        if apartment.apartment_types:
            for t in apartment.apartment_types:
                icon = "🟢" if t.free and t.free > 0 else "🔴"
                text += f"\n{icon} {t.class_name}: {t.free or 0} св. / {t.occupied or 0} зан."

        if apartment.last_updated:
            text += f"\n\n🕐 {apartment.last_updated.strftime('%d.%m.%Y %H:%M:%S')}"

        await message.answer(text, parse_mode="HTML")

    async def cmd_free(self, message: Message) -> None:
        uid = message.from_user.id
        async with DatabaseSession.get_session_context() as session:
            repo = ApartmentRepository(session)
            free_apts = await repo.get_free_apartments()
            stats = await repo.get_statistics()

        if not free_apts:
            await message.answer("😢 Нет свободных квартир.")
            return

        lines = [f"<b>🟢 Свободно: {stats['total_free']}/{stats['total_units']}</b>\n"]
        for i, apt in enumerate(free_apts, 1):
            lines.append(f"{i:02d}. <b>{apt.name}</b> — свободно {apt.free_apartments}")
        await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=self._get_keyboard(uid))

    async def cmd_occupied(self, message: Message) -> None:
        async with DatabaseSession.get_session_context() as session:
            repo = ApartmentRepository(session)
            apartments = await repo.get_all()

        occupied = [a for a in apartments if a.free_apartments == 0 and a.total_apartments and a.total_apartments > 0]
        if not occupied:
            await message.answer("🎉 Все квартиры свободны!")
            return

        lines = ["<b>🔴 Полностью занятые</b>\n"]
        for i, apt in enumerate(occupied, 1):
            lines.append(f"{i:02d}. {apt.name} — {apt.total_apartments}/{apt.total_apartments}")
        await message.answer("\n".join(lines), parse_mode="HTML")

    async def cmd_history(self, message: Message) -> None:
        args = message.text.replace("/history", "").strip()

        async with DatabaseSession.get_session_context() as session:
            change_repo = ChangeRepository(session)
            if args:
                try:
                    apt_id = int(args)
                    changes = await change_repo.get_changes_by_apartment(apt_id, limit=10)
                except ValueError:
                    await message.answer("Укажите числовой ID квартиры")
                    return

                if not changes:
                    await message.answer(f"Нет изменений для квартиры #{args}.")
                    return

                lines = [f"<b>📋 История · кв. #{args}</b>\n"]
                for c in changes:
                    t = c.detected_at.strftime("%d.%m %H:%M") if c.detected_at else "?"
                    lines.append(f"[{t}] {c.field_name}: {c.old_value or '—'} → {c.new_value}")
                await message.answer("\n".join(lines), parse_mode="HTML")
            else:
                changes = await change_repo.get_recent(limit=10)
                if not changes:
                    await message.answer("Изменений пока нет.")
                    return

                lines = ["<b>📋 Последние изменения</b>\n"]
                for c in changes:
                    t = c.detected_at.strftime("%d.%m %H:%M") if c.detected_at else "?"
                    name = c.apartment.name if c.apartment else "?"
                    lines.append(f"[{t}] {name}: {c.field_name}")
                await message.answer("\n".join(lines), parse_mode="HTML")

    async def cmd_last_update(self, message: Message) -> None:
        uid = message.from_user.id
        async with DatabaseSession.get_session_context() as session:
            repo = ScraperLogRepository(session)
            stats = await repo.get_statistics()

        if not stats["last_run"]:
            await message.answer("Парсер ещё не запускался.")
            return

        last_run = stats["last_run"]
        if isinstance(last_run, str):
            from datetime import datetime
            for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"]:
                try:
                    last_run = datetime.strptime(last_run.rsplit(".", 1)[0], fmt.replace(".%f", "")).strftime("%d.%m.%Y %H:%M:%S")
                    break
                except ValueError:
                    continue

        text = (
            f"🔄 <b>Обновление данных</b>\n\n"
            f"🕐 {last_run}\n"
            f"📊 Статус: {stats['last_run_status']}\n"
            f"✅ Успешно: {stats['successful_runs']}  ❌ Ошибок: {stats['failed_runs']}\n"
            f"📈 Успешность: {stats['success_rate']}%\n"
        )

        if self.scheduler:
            s = self.scheduler.stats
            text += (
                f"\n<b>Парсер:</b>\n"
                f"Режим: {s['current_mode']} | Payday: {'Да' if s['is_payday_window'] else 'Нет'}\n"
                f"Запусков: {s['total_runs']} | Ошибок подряд: {s['consecutive_failures']}"
            )

        await message.answer(text, parse_mode="HTML", reply_markup=self._get_keyboard(uid))

    async def cmd_stats(self, message: Message) -> None:
        uid = message.from_user.id
        async with DatabaseSession.get_session_context() as session:
            apt_repo = ApartmentRepository(session)
            log_repo = ScraperLogRepository(session)
            apt_stats = await apt_repo.get_statistics()
            log_stats = await log_repo.get_statistics()

        bar_len = 10
        free_pct = apt_stats["total_free"] / max(apt_stats["total_units"], 1)
        occ_blocks = round((1 - free_pct) * bar_len)
        bar = "🟩" * (bar_len - occ_blocks) + "⬛" * occ_blocks

        text = (
            f"📊 <b>Статистика · Murrieta</b>\n"
            f"┏━━━━━━━━━━━━━━━\n"
            f"┃ {bar}\n"
            f"┃ 🟢 Свободно: {apt_stats['total_free']}  🔴 Занято: {apt_stats['total_occupied']}\n"
            f"┃ 🏠 Зданий: {apt_stats['total_apartments']}  📦 Всего кв.: {apt_stats['total_units']}\n"
            f"┃ 📈 Заполненность: {apt_stats['occupancy_rate']}%\n"
            f"┗━━━━━━━━━━━━━━━\n\n"
            f"<b>Парсер</b>\n"
            f"┣ ✅ Успешно: {log_stats['successful_runs']}\n"
            f"┣ ❌ Ошибок: {log_stats['failed_runs']}\n"
            f"┗ 📈 Успешность: {log_stats['success_rate']}%"
        )
        await message.answer(text, parse_mode="HTML", reply_markup=self._get_keyboard(uid))

    async def cmd_search(self, message: Message) -> None:
        query = message.text.replace("/search", "").strip()
        if not query:
            await message.answer("🔍 Укажите запрос. Пример: /search Сан Винсент")
            return

        async with DatabaseSession.get_session_context() as session:
            repo = ApartmentRepository(session)
            results = await repo.search(query)

        if not results:
            await message.answer(f"🔍 По запросу '{query}' ничего не найдено.")
            return

        lines = [f"<b>🔍 Результаты: {query}</b>\n"]
        for apt in results:
            icon = "🟢" if apt.free_apartments and apt.free_apartments > 0 else "🔴"
            lines.append(f"{icon} {apt.name} — {apt.free_apartments or 0}/{apt.total_apartments or 0}")
        await message.answer("\n".join(lines), parse_mode="HTML")

    async def cmd_scrape(self, message: Message) -> None:
        if not self._is_admin(message.from_user.id):
            return
        if not self.scheduler:
            await message.answer("❌ Парсер не запущен.")
            return
        await message.answer("🔄 Запуск парсера...")
        try:
            result = await self.scheduler.force_scrape()
            if result:
                await message.answer(f"✅ Обработано {len(result)} квартир.")
            else:
                await message.answer("⚠️ Ошибка при парсинге.")
        except Exception as e:
            await message.answer(f"❌ {e}")

    async def cmd_crash_status(self, message: Message) -> None:
        if not self._is_admin(message.from_user.id):
            return
        from app.scraper.crash_detector import get_crash_detector
        detector = get_crash_detector()
        stats = await detector.get_crash_stats()

        text = (
            f"🚨 <b>Краш-детектор</b>\n"
            f"┣ Статус: {'🟢 Включён' if stats['enabled'] else '🔴 Выключен'}\n"
            f"┣ Слётов: {stats['total_crashes_detected']}\n"
            f"┗ Последний: {stats['last_crash'] or '—'}\n"
        )
        if stats.get("crash_history"):
            text += "\n<b>История:</b>\n"
            for e in stats["crash_history"]:
                text += f"• {e['time']}: {e['free_change']} ({e['change_pct']})\n"
        await message.answer(text, parse_mode="HTML")

    async def cmd_crash_on(self, message: Message) -> None:
        if not self._is_admin(message.from_user.id):
            return
        from app.scraper.crash_detector import get_crash_detector
        await get_crash_detector().set_enabled(True)
        await message.answer("✅ Краш-детектор <b>включён</b>", parse_mode="HTML")

    async def cmd_crash_off(self, message: Message) -> None:
        if not self._is_admin(message.from_user.id):
            return
        from app.scraper.crash_detector import get_crash_detector
        await get_crash_detector().set_enabled(False)
        await message.answer("🔴 Краш-детектор <b>выключен</b>", parse_mode="HTML")

    async def cmd_crashday(self, message: Message) -> None:
        uid = message.from_user.id
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with DatabaseSession.get_session_context() as session:
            from app.database.repository import CrashDayLogRepository
            repo = CrashDayLogRepository(session)
            records = await repo.get_by_date(today)

        if not records:
            await message.answer("📉 Слётов за сегодня не было.", parse_mode="HTML")
            return

        lines = [f"<b>📉 Слёты за {today}</b>\n"]
        total = 0
        for i, rec in enumerate(records, 1):
            import json
            try:
                apts = json.loads(rec.apartments_data)
            except (json.JSONDecodeError, TypeError):
                apts = []
            total += rec.total_freed
            t = rec.detected_at.strftime("%H:%M") if rec.detected_at else "?"
            lines.append(f"<b>{i}. {t}</b> — освободилось {rec.total_freed} кв.:")
            for apt in apts:
                lines.append(f"   🏠 {apt}")
            lines.append("")

        lines.append(f"┃ Всего освободилось квартир: {total}")
        await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=self._get_keyboard(uid))

    async def cmd_map_check(self, message: Message) -> None:
        if not self._is_admin(message.from_user.id):
            return
        async with DatabaseSession.get_session_context() as session:
            from app.scraper.crash_detector import get_crash_detector
            result = await get_crash_detector().check_map_version_change(session)
        if result:
            text = (
                "🔄 <b>Обновление карты!</b>\n"
                f"┣ Было: {result['old_version']}\n"
                f"┣ Стало: {result['new_version']}\n"
                f"┗ Разница: {result['time_diff_seconds']:.0f} сек"
            )
        else:
            text = "✅ Карта стабильна."
        await message.answer(text, parse_mode="HTML")


async def send_notification(
    bot: Bot,
    chat_id: int,
    text: str,
) -> bool:
    """
    Send a notification to a specific user.

    Args:
        bot: Bot instance.
        chat_id: Telegram user ID.
        text: Message text.

    Returns:
        True if sent successfully.
    """
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return True
    except TelegramForbiddenError:
        logger.warning(f"User {chat_id} blocked the bot")
        return False
    except TelegramRetryAfter as e:
        logger.warning(f"Rate limited, waiting {e.retry_after}s")
        await asyncio.sleep(e.retry_after)
        return await send_notification(bot, chat_id, text)
    except Exception as e:
        logger.error(f"Failed to send notification to {chat_id}: {e}")
        return False


async def run_bot(scheduler: Optional[SmartScheduler] = None) -> None:
    """Run the Telegram bot."""
    bot = ApartmentBot(scheduler)
    await bot.start()