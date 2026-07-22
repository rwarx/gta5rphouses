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
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest


class BotStates(StatesGroup):
    """FSM states for button-driven text input.

    Each button that needs free text (a search query, a nickname, a building
    name, …) puts the user into one of these states and reads their next
    message, so the flow works without the user typing a slash command.
    """
    search = State()          # apartment name search (/search)
    status = State()          # apartment id/name status (/status)
    history = State()         # apartment change history (/history)
    owners = State()          # owner nickname lookup (/owners)
    building = State()        # building name lookup (/building)
    owner_history = State()   # object owner timeline (/owner_history)
    login = State()           # password entry state

from app.config import get_settings
from app.database.session import DatabaseSession
from app.database.repository import (
    ApartmentRepository,
    ChangeRepository,
    ScraperLogRepository,
    ApartmentHistoryRepository,
    RealEstateRepository,
)
from app.scraper.scheduler import SmartScheduler


class ApartmentBot:
    """Telegram bot for apartment monitoring system."""

    def __init__(self, scheduler: Optional[SmartScheduler] = None):
        self.settings = get_settings()
        self.scheduler = scheduler
        self.bot: Optional[Bot] = None
        self.dp: Optional[Dispatcher] = None
        # Users who entered the correct bot password (in-memory, resets on restart).
        # Admin users (ALLOWED_USER_IDS) skip password check entirely.
        self._authorized_users: set[int] = set()

    async def start(self) -> None:
        """Initialize and start the bot."""
        if not self.settings.telegram.bot_token:
            logger.warning("BOT_TOKEN not set, Telegram bot disabled")
            return

        logger.info("Starting Telegram bot...")
        self.bot = Bot(token=self.settings.telegram.bot_token)
        self.dp = Dispatcher(storage=MemoryStorage())

        # Auth middleware: reject messages and callbacks from unauthorized users
        self.dp.message.outer_middleware.register(self._auth_middleware)
        self.dp.callback_query.outer_middleware.register(self._auth_middleware)

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
        self.dp.message.register(self.cmd_password, Command("password"))
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
        self.dp.message.register(self.cmd_realestate, Command("realestate"))
        self.dp.message.register(self.cmd_buildings, Command("buildings"))
        self.dp.message.register(self.cmd_building, Command("building"))
        self.dp.message.register(self.cmd_houses, Command("houses"))
        self.dp.message.register(self.cmd_owners, Command("owners"))
        self.dp.message.register(self.cmd_owner_history, Command("owner_history"))
        self.dp.message.register(self.cmd_possibly_notify, Command("possibly_notify"))
        self.dp.message.register(self.cmd_report, Command("report"))
        self.dp.message.register(self.cmd_last_data, Command("last_data"))
        self.dp.message.register(self.cmd_servers, Command("servers"))
        self.dp.message.register(self.cmd_subscribe, Command("subscribe"))
        self.dp.message.register(self.cmd_unsubscribe, Command("unsubscribe"))
        self.dp.message.register(self.cmd_subscriptions, Command("subscriptions"))
        self.dp.message.register(self.cmd_menu, Command("menu"))

        # FSM: buttons that need free text put the user into a state; these
        # handlers read the next message and run the corresponding query.
        self.dp.message.register(self._state_search, BotStates.search)
        self.dp.message.register(self._state_status, BotStates.status)
        self.dp.message.register(self._state_history, BotStates.history)
        self.dp.message.register(self._state_owners, BotStates.owners)
        self.dp.message.register(self._state_building, BotStates.building)
        self.dp.message.register(self._state_owner_history, BotStates.owner_history)
        self.dp.message.register(self._state_login, BotStates.login)

        # Register callback queries
        self.dp.callback_query.register(self._on_callback)



    def _check_auth(self, user_id: int) -> bool:
        """Check if a user is authorized (entered correct password or is admin).

        Admin users skip the password gate entirely. Regular users must have
        entered the bot password at /start to use commands.
        """
        if self._is_admin(user_id):
            return True
        return user_id in self._authorized_users

    def _is_admin(self, user_id: int) -> bool:
        """Check if user is an admin.

        Fail closed: if ALLOWED_USER_IDS is unset, nobody is an admin. Admin
        actions gate destructive/operational commands (run scraper, toggle
        crash detection, change notification settings), so an empty list must
        NOT grant everyone access.
        """
        allowed = self.settings.telegram.allowed_users
        if not allowed:
            return False
        return user_id in allowed

    # ==================================================================
    # Inline-menu UI
    # ------------------------------------------------------------------
    # The bot is menu-first: /start (or /menu) opens the main menu and every
    # feature is reachable by tapping. Navigation between menu screens edits
    # the current message in place; commands that produce long listings send
    # fresh messages with a compact "back to menu" keyboard. Buttons that need
    # free text (search, nickname, …) put the user into an FSM state.
    # ==================================================================

    @staticmethod
    def _btn(text: str, data: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=data)

    def _get_keyboard(self, user_id: int = 0) -> InlineKeyboardMarkup:
        """Main menu. Kept under this name so long-listing handlers can reuse it."""
        b = self._btn
        rows = [
            [b("🏠 Квартиры", "menu:apartments"), b("🏢 Каталог", "menu:catalog")],
            [b("🔔 Подписки", "menu:subs"), b("🕐 Отчёты", "menu:reports")],
            [b("📉 Слёты сегодня", "act:crashday"), b("🔄 Обновление", "act:last_update")],
        ]
        last = [b("❓ Помощь", "act:help")]
        if user_id and self._is_admin(user_id):
            last.insert(0, b("⚙️ Админ", "menu:admin"))
        rows.append(last)
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _back_kb(self, section: str = "main") -> InlineKeyboardMarkup:
        """Compact keyboard appended to data listings: jump back to a menu."""
        return InlineKeyboardMarkup(inline_keyboard=[[
            self._btn("⬅️ В меню", f"menu:{section}"),
        ]])

    _MENU_TEXT = {
        "main": (
            "<b>🏠 GTA5RP · Мониторинг недвижимости</b>\n"
            "Выберите раздел 👇"
        ),
        "apartments": (
            "<b>🏠 Квартиры</b>\n"
            "Списки, статус и поиск по зданиям выбранного сервера."
        ),
        "catalog": (
            "<b>🏢 Каталог владельцев</b>\n"
            "Занятые дома и квартиры, поиск по владельцу, история ников."
        ),
        "reports": (
            "<b>🕐 Отчёты</b>\n"
            "Почасовая сводка слётов и смен ников, плюс гос-отчёт за пейдей "
            "(слетевшие дома и квартиры после обновления карты)."
        ),
        "admin": (
            "<b>⚙️ Администрирование</b>\n"
            "Управление парсером и краш-детектором."
        ),
    }

    def _menu_markup(self, section: str, uid: int) -> InlineKeyboardMarkup:
        b = self._btn
        if section == "apartments":
            rows = [
                [b("🏠 Все квартиры", "act:list"), b("✅ Свободные", "act:free")],
                [b("🔴 Занятые", "act:occupied"), b("📊 Статистика", "act:stats")],
                [b("🔍 Поиск", "ask:search"), b("ℹ️ Статус квартиры", "ask:status")],
                [b("📋 История изменений", "ask:history")],
                [b("⬅️ Назад", "menu:main")],
            ]
        elif section == "catalog":
            rows = [
                [b("🏢 Статус каталога", "act:realestate")],
                [b("🏢 Здания", "pick:buildings"), b("🏠 Дома", "pick:houses")],
                [b("👤 По владельцу", "ask:owners"), b("📜 История ников", "ask:owner_history")],
                [b("⏱ Срок владения", "act:ownership_durations"),
                 b("⚡ Возможные слёты", "act:possible_frees")],
                [b("🏠+🏢 Дом + квартира", "act:both_owners")],
                [b("🌐 Серверы", "act:servers")],
                [b("⬅️ Назад", "menu:main")],
            ]
        elif section == "reports":
            rows = [
                [b("🕐 Отчёт сейчас", "act:report_now")],
                [b("📊 Последние данные", "act:latest_data")],
            ]
            if self._is_admin(uid):
                rows.append([b("🔔 Авто-отчёт (вкл/выкл)", "adm:report_toggle")])
                rows.append([b("🏛 Гос-отчёт за пейдей (вкл/выкл)", "adm:payday_toggle")])
                rows.append([b("⚡ «Возможные слёты» (вкл/выкл)", "adm:possibly_toggle")])
            rows.append([b("⬅️ Назад", "menu:main")])
        elif section == "admin":
            rows = [
                [b("🔄 Запустить парсер", "adm:scrape")],
                [b("🚨 Краш-статус", "adm:crash_status")],
                [b("🟢 Краш вкл", "adm:crash_on"), b("🔴 Краш выкл", "adm:crash_off")],
                [b("🗺 Проверить карту", "adm:map_check")],
                [b("🔔 Гос-уведомления (вкл/выкл)", "adm:free_notify")],
                [b("⬅️ Назад", "menu:main")],
            ]
        else:  # main
            return self._get_keyboard(uid)
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def _subs_markup(self, uid: int) -> InlineKeyboardMarkup:
        """Subscriptions screen: current subs (tap to remove) + subscribe entry."""
        from app.database.repository import SubscriptionRepository
        from app.scraper.realestate_client import sid_to_server_name

        async with DatabaseSession.get_session_context() as session:
            repo = SubscriptionRepository(session)
            subs = await repo.list_for_user(uid)

        kind_ru = {"any": "все", "house": "дома", "apartment": "кв."}
        rows = []
        for s in subs:
            name = sid_to_server_name(s.server_sid) or f"sid {s.server_sid}"
            label = f"{name} · {kind_ru.get(s.kind, s.kind)}"
            if s.class_name:
                label += f" ({s.class_name})"
            del_suffix = f":{s.kind}"
            if s.class_name:
                del_suffix += f":{s.class_name}"
            rows.append([self._btn(f"❌ {label}", f"sub:del:{s.server_sid}{del_suffix}")])
        rows.append([self._btn("➕ Подписаться", "menu:subpick")])
        rows.append([self._btn("⬅️ Назад", "menu:main")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _server_pick_markup(self, action: str,
                            active_sid: Optional[str] = None) -> InlineKeyboardMarkup:
        """Server picker for the given action (selsrv/buildings/houses/sub).

        For `selsrv` (the active-server choice at /start) we offer the FULL wiki
        server list — the user may start tracking any of them, and the scheduler
        picks it up dynamically. For every other action we only list the servers
        already configured/monitored, since those actions read existing catalog
        data. The long full list is laid out two per row to stay compact; the
        currently-active server (if any) is marked with a check.
        """
        from app.scraper.realestate_client import resolve_servers, all_wiki_servers

        if action == "selsrv":
            servers = all_wiki_servers()
            btns = [
                self._btn(f"{'✅ ' if sid == active_sid else '🌐 '}{name}", f"{action}:{sid}")
                for sid, name in servers.items()
            ]
            rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
            rows.append([self._btn("⬅️ В меню", "menu:main")])
            return InlineKeyboardMarkup(inline_keyboard=rows)

        if action == "subkind":
            servers = all_wiki_servers()
            btns = [self._btn(f"🌐 {name}", f"{action}:{sid}") for sid, name in servers.items()]
            rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
            rows.append([self._btn("⬅️ Назад", "menu:subs")])
            return InlineKeyboardMarkup(inline_keyboard=rows)

        servers = resolve_servers(self.settings.realestate.server_names)
        rows = [[self._btn(f"🌐 {name}", f"{action}:{sid}")] for sid, name in servers.items()]
        rows.append([self._btn("⬅️ Назад", "menu:catalog")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _sub_kind_markup(self, sid: str) -> InlineKeyboardMarkup:
        from app.scraper.realestate_client import sid_to_server_name
        name = sid_to_server_name(sid) or f"sid {sid}"
        b = self._btn
        return InlineKeyboardMarkup(inline_keyboard=[
            [b(f"🔔 {name}: все объекты", f"sub:add:{sid}:any")],
            [b("🏠 дома", f"sub:class:{sid}"),
             b("🏢 только квартиры", f"sub:add:{sid}:apartment")],
            [b("⬅️ Назад", "menu:subpick")],
        ])

    def _sub_class_markup(self, sid: str) -> InlineKeyboardMarkup:
        from app.scraper.realestate_client import sid_to_server_name
        name = sid_to_server_name(sid) or f"sid {sid}"
        b = self._btn
        return InlineKeyboardMarkup(inline_keyboard=[
            [b(f"🔔 {name}: дома (любой класс)", f"sub:add:{sid}:house")],
            [b("🏠 Престиж", f"sub:add:{sid}:house:Престиж"),
             b("🏠 Стандарт", f"sub:add:{sid}:house:Стандарт")],
            [b("🏠 Эконом", f"sub:add:{sid}:house:Эконом"),
             b("🏠 Комфорт", f"sub:add:{sid}:house:Комфорт")],
            [b("🏠 Премиум", f"sub:add:{sid}:house:Премиум")],
            [b("⬅️ Назад", f"subkind:{sid}")],
        ])

    async def _edit_menu(self, query: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
        """Edit the message in place; fall back to a new message if edit fails."""
        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except TelegramBadRequest:
            # Message has no editable text (e.g. it was a data listing) or is
            # unchanged — just send a fresh menu instead.
            await query.message.answer(text, parse_mode="HTML", reply_markup=markup)

    async def _on_callback(self, query: CallbackQuery, state: FSMContext) -> None:
        data = query.data or ""
        uid = query.from_user.id
        is_admin = self._is_admin(uid)

        # --- Menu navigation (delete old messages, send fresh menu) ---
        if data.startswith("menu:"):
            section = data.split(":", 1)[1]
            await query.answer()
            if section == "admin" and not is_admin:
                await query.answer("⛔ Только для админов", show_alert=True)
                return
            try:
                await query.message.delete()
            except Exception:
                pass
            text = self._MENU_TEXT.get(section, self._MENU_TEXT["main"])
            if section == "subs":
                text = "<b>🔔 Подписки на слёты</b>\n" \
                       "Уведомления приходят вам лично по выбранным серверам."
                markup = await self._subs_markup(uid)
            elif section == "subpick":
                text = "<b>➕ Выберите сервер</b>"
                markup = self._server_pick_markup("subkind")
            else:
                markup = self._menu_markup(section, uid)
            await query.message.answer(text, parse_mode="HTML", reply_markup=markup)
            return

        # --- Active-server selection (persisted per user) ---
        if data.startswith("selsrv:"):
            sid = data.split(":", 1)[1]
            from app.scraper.realestate_client import sid_to_server_name
            await self._set_user_sid(uid, sid)
            name = sid_to_server_name(sid) or f"sid {sid}"
            await query.answer(f"🌐 Сервер: {name}, загружаю данные...")
            # Immediately fetch catalog data for the selected server
            try:
                from app.scraper.realestate_client import RealEstateClient
                from app.scraper.realestate_detector import RealEstateDetector
                client = RealEstateClient()
                snapshot = await client.fetch_snapshot(sid)
                if snapshot and (snapshot.houses or snapshot.apartments or snapshot.buildings):
                    async with DatabaseSession.get_session_context() as session:
                        detector = RealEstateDetector(session)
                        await detector.process_snapshot(snapshot, is_payday=False)
                    await query.message.answer(
                        f"✅ Данные сервера <b>{name}</b> загружены:\n"
                        f"🏠 Домов: {len(snapshot.houses)} | "
                        f"🏢 Квартир: {len(snapshot.apartments)} | "
                        f"🏘 Зданий: {len(snapshot.buildings)}",
                        parse_mode="HTML",
                    )
                else:
                    await query.message.answer(
                        f"⚠️ Данные для сервера {name} пока недоступны. "
                        "Каталог обновится автоматически в ближайшее время."
                    )
            except Exception as e:
                from loguru import logger
                logger.warning(f"Immediate fetch for {name} failed: {e}")
            await self._edit_menu(
                query,
                self._MENU_TEXT["main"] + f"\n\n<i>Активный сервер: {name}</i>",
                self._get_keyboard(uid),
            )
            return

        # --- Server picker for catalog listings ---
        if data.startswith("pick:"):
            action = data.split(":", 1)[1]  # buildings | houses
            await query.answer()
            # Default to the user's active server; the 🌐 Серверы screen is the
            # place to switch, so we don't re-prompt on every listing.
            sid, _ = await self._default_sid_for_user(uid)
            await self._run_catalog(action, query.message, sid)
            return

        if data.startswith("buildings:") or data.startswith("houses:"):
            action, sid = data.split(":", 1)
            await query.answer()
            await self._run_catalog(action, query.message, sid)
            return

        # --- Subscriptions ---
        if data.startswith("subkind:"):
            sid = data.split(":", 1)[1]
            await query.answer()
            await self._edit_menu(query, "<b>Выберите тип объектов</b>", self._sub_kind_markup(sid))
            return
        if data.startswith("sub:class:"):
            sid = data.split(":", 2)[2]
            await query.answer()
            await self._edit_menu(query, f"<b>Выберите класс дома</b>", self._sub_class_markup(sid))
            return
        if data.startswith("sub:add:"):
            parts = data.split(":")
            # sub:add:<sid>:<kind> or sub:add:<sid>:<kind>:<class_name>
            sid = parts[2]
            kind = parts[3]
            class_name = parts[4] if len(parts) >= 5 else None
            await self._do_subscribe(uid, sid, kind, class_name)
            await query.answer("✅ Подписка оформлена")
            await self._edit_menu(query, "<b>🔔 Подписки на слёты</b>\n"
                                  "Уведомления приходят вам лично по выбранным серверам.",
                                  await self._subs_markup(uid))
            return
        if data.startswith("sub:del:"):
            parts = data.split(":")
            sid = parts[2]
            kind = parts[3] if len(parts) >= 4 else None
            class_name = parts[4] if len(parts) >= 5 else None
            await self._do_unsubscribe(uid, sid, kind, class_name)
            await query.answer("❌ Подписка удалена")
            await self._edit_menu(query, "<b>🔔 Подписки на слёты</b>\n"
                                  "Уведомления приходят вам лично по выбранным серверам.",
                                  await self._subs_markup(uid))
            return

        # --- Text-input actions: enter FSM state and prompt ---
        if data.startswith("ask:"):
            await query.answer()
            await self._prompt_input(query.message, state, data.split(":", 1)[1])
            return

        # --- Admin actions ---
        if data.startswith("adm:"):
            if not is_admin:
                await query.answer("⛔ Только для админов", show_alert=True)
                return
            await query.answer()
            await self._run_admin(data.split(":", 1)[1], query.message, uid)
            return

        # --- Simple data actions (fresh message) ---
        if data.startswith("act:"):
            await query.answer()
            await self._run_action(data.split(":", 1)[1], query.message, uid)
            return

        # --- Owner history callback ---
        if data.startswith("hst:"):
            await query.answer()
            object_key = data.split(":", 1)[1]
            await self._render_owner_history(query.message, object_key)
            return

        await query.answer("Неизвестная команда", show_alert=True)

    async def cmd_menu(self, message: Message, state: Optional[FSMContext] = None) -> None:
        """Open the main inline menu (also the escape hatch from any FSM prompt)."""
        if not self._check_auth(message.from_user.id):
            await self.cmd_start(message, state)
            return
        if state is not None:
            await state.clear()
        await message.answer(self._MENU_TEXT["main"], parse_mode="HTML",
                             reply_markup=self._get_keyboard(message.from_user.id))

    # ---- Callback action dispatch ----

    async def _run_action(self, action: str, message: Message, uid: int) -> None:
        handlers = {
            "list": self.cmd_list,
            "free": self.cmd_free,
            "occupied": self.cmd_occupied,
            "stats": self.cmd_stats,
            "last_update": self.cmd_last_update,
            "crashday": self.cmd_crashday,
            "realestate": self.cmd_realestate,
            "servers": self.cmd_servers,
            "help": self.cmd_help,
            "report_now": self._report_now,
            "latest_data": self._latest_data,
            "ownership_durations": self.cmd_ownership_durations,
            "possible_frees": self.cmd_possible_frees,
            "both_owners": self.cmd_both_owners,
        }
        handler = handlers.get(action)
        if handler:
            await handler(message, user_id=uid)

    async def _run_catalog(self, action: str, message: Message, sid: Optional[str]) -> None:
        """Render a buildings/houses listing for a specific server sid."""
        if not sid:
            await message.answer("⚠️ Сервер не распознан.")
            return
        if action == "buildings":
            await self._render_buildings(message, sid)
        elif action == "houses":
            await self._render_houses(message, sid, query=None)

    async def _run_admin(self, action: str, message: Message, uid: int) -> None:
        if action == "report_toggle":
            await self._toggle_setting_report(message, "hourly_report", "Почасовой отчёт")
        elif action == "payday_toggle":
            await self._toggle_setting_report(message, "payday_report", "Гос-отчёт за пейдей")
        elif action == "possibly_toggle":
            await self._toggle_setting_report(message, "notify_possibly_freed",
                                              "Уведомления о «возможных слётах»")
        elif action == "free_notify":
            await self._toggle_free_notify(message)
        elif action == "scrape":
            await self.cmd_scrape(message, user_id=uid)
        elif action == "crash_status":
            await self.cmd_crash_status(message, user_id=uid)
        elif action == "crash_on":
            await self.cmd_crash_on(message, user_id=uid)
        elif action == "crash_off":
            await self.cmd_crash_off(message, user_id=uid)
        elif action == "map_check":
            await self.cmd_map_check(message, user_id=uid)

    async def _prompt_input(self, message: Message, state: FSMContext, kind: str) -> None:
        prompts = {
            "search": (BotStates.search, "🔍 Введите <b>название</b> квартиры для поиска:"),
            "status": (BotStates.status, "ℹ️ Введите <b>ID или название</b> квартиры:"),
            "history": (BotStates.history, "📋 Введите <b>ID квартиры</b> для истории изменений:"),
            "owners": (BotStates.owners, "👤 Введите <b>ник владельца</b>:"),
            "building": (BotStates.building, "🏢 Введите <b>название здания</b>:"),
            "owner_history": (BotStates.owner_history,
                              "📜 Введите <b>ключ объекта</b> (напр. <code>20:house:242</code>):"),
        }
        st, text = prompts[kind]
        await state.set_state(st)
        await message.answer(text + "\n\n<i>Отмена: /menu</i>", parse_mode="HTML")

    # ---- FSM state handlers (read the next message, then run the query) ----

    @staticmethod
    async def _state_text(message: Message, state: FSMContext) -> Optional[str]:
        """Return the trimmed text of a state reply, or None if it was not text.

        Clears the state either way so a stray photo/sticker doesn't trap the
        user; on non-text input it nudges them back to the menu.
        """
        await state.clear()
        text = (message.text or "").strip()
        if not text:
            await message.answer("⚠️ Ожидался текст. Откройте меню: /menu")
            return None
        return text

    async def _state_search(self, message: Message, state: FSMContext) -> None:
        text = await self._state_text(message, state)
        if text is not None:
            await self._render_search(message, text)

    async def _state_status(self, message: Message, state: FSMContext) -> None:
        text = await self._state_text(message, state)
        if text is not None:
            await self._render_status(message, text)

    async def _state_history(self, message: Message, state: FSMContext) -> None:
        text = await self._state_text(message, state)
        if text is not None:
            await self._render_history(message, text)

    async def _state_owners(self, message: Message, state: FSMContext) -> None:
        text = await self._state_text(message, state)
        if text is not None:
            await self._render_owners(message, self._current_sid(),
                                      self.settings.realestate.server_name, text)

    async def _state_building(self, message: Message, state: FSMContext) -> None:
        text = await self._state_text(message, state)
        if text is not None:
            await self._render_building(message, self._current_sid(),
                                        self.settings.realestate.server_name, text)

    async def _state_owner_history(self, message: Message, state: FSMContext) -> None:
        text = await self._state_text(message, state)
        if text is not None:
            await self._render_owner_history(message, text)

    # ---- Subscription helpers (use the REAL invoking user id) ----

    async def _do_subscribe(
        self, uid: int, sid: str, kind: str,
        class_name: Optional[str] = None,
    ) -> None:
        from app.database.repository import SubscriptionRepository, UserServerSelectionRepository
        from app.scraper.realestate_client import sid_to_server_name
        async with DatabaseSession.get_session_context() as session:
            repo = SubscriptionRepository(session)
            await repo.subscribe(uid, sid, kind=kind, class_name=class_name)
            # Ensure this server gets polled by the scheduler. Save it as the
            # user's active selection so _refresh_servers (which unions all user
            # selections) picks it up. This way subscribing to ANY wiki server
            # immediately starts its catalog fetches.
            sel_repo = UserServerSelectionRepository(session)
            await sel_repo.set(uid, sid)

    async def _do_unsubscribe(
        self, uid: int, sid: str,
        kind: Optional[str] = None, class_name: Optional[str] = None,
    ) -> None:
        from app.database.repository import SubscriptionRepository
        async with DatabaseSession.get_session_context() as session:
            repo = SubscriptionRepository(session)
            await repo.unsubscribe(uid, sid, kind=kind, class_name=class_name)

    async def _toggle_setting_report(self, message: Message, key: str, label: str) -> None:
        from app.database.repository import ScraperSettingsRepository
        async with DatabaseSession.get_session_context() as session:
            repo = ScraperSettingsRepository(session)
            current = await repo.get(key)
            if current is None:
                current = "1"
            new_val = "0" if current == "1" else "1"
            await repo.set(key, new_val)
        status = "🔔 включён" if new_val == "1" else "🔕 выключен"
        await message.answer(f"{label}: {status}.")

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

    async def cmd_realestate(self, message: Message, user_id: Optional[int] = None) -> None:
        """Show the /realestate source status and recent freed objects."""
        from app.database.repository import RealEstateRepository
        from app.scraper.realestate_client import resolve_servers

        rs = self.settings.realestate
        servers = resolve_servers(rs.server_names)
        active_sid, _ = await self._default_sid_for_user(user_id or message.from_user.id)

        async with DatabaseSession.get_session_context() as session:
            repo = RealEstateRepository(session)
            occupied_by_server = {
                sid: await repo.count_occupied(sid) for sid in servers
            }
            recent = await repo.get_recent_events(limit=10, event_type="freed")

        status = "🟢 Включён" if rs.enabled else "🔴 Выключен"
        lines = [
            "<b>🏢 Каталог /realestate</b>",
            f"┣ Источник: {status}",
            f"┣ Интервал: {rs.interval}s",
            "┣ Серверы:",
        ]
        for sid, name in servers.items():
            mark = " ⭐" if sid == active_sid else ""
            lines.append(f"┃   • {name} (sid {sid}){mark} — занятых: {occupied_by_server.get(sid, 0)}")
        lines.append("┗ /subscribe &lt;сервер&gt; — подписка на уведомления")

        if recent:
            lines.append("\n<b>🎉 Последние освобождения:</b>")
            for e in recent:
                when = e.detected_at.strftime("%d.%m %H:%M") if e.detected_at else "—"
                kind_ru = "дом" if e.kind == "house" else "кв."
                price = f" · {e.price:,}".replace(",", " ") if e.price else ""
                srv = servers.get(e.server_sid)
                srv_tag = f" [{srv}]" if srv else ""
                lines.append(f"• {when}{srv_tag} — {kind_ru} {e.name or e.object_key}{price}")
        else:
            lines.append("\n<i>Освобождений пока не зафиксировано.</i>")

        await message.answer("\n".join(lines), parse_mode="HTML",
                             reply_markup=self._back_kb("catalog"))

    # ---- Catalog: owner listings (houses / apartments) ----

    @staticmethod
    def _fmt_price(price: Optional[int]) -> str:
        """Format a price with space thousands separators, or '—'."""
        if not price:
            return "—"
        return f"{price:,}".replace(",", " ")

    async def _reply_chunked(self, message: Message, lines: List[str], header: str = "",
                             footer_kb: Optional[InlineKeyboardMarkup] = None) -> None:
        """Send potentially long line lists as multiple <=4000-char messages.

        `footer_kb`, when given, is attached to the final chunk so listings sent
        from the inline menu carry a "back to menu" button.
        """
        if not lines:
            await message.answer(header or "Нет данных.", parse_mode="HTML", reply_markup=footer_kb)
            return
        chunks: List[str] = []
        buf = header
        for line in lines:
            piece = ("\n" if buf else "") + line
            if len(buf) + len(piece) > 4000:
                chunks.append(buf)
                buf = line
            else:
                buf += piece
        if buf:
            chunks.append(buf)
        for i, chunk in enumerate(chunks):
            kb = footer_kb if i == len(chunks) - 1 else None
            await message.answer(chunk, parse_mode="HTML", reply_markup=kb)

    def _current_sid(self) -> Optional[str]:
        from app.scraper.realestate_client import server_name_to_sid
        return server_name_to_sid(self.settings.realestate.server_name)

    def _parse_server_and_query(
        self, text: str, command: str,
        default_sid: Optional[str] = None, default_name: Optional[str] = None,
    ) -> tuple:
        """Split a catalog command's args into (sid, server_name, remaining_query).

        A leading `server=<name>` or `@<name>` token, or a bare first token that
        matches a monitored server name, selects that server. Otherwise we fall
        back to `default_sid`/`default_name` (the caller passes the user's active
        server) and, failing that, the primary REALESTATE_SERVER — so the whole
        arg stays as the query. This keeps single-server usage unchanged while
        allowing per-server queries and per-user defaults.
        """
        from app.scraper.realestate_client import server_name_to_sid, resolve_servers

        arg = text.replace(command, "", 1).strip()
        monitored = resolve_servers(self.settings.realestate.server_names)
        names_by_lower = {n.lower(): (s, n) for s, n in monitored.items()}

        chosen_sid = None
        chosen_name = None
        if arg:
            first, _, rest = arg.partition(" ")
            token = first
            if token.startswith("server="):
                token = token[len("server="):]
            elif token.startswith("@"):
                token = token[1:]
            hit = names_by_lower.get(token.lower())
            if hit:
                chosen_sid, chosen_name = hit
                arg = rest.strip()

        if chosen_sid is None:
            if default_sid:
                chosen_sid, chosen_name = default_sid, default_name
            else:
                chosen_name = self.settings.realestate.server_name
                chosen_sid = server_name_to_sid(chosen_name)

        return chosen_sid, chosen_name, arg

    async def cmd_buildings(self, message: Message) -> None:
        """List apartment buildings with free/total counts."""
        d_sid, d_name = await self._default_sid_for_user(message.from_user.id)
        sid, server_name, _ = self._parse_server_and_query(
            message.text, "/buildings", d_sid, d_name)
        await self._render_buildings(message, sid, server_name)

    async def _render_buildings(self, message: Message, sid: Optional[str],
                                server_name: Optional[str] = None) -> None:
        from app.database.repository import RealEstateRepository
        from app.scraper.realestate_client import sid_to_server_name
        from app.services.tax import format_tax

        if not sid:
            await message.answer("⚠️ Сервер не распознан.")
            return
        server_name = server_name or sid_to_server_name(sid) or f"sid {sid}"

        async with DatabaseSession.get_session_context() as session:
            repo = RealEstateRepository(session)
            all_apts = await repo.list_occupied(
                sid, kind="apartment", limit=5000
            )

        if not all_apts:
            await message.answer("🏢 Занятых квартир пока нет. Дождитесь первого сканирования.",
                                 reply_markup=self._back_kb("catalog"))
            return

        from collections import defaultdict
        by_building = defaultdict(list)
        for a in all_apts:
            by_building[a.building_name or "Без здания"].append(a)

        lines = []
        for bld_name in sorted(by_building):
            units = sorted(by_building[bld_name], key=lambda u: u.unit_id)
            total = len(units)
            lines.append(f"\n<b>{bld_name}</b> — {total} кв.")
            for u in units:
                cls = f" · 🏷 {u.class_name}" if u.class_name else ""
                garage = f" · 🚗 {u.vehicle_count}гм" if u.vehicle_count else ""
                tax_str = f" · {format_tax(u.class_name)}" if u.class_name else ""
                lines.append(
                    f"🔴 {u.name or ('#' + str(u.unit_id))} · ID #{u.unit_id}{cls}{garage}\n"
                    f"    👤 {u.owner_name or '—'} · 💰 {self._fmt_price(u.price)}{tax_str}"
                )

        header = f"<b>🏢 Все квартиры с владельцами · {server_name}</b>\n"
        await self._reply_chunked(message, lines, header,
                                  footer_kb=self._back_kb("catalog"))

    async def cmd_ownership_durations(self, message: Message, user_id: Optional[int] = None) -> None:
        """Show how long each current owner has held their property."""
        uid = user_id or message.from_user.id
        d_sid, d_name = await self._default_sid_for_user(uid)
        if not d_sid:
            await message.answer("⚠️ Сначала выберите сервер.")
            return
        from app.scraper.realestate_client import sid_to_server_name
        server_name = sid_to_server_name(d_sid) or f"sid {d_sid}"
        await self._render_ownership_durations(message, d_sid, server_name)

    async def _render_ownership_durations(
        self, message: Message, sid: str, server_name: str
    ) -> None:
        from app.database.repository import RealEstateRepository
        from app.telegram.notifier import format_duration
        from app.services.tax import format_tax, paid_status
        from datetime import timedelta

        async with DatabaseSession.get_session_context() as session:
            repo = RealEstateRepository(session)
            rows = await repo.get_all_current_ownership_durations(sid)

        if not rows:
            await message.answer("⏱ Нет данных о времени владения.",
                                 reply_markup=self._back_kb("catalog"))
            return

        houses = [r for r in rows if r["kind"] == "house"]
        apts = [r for r in rows if r["kind"] == "apartment"]

        lines = []
        if houses:
            lines.append("<b>🏠 Дома</b>")
            for r in sorted(houses, key=lambda x: (x["duration"] or timedelta()).total_seconds(), reverse=True):
                dur = format_duration(r["duration"]) if r["duration"] else "—"
                name = r["name"] or f"Дом #{r['unit_id']}"
                lines.append(f"• {name} · 👤 {r['owner_name']} — ⏱ {dur}")
        if apts:
            if houses:
                lines.append("")
            lines.append("<b>🏢 Квартиры</b>")
            by_bld = sorted(apts, key=lambda x: (
                x.get("building_name") or "",
                (x["duration"] or timedelta()).total_seconds()
            ), reverse=True)
            for r in by_bld:
                dur = format_duration(r["duration"]) if r["duration"] else "—"
                where = f" · {r['building_name']}" if r.get("building_name") else ""
                name = r["name"] or f"#{r['unit_id']}"
                days = (r["duration"].total_seconds() / 86400) if r["duration"] else 0
                badge = f" {paid_status(days)}" if days > 0 else ""
                tax_str = f" · {format_tax(r['class_name'])}" if r.get("class_name") else ""
                lines.append(f"• {name}{where} · 👤 {r['owner_name']} — ⏱ {dur}{badge}{tax_str}")

        header = f"<b>⏱ Срок владения · {server_name}</b>\n"
        await self._reply_chunked(message, lines, header,
                                  footer_kb=self._back_kb("catalog"))

    async def cmd_possible_frees(self, message: Message, user_id: Optional[int] = None) -> None:
        """Show objects at risk of freeing based on ownership duration."""
        uid = user_id or message.from_user.id
        d_sid, d_name = await self._default_sid_for_user(uid)
        if not d_sid:
            await message.answer("⚠️ Сначала выберите сервер.")
            return
        await self._render_possible_frees(message, d_sid, d_name or "")

    async def _render_possible_frees(self, message: Message, sid: str,
                                     server_name: str) -> None:
        from app.database.repository import RealEstateRepository
        from app.scraper.realestate_client import sid_to_server_name
        from app.telegram.notifier import format_duration
        from datetime import timedelta

        server_name = server_name or sid_to_server_name(sid) or f"sid {sid}"

        async with DatabaseSession.get_session_context() as session:
            repo = RealEstateRepository(session)
            rows = await repo.get_all_current_ownership_durations(sid)
            # Find owners whose apartment freed recently (last 24h)
            recent = await repo.get_events_since(
                since=datetime.utcnow() - timedelta(hours=24),
                event_types=["freed"],
                server_sid=sid,
            )
            apt_freed_owners = set(
                e.old_owner for e in recent
                if e.kind == "apartment" and e.old_owner
            )

        if not rows:
            await message.answer("⏱ Нет данных о времени владения.",
                                 reply_markup=self._back_kb("catalog"))
            return

        houses = [r for r in rows if r["kind"] == "house"]
        apts = [r for r in rows if r["kind"] == "apartment"]

        lines = []
        # — houses at risk because their owner's apartment freed —
        if apt_freed_owners:
            at_risk = [r for r in houses if r["owner_name"] in apt_freed_owners]
            if at_risk:
                lines.append("<b>🔴 Дом под риском (квартира уже слетела)</b>")
                for r in sorted(at_risk, key=lambda x: (x["duration"] or timedelta()).total_seconds()):
                    dur = r["duration"]
                    name = r["name"] or f"#{r['unit_id']}"
                    dur_str = format_duration(dur) if dur else "—"
                    lines.append(
                        f"• {name} · 👤 {r['owner_name']} — ⏱ {dur_str}"
                    )
                lines.append("")

        # — risk groups —
        for label, kind, items in [("🏠 Дома", "house", houses),
                                   ("🏢 Квартиры", "apartment", apts)]:
            if not items:
                continue
            if lines:
                lines.append("")
            lines.append(f"<b>{label}</b>")
            for r in sorted(items, key=lambda x: (x["duration"] or timedelta()).total_seconds()):
                dur = r["duration"]
                days = dur.total_seconds() / 86400 if dur else 0
                name = r["name"] or f"#{r['unit_id']}"
                where = f" · {r['building_name']}" if r.get("building_name") else ""
                dur_str = format_duration(dur) if dur else "—"

                if days < 7:
                    badge = "🔴 риск"
                elif days < 30:
                    badge = "🟡 оплачено"
                else:
                    badge = "🟢 VIP"

                lines.append(
                    f"• {name}{where} · 👤 {r['owner_name']} — ⏱ {dur_str} — {badge}"
                )

        header = (
            f"<b>⚡ Прогноз слётов · {server_name}</b>\n"
            f"🔴 &lt;7д — риск (не оплачен)  🟡 7-30д — оплачено  🟢 &gt;30д — VIP\n\n"
        )
        await self._reply_chunked(message, lines, header,
                                  footer_kb=self._back_kb("catalog"))

    async def cmd_building(self, message: Message) -> None:
        """List all apartments (with owners) inside a building."""
        d_sid, d_name = await self._default_sid_for_user(message.from_user.id)
        sid, server_name, query = self._parse_server_and_query(
            message.text, "/building", d_sid, d_name)
        await self._render_building(message, sid, server_name, query)

    async def _render_building(self, message: Message, sid: Optional[str],
                               server_name: Optional[str], query: str) -> None:
        from app.database.repository import RealEstateRepository
        from app.scraper.realestate_client import sid_to_server_name

        if not query:
            await message.answer(
                "Укажите название здания. Пример: <code>/building Eclipse Towers</code>",
                parse_mode="HTML",
            )
            return

        if not sid:
            await message.answer("⚠️ Сервер не распознан.")
            return
        server_name = server_name or sid_to_server_name(sid) or f"sid {sid}"

        async with DatabaseSession.get_session_context() as session:
            repo = RealEstateRepository(session)
            # Resolve the building name from the (possibly partial) query.
            buildings = await repo.get_buildings(sid)
            match = next(
                (b for b in buildings if query.lower() in (b.name or "").lower()), None
            )
            building_name = match.name if match else query
            units = await repo.list_occupied(
                sid, kind="apartment", building_name=building_name
            )

        if not units:
            await message.answer(
                f"🏢 Занятых квартир в «{building_name}» не найдено "
                f"(проверьте название через /buildings)."
            )
            return

        units.sort(key=lambda u: u.unit_id)
        lines = []
        for u in units:
            cls = f" · 🏷 {u.class_name}" if u.class_name else ""
            garage = f" · 🚗 {u.vehicle_count}гм" if u.vehicle_count else ""
            lines.append(
                f"🔴 {u.name or ('#' + str(u.unit_id))} · ID #{u.unit_id}{cls}{garage}\n"
                f"    👤 {u.owner_name or '—'} · 💰 {self._fmt_price(u.price)}"
            )
        header = f"<b>🏢 {building_name} · {server_name}</b>\nЗанятых квартир: {len(units)}\n\n"
        await self._reply_chunked(message, lines, header, footer_kb=self._back_kb("catalog"))

    async def cmd_houses(self, message: Message) -> None:
        """List occupied private houses with owners; optional search filter."""
        d_sid, d_name = await self._default_sid_for_user(message.from_user.id)
        sid, server_name, query = self._parse_server_and_query(
            message.text, "/houses", d_sid, d_name)
        await self._render_houses(message, sid, server_name, query or None)

    async def _render_houses(self, message: Message, sid: Optional[str],
                             server_name: Optional[str] = None, query: Optional[str] = None) -> None:
        from app.database.repository import RealEstateRepository
        from app.scraper.realestate_client import sid_to_server_name

        if not sid:
            await message.answer("⚠️ Сервер не распознан.")
            return
        server_name = server_name or sid_to_server_name(sid) or f"sid {sid}"

        async with DatabaseSession.get_session_context() as session:
            repo = RealEstateRepository(session)
            houses = await repo.list_occupied(
                sid, kind="house", search=query or None, limit=1000
            )

        if not houses:
            await message.answer(
                "🏠 Домов не найдено." if query else
                "🏠 Данных о домах пока нет. Дождитесь первого сканирования."
            )
            return

        houses.sort(key=lambda u: u.unit_id)
        lines = []
        for u in houses:
            cls = f" · {u.class_name}" if u.class_name else ""
            if u.vehicle_count:
                cls += f" · 🚗 {u.vehicle_count}гм"
            lines.append(
                f"🔴 {u.name or ('Дом #' + str(u.unit_id))}{cls}\n"
                f"    👤 {u.owner_name or '—'} · 💰 {self._fmt_price(u.price)}"
            )
        title = f"поиск «{query}»" if query else "все занятые"
        header = f"<b>🏠 Дома · {server_name} · {title}</b>\nНайдено: {len(houses)}\n\n"
        await self._reply_chunked(message, lines, header, footer_kb=self._back_kb("catalog"))

    async def cmd_owners(self, message: Message) -> None:
        """Find all objects (houses + apartments) owned by a nickname."""
        d_sid, d_name = await self._default_sid_for_user(message.from_user.id)
        sid, server_name, query = self._parse_server_and_query(
            message.text, "/owners", d_sid, d_name)
        await self._render_owners(message, sid, server_name, query)

    async def _render_owners(self, message: Message, sid: Optional[str],
                             server_name: Optional[str], query: str) -> None:
        from app.database.repository import RealEstateRepository
        from app.scraper.realestate_client import sid_to_server_name

        if not query:
            await message.answer(
                "Укажите ник. Пример: <code>/owners Kirill_Morales</code>",
                parse_mode="HTML",
            )
            return

        if not sid:
            await message.answer("⚠️ Сервер не распознан.")
            return
        server_name = server_name or sid_to_server_name(sid) or f"sid {sid}"

        async with DatabaseSession.get_session_context() as session:
            repo = RealEstateRepository(session)
            objs = await repo.list_occupied(sid, search=query, limit=400)

        # list_occupied searches name+owner; keep only owner matches here.
        objs = [o for o in objs if query.lower() in (o.owner_name or "").lower()]
        if not objs:
            await message.answer(f"👤 Объектов у «{query}» не найдено.",
                                 reply_markup=self._back_kb("catalog"))
            return

        houses = [o for o in objs if o.kind == "house"]
        apts = [o for o in objs if o.kind == "apartment"]

        lines = []
        kb_rows = []
        if houses:
            lines.append("<b>🏠 Дома</b>")
            for o in sorted(houses, key=lambda x: x.unit_id):
                garage = f" · 🚗 {o.vehicle_count}гм" if o.vehicle_count else ""
                lines.append(
                    f"• ID #{o.unit_id} · 💰 {self._fmt_price(o.price)}"
                    + (f" · 🏷 {o.class_name}" if o.class_name else "")
                    + garage
                    + f" · 👤 {o.owner_name}"
                )
                key = repo.make_key(sid, o.kind, o.unit_id)
                kb_rows.append([self._btn(f"📜 ID #{o.unit_id}", f"hst:{key}")])
        if apts:
            if houses:
                lines.append("")
            lines.append("<b>🏢 Квартиры</b>")
            for o in sorted(apts, key=lambda x: (x.building_name or "", x.unit_id)):
                where = f" · {o.building_name}" if o.building_name else ""
                garage = f" · 🚗 {o.vehicle_count}гм" if o.vehicle_count else ""
                lines.append(
                    f"• ID #{o.unit_id}{where} · 💰 {self._fmt_price(o.price)}"
                    + (f" · 🏷 {o.class_name}" if o.class_name else "")
                    + garage
                    + f" · 👤 {o.owner_name}"
                )
                key = repo.make_key(sid, o.kind, o.unit_id)
                kb_rows.append([self._btn(f"📜 ID #{o.unit_id}", f"hst:{key}")])

        header = (
            f"<b>👤 Объекты игрока «{query}» · {server_name}</b>\n"
            f"🏠 Домов: {len(houses)} · 🏢 Квартир: {len(apts)}\n\n"
        )
        kb_rows.append([self._btn("⬅️ В меню", "menu:catalog")])
        markup = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        await self._reply_chunked(message, lines, header, footer_kb=markup)

    # ---- Per-server notification subscriptions ----

    async def cmd_servers(self, message: Message, user_id: Optional[int] = None) -> None:
        """Show monitored servers and let the user pick their active one."""
        from app.scraper.realestate_client import resolve_servers

        servers = resolve_servers(self.settings.realestate.server_names)
        if not servers:
            await message.answer("⚠️ Нет настроенных серверов (REALESTATE_SERVERS).",
                                 reply_markup=self._back_kb("catalog"))
            return

        uid = user_id or message.from_user.id
        active_sid, active_name = await self._default_sid_for_user(uid)

        # One button per server; the active one is marked. Tapping switches it.
        rows = []
        for sid, name in servers.items():
            mark = "✅ " if sid == active_sid else "🌐 "
            rows.append([self._btn(f"{mark}{name}", f"selsrv:{sid}")])
        rows.append([self._btn("⬅️ Назад", "menu:catalog")])
        markup = InlineKeyboardMarkup(inline_keyboard=rows)

        text = (
            "<b>🌐 Серверы</b>\n"
            f"Активный: <b>{active_name or '—'}</b>\n"
            "Нажмите, чтобы переключить — списки и карта будут показывать его.\n"
            "<i>Подписки на слёты: раздел 🔔 Подписки.</i>"
        )
        await message.answer(text, parse_mode="HTML", reply_markup=markup)

    async def cmd_subscribe(self, message: Message) -> None:
        """Subscribe the current user to freed-object alerts for a server."""
        from app.database.repository import SubscriptionRepository
        from app.scraper.realestate_client import resolve_servers, sid_to_server_name

        arg = message.text.replace("/subscribe", "", 1).strip()
        servers = resolve_servers(self.settings.realestate.server_names)
        if not arg:
            names = ", ".join(servers.values()) or "—"
            await message.answer(
                "Укажите сервер. Пример: <code>/subscribe Strawberry</code>\n"
                f"Доступные: {names}\n"
                "Можно уточнить тип: <code>/subscribe Strawberry house</code>",
                parse_mode="HTML",
            )
            return

        parts = arg.split()
        server_token = parts[0]
        kind = "any"
        if len(parts) > 1:
            k = parts[1].lower()
            if k in ("house", "houses", "дом", "дома"):
                kind = "house"
            elif k in ("apartment", "apartments", "apt", "кв", "квартира", "квартиры"):
                kind = "apartment"

        names_by_lower = {n.lower(): (s, n) for s, n in servers.items()}
        hit = names_by_lower.get(server_token.lower())
        if not hit:
            names = ", ".join(servers.values()) or "—"
            await message.answer(
                f"⚠️ Сервер «{server_token}» не отслеживается.\nДоступные: {names}"
            )
            return
        sid, name = hit

        async with DatabaseSession.get_session_context() as session:
            repo = SubscriptionRepository(session)
            await repo.subscribe(message.from_user.id, sid, kind=kind)

        kind_ru = {"any": "все объекты", "house": "только дома", "apartment": "только квартиры"}[kind]
        await message.answer(
            f"✅ Подписка оформлена: <b>{name}</b> ({kind_ru}).\n"
            "Уведомления о слётах будут приходить вам лично.",
            parse_mode="HTML",
        )

    async def cmd_unsubscribe(self, message: Message) -> None:
        """Remove the current user's subscription to a server."""
        from app.database.repository import SubscriptionRepository
        from app.scraper.realestate_client import resolve_servers

        arg = message.text.replace("/unsubscribe", "", 1).strip()
        servers = resolve_servers(self.settings.realestate.server_names)
        if not arg:
            await message.answer(
                "Укажите сервер. Пример: <code>/unsubscribe Strawberry</code>",
                parse_mode="HTML",
            )
            return

        names_by_lower = {n.lower(): (s, n) for s, n in servers.items()}
        hit = names_by_lower.get(arg.split()[0].lower())
        if not hit:
            await message.answer(f"⚠️ Сервер «{arg}» не найден.")
            return
        sid, name = hit

        async with DatabaseSession.get_session_context() as session:
            repo = SubscriptionRepository(session)
            removed = await repo.unsubscribe(message.from_user.id, sid)

        if removed:
            await message.answer(f"✅ Подписка на <b>{name}</b> отменена.", parse_mode="HTML")
        else:
            await message.answer(f"ℹ️ У вас не было подписки на «{name}».")

    async def cmd_subscriptions(self, message: Message) -> None:
        """Show the current user's active subscriptions."""
        from app.database.repository import SubscriptionRepository
        from app.scraper.realestate_client import sid_to_server_name

        async with DatabaseSession.get_session_context() as session:
            repo = SubscriptionRepository(session)
            subs = await repo.list_for_user(message.from_user.id)

        if not subs:
            await message.answer(
                "У вас нет активных подписок.\n"
                "Оформить: <code>/subscribe &lt;сервер&gt;</code>",
                parse_mode="HTML",
            )
            return

        kind_ru = {"any": "все", "house": "дома", "apartment": "квартиры"}
        lines = ["<b>🔔 Ваши подписки</b>"]
        for s in subs:
            name = sid_to_server_name(s.server_sid) or f"sid {s.server_sid}"
            lines.append(f"• {name} — {kind_ru.get(s.kind, s.kind)}")
        await message.answer("\n".join(lines), parse_mode="HTML")

    async def cmd_owner_history(self, message: Message) -> None:
        """Show the owner-nickname timeline for one object by its key."""
        query = message.text.replace("/owner_history", "").strip()
        await self._render_owner_history(message, query)

    async def _render_owner_history(self, message: Message, query: str) -> None:
        from app.database.repository import RealEstateRepository
        from app.telegram.notifier import format_duration

        if not query:
            await message.answer(
                "Укажите ключ объекта. Пример: <code>/owner_history 20:house:242</code>\n"
                "<i>Ключ показывается в уведомлениях и каталоге.</i>",
                parse_mode="HTML",
            )
            return

        async with DatabaseSession.get_session_context() as session:
            repo = RealEstateRepository(session)
            obj = await repo.get_object(query)
            history = await repo.get_owner_history_chronological(query, limit=50)

        if not obj and not history:
            await message.answer(f"🔍 Объект «{query}» не найден.",
                                 reply_markup=self._back_kb("catalog"))
            return

        title = (obj.name if obj else query) or query
        lines = [f"<b>📜 История владельцев</b>\n🏠 {title}\n"]
        if not history:
            lines.append("<i>Смен владельца не зафиксировано.</i>")
        else:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            for i, h in enumerate(history):
                when = h.recorded_at.strftime("%d.%m %H:%M") if h.recorded_at else "—"
                # Duration: for the latest entry → now, for older → next entry - current
                if i == len(history) - 1:
                    dur_td = now - h.recorded_at if h.recorded_at else timedelta()
                else:
                    dur_td = history[i + 1].recorded_at - h.recorded_at if h.recorded_at and history[i + 1].recorded_at else timedelta()
                dur_str = format_duration(dur_td) if dur_td.total_seconds() > 0 else ""
                dur_text = f" — <b>{dur_str}</b>" if dur_str else ""
                pd = " ⚡️" if h.during_payday else ""
                lines.append(
                    f"• {when}{pd}: {h.previous_owner or '—'} → {h.owner_name or '—'}{dur_text}"
                )
        await self._reply_chunked(message, lines[1:], lines[0] + "\n",
                                  footer_kb=self._back_kb("catalog"))

    async def cmd_possibly_notify(self, message: Message) -> None:
        """Toggle notifications for 'possibly freed' (Payday owner-change) events."""
        from app.database.repository import ScraperSettingsRepository

        if not self._is_admin(message.from_user.id):
            await message.answer("⛔ Только для админов.")
            return

        async with DatabaseSession.get_session_context() as session:
            repo = ScraperSettingsRepository(session)
            current = await repo.get("notify_possibly_freed")
            if current is None:
                current = "1"
            new_val = "0" if current == "1" else "1"
            await repo.set("notify_possibly_freed", new_val)

        status = "🔔 включены" if new_val == "1" else "🔕 выключены"
        await message.answer(
            f"Уведомления о «возможных слётах» (смена ника в пейдей) {status}."
        )

    async def cmd_last_data(self, message: Message) -> None:
        """Send the latest Payday data for the user's active server."""
        await self._latest_data(message, user_id=message.from_user.id)

    async def cmd_report(self, message: Message) -> None:
        """Send the last-hour Payday report now, or toggle the auto report.

        `/report` — build and send the report for the last hour immediately.
        `/report on` / `/report off` — enable/disable the automatic hourly report.
        """
        from datetime import datetime, timezone, timedelta
        from app.database.repository import (
            RealEstateRepository,
            ScraperSettingsRepository,
        )

        arg = message.text.replace("/report", "").strip().lower()

        if arg in ("on", "off"):
            if not self._is_admin(message.from_user.id):
                await message.answer("⛔ Только для админов.")
                return
            new_val = "1" if arg == "on" else "0"
            async with DatabaseSession.get_session_context() as session:
                repo = ScraperSettingsRepository(session)
                await repo.set("hourly_report", new_val)
            status = "🔔 включён" if new_val == "1" else "🔕 выключен"
            await message.answer(f"Почасовой отчёт {status}.")
            return

        await self._report_now(message)

    async def _report_now(self, message: Message, user_id: Optional[int] = None) -> None:
        """Build and send the last-hour Payday report immediately."""
        from datetime import datetime, timezone, timedelta
        from app.database.repository import RealEstateRepository

        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=1)
        async with DatabaseSession.get_session_context() as session:
            repo = RealEstateRepository(session)
            events = await repo.get_events_since(since)

        from app.telegram.notifier import _is_nickname_change
        freed_houses = [e for e in events if e.event_type == "freed" and e.kind == "house"]
        freed_apts = [e for e in events if e.event_type == "freed" and e.kind == "apartment"]
        possibly = [e for e in events if e.event_type == "possibly_freed"]
        real_possibly = [e for e in possibly if not _is_nickname_change(e.old_owner, e.new_owner)]
        nick_changes = len(possibly) - len(real_possibly)

        period = f"{since.strftime('%H:%M')}–{now.strftime('%H:%M')} UTC"
        lines = [
            "🕐 <b>Отчёт за последний час</b>",
            f"<i>{period}</i>",
            "━━━━━━━━━━━━━━━",
            f"🏠 Слетело домов: <b>{len(freed_houses)}</b>",
            f"🏢 Слетело квартир: <b>{len(freed_apts)}</b>",
            f"🔄 Смен ников (возможные слёты): <b>{len(real_possibly)}</b>",
        ]
        if nick_changes:
            lines.append(f"📝 Смена ника (не слёт): <b>{nick_changes}</b>")
        if real_possibly:
            lines.append("\n<b>Возможные слёты:</b>")
            for e in possibly:
                kind_ru = "дом" if e.kind == "house" else "кв."
                lines.append(
                    f"• {kind_ru} {e.name or e.object_key}: "
                    f"{e.old_owner or '—'} → {e.new_owner or '—'}"
                )
        elif not events:
            lines.append("\n<i>За этот час изменений не было.</i>")

        await message.answer("\n".join(lines), parse_mode="HTML",
                             reply_markup=self._back_kb("reports"))

    async def _latest_data(self, message: Message, user_id: Optional[int] = None) -> None:
        """Send the latest Payday data for the user's active server."""
        from datetime import datetime, timezone, timedelta
        from app.database.repository import RealEstateRepository
        from app.scraper.realestate_client import sid_to_server_name

        uid = user_id or message.from_user.id
        sid, server_name = await self._default_sid_for_user(uid)
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=6)

        async with DatabaseSession.get_session_context() as session:
            repo = RealEstateRepository(session)
            events = await repo.get_events_since(since, server_sid=sid)
            buildings = await repo.get_buildings(sid)
            occupied_houses = await repo.count_occupied(sid, kind="house")
            occupied_apts = await repo.count_occupied(sid, kind="apartment")

        from app.telegram.notifier import _is_nickname_change
        freed_houses = [e for e in events if e.event_type == "freed" and e.kind == "house"]
        freed_apts = [e for e in events if e.event_type == "freed" and e.kind == "apartment"]
        possibly = [e for e in events if e.event_type == "possibly_freed"]
        real_possibly = [e for e in possibly if not _is_nickname_change(e.old_owner, e.new_owner)]
        nick_changes = len(possibly) - len(real_possibly)

        apt_total = sum((b.apartments_count or 0) for b in buildings)
        apt_free = sum((b.free_count or 0) for b in buildings)

        lines = [
            f"📊 <b>Последние данные · {server_name}</b>",
            f"<i>{now.strftime('%d.%m.%Y %H:%M')} UTC</i>",
            "━━━━━━━━━━━━━━━",
            f"🏢 Квартир всего: {apt_total}, свободно: {apt_free}",
            f"🏠 Занятых домов: {occupied_houses}",
            f"🏢 Занятых квартир: {occupied_apts}",
            "━━━━━━━━━━━━━━━",
            f"🏠 Слетело домов (за 6ч): <b>{len(freed_houses)}</b>",
            f"🏢 Слетело квартир (за 6ч): <b>{len(freed_apts)}</b>",
            f"🔄 Смен ников (за 6ч): <b>{len(real_possibly)}</b>",
        ]
        if nick_changes:
            lines.append(f"📝 Смена ника (не слёт): <b>{nick_changes}</b>")

        if freed_houses:
            lines.append("\n<b>Слетевшие дома:</b>")
            for e in freed_houses:
                lines.append(f"• {e.name or '#' + str(e.unit_id)} ({e.class_name or '—'}) · 💰 {e.price or '—'}")
        if freed_apts:
            lines.append("\n<b>Слетевшие квартиры:</b>")
            for e in freed_apts:
                lines.append(f"• {e.name or '#' + str(e.unit_id)} · {e.building_name or '—'} · 💰 {e.price or '—'}")
        if real_possibly:
            lines.append("\n<b>Смены владельцев (возможные слёты):</b>")
            for e in possibly:
                kind_ru = "дом" if e.kind == "house" else "кв."
                lines.append(f"• {kind_ru} {e.name or e.object_key}: {e.old_owner or '—'} → {e.new_owner or '—'}")

        await self._reply_chunked(message, lines[1:], lines[0] + "\n",
                                  footer_kb=self._back_kb("reports"))

    async def cmd_start(self, message: Message, state: Optional[FSMContext] = None) -> None:
        uid = message.from_user.id

        if not self._check_auth(uid):
            await state.set_state(BotStates.login)
            await message.answer(
                "🔑 <b>Добро пожаловать!</b>\n"
                "Бот защищён паролем. Введите пароль для доступа:\n\n"
                "<i>Команда /start для повторного ввода</i>",
                parse_mode="HTML",
            )
            return

        # /start always asks which server to track
        active = await self._get_user_sid(uid)
        await message.answer(
            "<b>🏠 GTA5RP · Мониторинг недвижимости</b>\n"
            "Выберите сервер, который хотите отслеживать — карта и статистика "
            "домов/квартир будут показывать именно его.\n"
            "<i>Сменить можно в любой момент: /start или 🌐 Серверы.</i>",
            parse_mode="HTML",
            reply_markup=self._server_pick_markup("selsrv", active_sid=active),
        )

    async def cmd_password(self, message: Message, state: FSMContext) -> None:
        """Re-enter the bot password."""
        await state.set_state(BotStates.login)
        await message.answer(
            "🔑 Введите пароль для доступа к боту:",
            parse_mode="HTML",
        )

    async def _state_login(self, message: Message, state: FSMContext) -> None:
        """Handle password entry."""
        text = (message.text or "").strip()
        uid = message.from_user.id

        if not text:
            await message.answer("⚠️ Пожалуйста, введите пароль.")
            return

        if text == self.settings.telegram.bot_password:
            self._authorized_users.add(uid)
            await state.clear()
            await self.cmd_start(message)
        else:
            await message.answer(
                "❌ <b>Неверный пароль!</b>\n"
                "Попробуйте снова или обратитесь к администратору.",
                parse_mode="HTML",
            )

    async def _send_main_menu(self, message: Message, uid: int) -> None:
        text = (
            "<b>🏠 GTA5RP · Мониторинг недвижимости</b>\n"
            "Слежу за квартирами, домами и слётами.\n\n"
            "Всё управление — через кнопки ниже 👇\n"
            "<i>Открыть меню в любой момент: /menu</i>"
        )
        await message.answer(text, parse_mode="HTML", reply_markup=self._get_keyboard(uid))

    async def _get_user_sid(self, user_id: int) -> Optional[str]:
        """A user's chosen active server sid, or None if they haven't picked one."""
        from app.database.repository import UserServerSelectionRepository

        async with DatabaseSession.get_session_context() as session:
            repo = UserServerSelectionRepository(session)
            return await repo.get(user_id)

    async def _set_user_sid(self, user_id: int, sid: str) -> None:
        """Persist a user's active server selection (upsert)."""
        from app.database.repository import UserServerSelectionRepository

        async with DatabaseSession.get_session_context() as session:
            repo = UserServerSelectionRepository(session)
            await repo.set(user_id, sid)

    async def _default_sid_for_user(self, user_id: int) -> tuple:
        """Resolve the server a catalog command should default to for this user.

        Prefers the user's saved selection. Since /start now lets a user pick any
        server from the full wiki list (not just the pre-configured ones), we
        honour any valid wiki sid — the scheduler picks it up and starts filling
        its catalog. Falls back to the configured primary REALESTATE_SERVER when
        the user hasn't chosen yet. Returns (sid, server_name).
        """
        from app.scraper.realestate_client import (
            sid_to_server_name, server_name_to_sid,
        )

        sid = await self._get_user_sid(user_id)
        if sid:
            name = sid_to_server_name(sid)
            if name:
                return sid, name

        name = self.settings.realestate.server_name
        return server_name_to_sid(name), name

    async def _auth_middleware(self, handler, event, data) -> None:
        """Middleware: block messages/callbacks from unauthorized users.

        Skips the check for /start, /password, and messages in the login FSM
        state so the auth flow itself works. Every other command and callback
        requires a valid password or admin status.
        """
        from aiogram.types import Message as TgMsg, CallbackQuery as TgCb

        if isinstance(event, TgMsg):
            text = (event.text or "").strip()
            if text in ("/start", "/password"):
                return await handler(event, data)
            state = data.get("state")
            if state and await state.get_state() == BotStates.login:
                return await handler(event, data)
            if not self._check_auth(event.from_user.id):
                await event.answer(
                    "🔑 <b>Требуется авторизация</b>\n"
                    "Введите пароль: /password или /start",
                    parse_mode="HTML",
                )
                return

        elif isinstance(event, TgCb):
            if not self._check_auth(event.from_user.id):
                await event.answer("🔑 Сначала авторизуйтесь: /start", show_alert=True)
                return

        return await handler(event, data)

    async def cmd_help(self, message: Message, user_id: Optional[int] = None) -> None:
        text = (
            "📖 <b>Помощь</b>\n\n"
            "Бот управляется кнопками — нажмите /menu, чтобы открыть меню.\n"
            "Разделы: 🏠 Квартиры · 🏢 Каталог · 🔔 Подписки · 🕐 Отчёты · ⚙️ Админ.\n\n"
            "<b>Команды тоже работают:</b>\n"
            "  /list /free /occupied /stats — квартиры\n"
            "  /search <i>текст</i> · /status <i>id</i> · /history <i>id</i>\n"
            "  /realestate — каталог освобождений\n"
            "  /buildings · /building <i>название</i> — здания и квартиры\n"
            "  /houses · /owners <i>ник</i> · /owner_history <i>ключ</i>\n"
            "  /report [on|off] · /last_data — отчёты\n"
            "  /servers · /subscribe <i>сервер</i> [house|apartment]\n"
            "  /unsubscribe <i>сервер</i> · /subscriptions — подписки\n"
            "  /crashday · /last_update\n\n"
            "<i>Мультисервер: /houses Strawberry, /buildings @Sunrise</i>"
        )
        await message.answer(text, parse_mode="HTML", reply_markup=self._back_kb("main"))

    async def cmd_list(self, message: Message, user_id: Optional[int] = None) -> None:
        """List apartment buildings (free/total) for the user's selected server.

        Sourced from the per-server `/realestate` catalog, so the list follows
        whichever server the user picked at /start — not the single map server.
        """
        uid = user_id or message.from_user.id
        sid, server_name = await self._default_sid_for_user(uid)
        async with DatabaseSession.get_session_context() as session:
            repo = RealEstateRepository(session)
            buildings = await repo.get_buildings(sid)

        if not buildings:
            await message.answer(
                f"🏠 Нет данных о квартирах для сервера {server_name}. "
                "Дождитесь первого сканирования каталога.",
                reply_markup=self._back_kb("apartments"),
            )
            return

        total_free = sum((b.free_count or 0) for b in buildings)
        total_units = sum((b.apartments_count or 0) for b in buildings)
        total_occupied = max(total_units - total_free, 0)
        free_count = sum(1 for b in buildings if b.free_count and b.free_count > 0)
        total_count = len(buildings)
        lines = [
            f"<b>🏠 Все квартиры · {server_name}</b>\n",
            f"🟢 Свободно: {total_free} | 🔴 Занято: {total_occupied} | 📦 Всего: {total_units}\n",
            f"┃ 🏠 Зданий со свободными: {free_count}/{total_count}\n",
        ]
        for i, b in enumerate(buildings, 1):
            free = b.free_count or 0
            total = b.apartments_count or 0
            icon = "🟢" if free > 0 else "🔴"
            status = "свободно" if free > 0 else "занято"
            lines.append(f"{icon} {i:02d}. {b.name} — {status} | {free}/{total}")

        await self._reply_chunked(message, lines[1:], lines[0] + "\n",
                                  footer_kb=self._back_kb("apartments"))

    async def cmd_status(self, message: Message) -> None:
        args = message.text.replace("/status", "").strip()
        await self._render_status(message, args)

    async def _render_status(self, message: Message, args: str) -> None:
        if not args:
            await message.answer("Укажите название здания. Пример: /status Eclipse Towers")
            return

        # Resolve a building in the per-server catalog for the user's active
        # server (partial name match), so status follows the picked server
        # instead of the global map-scraper table.
        from app.database.repository import RealEstateRepository
        uid = message.from_user.id
        sid, server_name = await self._default_sid_for_user(uid)

        async with DatabaseSession.get_session_context() as session:
            repo = RealEstateRepository(session)
            buildings = await repo.get_buildings(sid) if sid else []

        q = args.lower()
        building = next((b for b in buildings if q in (b.name or "").lower()), None)

        if not building:
            await message.answer(
                f"🏢 Здание «{args}» не найдено на сервере {server_name} "
                f"(список: /buildings).",
                reply_markup=self._back_kb("apartments"),
            )
            return

        free = building.free_count or 0
        total = building.apartments_count if building.apartments_count is not None else 0
        occupied = max(total - free, 0)
        bar_len = 12
        free_blocks = round(free / max(total, 1) * bar_len) if total > 0 else 0
        bar = "🟩" * free_blocks + "⬛" * (bar_len - free_blocks)

        text = (
            f"<b>🏢 {building.name} · {server_name}</b>\n"
            f"┣ {bar}\n"
            f"┣ 🟢 Свободно: {free}  |  🔴 Занято: {occupied}\n"
            f"┗ 📊 Всего: {total}\n\n"
            f"<i>Владельцы занятых квартир: /building {building.name}</i>"
        )
        if building.updated_at:
            text += f"\n🕐 {building.updated_at.strftime('%d.%m.%Y %H:%M:%S')}"

        await message.answer(text, parse_mode="HTML", reply_markup=self._back_kb("apartments"))

    async def cmd_free(self, message: Message, user_id: Optional[int] = None) -> None:
        """Buildings with free apartments, on the user's selected server."""
        uid = user_id or message.from_user.id
        sid, server_name = await self._default_sid_for_user(uid)
        from app.database.repository import RealEstateRepository

        async with DatabaseSession.get_session_context() as session:
            repo = RealEstateRepository(session)
            buildings = await repo.get_buildings(sid)

        free_bld = [b for b in buildings if (b.free_count or 0) > 0]
        if not free_bld:
            await message.answer(
                f"😢 На сервере {server_name} нет зданий со свободными квартирами."
                if buildings else
                f"🏢 Данных по {server_name} пока нет. Дождитесь первого сканирования.",
                reply_markup=self._back_kb("apartments"),
            )
            return

        total_free = sum(b.free_count or 0 for b in free_bld)
        lines = [f"<b>🟢 Свободные квартиры · {server_name}</b>\nВсего свободно: {total_free}\n"]
        for i, b in enumerate(sorted(free_bld, key=lambda x: -(x.free_count or 0)), 1):
            total = b.apartments_count if b.apartments_count is not None else "?"
            lines.append(f"🟢 {i:02d}. <b>{b.name}</b> — свободно {b.free_count}/{total}")
        lines.append("\n<i>Владельцы занятых: /building &lt;название&gt;</i>")
        await self._reply_chunked(message, lines[1:], lines[0] + "\n",
                                  footer_kb=self._back_kb("apartments"))

    async def cmd_occupied(self, message: Message, user_id: Optional[int] = None) -> None:
        """Fully-occupied buildings (no free apartments) on the selected server."""
        uid = user_id or message.from_user.id
        sid, server_name = await self._default_sid_for_user(uid)
        from app.database.repository import RealEstateRepository

        async with DatabaseSession.get_session_context() as session:
            repo = RealEstateRepository(session)
            buildings = await repo.get_buildings(sid)

        occupied = [b for b in buildings if (b.free_count or 0) == 0 and (b.apartments_count or 0) > 0]
        if not occupied:
            await message.answer(
                f"🎉 На сервере {server_name} везде есть свободные квартиры!"
                if buildings else
                f"🏢 Данных по {server_name} пока нет. Дождитесь первого сканирования.",
                reply_markup=self._back_kb("apartments"),
            )
            return

        lines = [f"<b>🔴 Полностью занятые · {server_name}</b>\n"]
        for i, b in enumerate(sorted(occupied, key=lambda x: x.name or ""), 1):
            total = b.apartments_count or 0
            lines.append(f"🔴 {i:02d}. {b.name} — {total}/{total}")
        await self._reply_chunked(message, lines[1:], lines[0] + "\n",
                                  footer_kb=self._back_kb("apartments"))

    async def cmd_history(self, message: Message) -> None:
        args = message.text.replace("/history", "").strip()
        await self._render_history(message, args)

    async def _render_history(self, message: Message, args: str) -> None:
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
                    await message.answer(f"Нет изменений для квартиры #{args}.",
                                         reply_markup=self._back_kb("apartments"))
                    return

                lines = [f"<b>📋 История · кв. #{args}</b>\n"]
                for c in changes:
                    t = c.detected_at.strftime("%d.%m %H:%M") if c.detected_at else "?"
                    lines.append(f"[{t}] {c.field_name}: {c.old_value or '—'} → {c.new_value}")
                await self._reply_chunked(message, lines[1:], lines[0] + "\n",
                                          footer_kb=self._back_kb("apartments"))
            else:
                changes = await change_repo.get_recent(limit=10)
                if not changes:
                    await message.answer("Изменений пока нет.",
                                         reply_markup=self._back_kb("apartments"))
                    return

                lines = ["<b>📋 Последние изменения</b>\n"]
                for c in changes:
                    t = c.detected_at.strftime("%d.%m %H:%M") if c.detected_at else "?"
                    name = c.apartment.name if c.apartment else "?"
                    lines.append(f"[{t}] {name}: {c.field_name}")
                await self._reply_chunked(message, lines[1:], lines[0] + "\n",
                                          footer_kb=self._back_kb("apartments"))

    async def cmd_last_update(self, message: Message, user_id: Optional[int] = None) -> None:
        uid = user_id or message.from_user.id
        async with DatabaseSession.get_session_context() as session:
            repo = ScraperLogRepository(session)
            stats = await repo.get_statistics()

        if not stats["last_run"]:
            await message.answer("Парсер ещё не запускался.", reply_markup=self._back_kb("main"))
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

        await message.answer(text, parse_mode="HTML", reply_markup=self._back_kb("main"))

    async def cmd_stats(self, message: Message, user_id: Optional[int] = None) -> None:
        """Apartment statistics for the user's active server (from the catalog)."""
        uid = user_id or message.from_user.id
        sid, server_name = await self._default_sid_for_user(uid)

        from app.database.repository import RealEstateRepository, ScraperLogRepository
        async with DatabaseSession.get_session_context() as session:
            re_repo = RealEstateRepository(session)
            buildings = await re_repo.get_buildings(sid)
            occupied_houses = await re_repo.count_occupied(sid, kind="house")
            log_repo = ScraperLogRepository(session)
            log_stats = await log_repo.get_statistics()

        # Apartment totals come from the per-building free/total counts; houses
        # are counted from occupied catalog objects (the catalog lists only
        # occupied ones, so "free houses" isn't derivable — we show occupied).
        apt_total = sum((b.apartments_count or 0) for b in buildings)
        apt_free = sum((b.free_count or 0) for b in buildings)
        apt_occupied = apt_total - apt_free
        buildings_with_free = sum(1 for b in buildings if (b.free_count or 0) > 0)

        if not buildings and occupied_houses == 0:
            await message.answer(
                f"📊 <b>Статистика · {server_name}</b>\n"
                "Данных пока нет — дождитесь первого сканирования этого сервера.",
                parse_mode="HTML", reply_markup=self._back_kb("apartments"),
            )
            return

        bar_len = 10
        free_pct = apt_free / max(apt_total, 1)
        occ_blocks = round((1 - free_pct) * bar_len)
        bar = "🟩" * (bar_len - occ_blocks) + "⬛" * occ_blocks
        occupancy = round((1 - free_pct) * 100, 1)

        text = (
            f"📊 <b>Статистика · {server_name}</b>\n"
            f"┏━━━━━━━━━━━━━━━\n"
            f"┃ {bar}\n"
            f"┃ 🏢 Квартиры: 🟢 {apt_free} своб.  🔴 {apt_occupied} зан.  📦 {apt_total} всего\n"
            f"┃ 🏠 Зданий: {len(buildings)} (со свободными: {buildings_with_free})\n"
            f"┃ 🏠 Занятых домов: {occupied_houses}\n"
            f"┃ 📈 Заполненность квартир: {occupancy}%\n"
            f"┗━━━━━━━━━━━━━━━\n\n"
            f"<b>Парсер</b>\n"
            f"┣ ✅ Успешно: {log_stats['successful_runs']}\n"
            f"┣ ❌ Ошибок: {log_stats['failed_runs']}\n"
            f"┗ 📈 Успешность: {log_stats['success_rate']}%"
        )
        await message.answer(text, parse_mode="HTML", reply_markup=self._back_kb("apartments"))

    async def cmd_search(self, message: Message) -> None:
        query = message.text.replace("/search", "").strip()
        await self._render_search(message, query)

    async def _render_search(self, message: Message, query: str) -> None:
        if not query:
            await message.answer("🔍 Укажите запрос. Пример: /search Сан Винсент")
            return

        # Search the per-server catalog for the user's active server: match
        # apartment buildings by name (with their live free/total counts). This
        # replaces the old global map-scraper table so results follow the server
        # the user picked at /start.
        from app.database.repository import RealEstateRepository
        uid = message.from_user.id
        sid, server_name = await self._default_sid_for_user(uid)

        async with DatabaseSession.get_session_context() as session:
            repo = RealEstateRepository(session)
            buildings = await repo.get_buildings(sid) if sid else []

        q = query.lower()
        results = [b for b in buildings if q in (b.name or "").lower()]

        if not results:
            await message.answer(
                f"🔍 По запросу «{query}» на сервере {server_name} ничего не найдено.",
                reply_markup=self._back_kb("apartments"),
            )
            return

        lines = [f"<b>🔍 Результаты: {query} · {server_name}</b>\n"]
        for b in results:
            free = b.free_count or 0
            total = b.apartments_count if b.apartments_count is not None else 0
            icon = "🟢" if free > 0 else "🔴"
            lines.append(f"{icon} {b.name} — свободно {free}/{total}")
        lines.append("\n<i>Владельцы квартир: /building &lt;название&gt;</i>")
        await self._reply_chunked(message, lines[1:], lines[0] + "\n",
                                  footer_kb=self._back_kb("apartments"))

    async def cmd_scrape(self, message: Message, user_id: Optional[int] = None) -> None:
        if not self._is_admin(user_id or message.from_user.id):
            return
        if not self.scheduler:
            await message.answer("❌ Парсер не запущен.", reply_markup=self._back_kb("admin"))
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

    async def cmd_crash_status(self, message: Message, user_id: Optional[int] = None) -> None:
        if not self._is_admin(user_id or message.from_user.id):
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
        await message.answer(text, parse_mode="HTML", reply_markup=self._back_kb("admin"))

    async def cmd_crash_on(self, message: Message, user_id: Optional[int] = None) -> None:
        if not self._is_admin(user_id or message.from_user.id):
            return
        from app.scraper.crash_detector import get_crash_detector
        await get_crash_detector().set_enabled(True)
        await message.answer("✅ Краш-детектор <b>включён</b>", parse_mode="HTML",
                             reply_markup=self._back_kb("admin"))

    async def cmd_crash_off(self, message: Message, user_id: Optional[int] = None) -> None:
        if not self._is_admin(user_id or message.from_user.id):
            return
        from app.scraper.crash_detector import get_crash_detector
        await get_crash_detector().set_enabled(False)
        await message.answer("🔴 Краш-детектор <b>выключен</b>", parse_mode="HTML",
                             reply_markup=self._back_kb("admin"))

    async def cmd_crashday(self, message: Message, user_id: Optional[int] = None) -> None:
        uid = user_id or message.from_user.id
        from datetime import datetime, timezone, timedelta
        today = datetime.now(timezone.utc)
        since = today.replace(hour=0, minute=0, second=0, microsecond=0)
        async with DatabaseSession.get_session_context() as session:
            from app.database.repository import RealEstateRepository, SubscriptionRepository
            from app.scraper.realestate_client import sid_to_server_name
            repo = RealEstateRepository(session)
            events = await repo.get_events_since(since, event_types=["freed", "converted"])

            sub_repo = SubscriptionRepository(session)
            subs = await sub_repo.list_for_user(uid)
            subscribed_sids = {s.server_sid for s in subs}
            if subscribed_sids:
                events = [e for e in events if e.server_sid in subscribed_sids]

        if not events:
            await message.answer("📉 Слётов за сегодня не было.", parse_mode="HTML",
                                 reply_markup=self._back_kb("main"))
            return

        freed_houses = [e for e in events if e.event_type == "freed" and e.kind == "house"]
        freed_apts = [e for e in events if e.event_type == "freed" and e.kind == "apartment"]
        converted = [e for e in events if e.event_type == "converted"]

        date_str = today.strftime("%d.%m.%Y")
        lines = [f"<b>📉 Слёты за {date_str}</b>\n"]
        lines.append(f"🏠 Слетело домов: <b>{len(freed_houses)}</b>")
        lines.append(f"🏢 Слетело квартир: <b>{len(freed_apts)}</b>")
        if converted:
            lines.append(f"🏛 Конвертировано в особняк: <b>{len(converted)}</b>")
        lines.append("")

        if freed_houses:
            lines.append("<b>Дома:</b>")
            for e in freed_houses:
                server = sid_to_server_name(e.server_sid) if e.server_sid else "?"
                name = e.name or f"#{e.object_key.split(':')[-1]}"
                t = e.detected_at.strftime("%H:%M") if e.detected_at else "?"
                cls = f" ({e.class_name})" if e.class_name else ""
                owner = f" · {e.old_owner}" if e.old_owner else ""
                lines.append(f"  🏠 [{server}] {name}{cls}{owner} — {t}")
            lines.append("")

        if freed_apts:
            lines.append("<b>Квартиры:</b>")
            for e in freed_apts:
                server = sid_to_server_name(e.server_sid) if e.server_sid else "?"
                name = e.name or f"#{e.object_key.split(':')[-1]}"
                t = e.detected_at.strftime("%H:%M") if e.detected_at else "?"
                bld = f" · {e.building_name}" if e.building_name else ""
                owner = f" · {e.old_owner}" if e.old_owner else ""
                lines.append(f"  🏢 [{server}] {name}{bld}{owner} — {t}")
            lines.append("")

        await self._reply_chunked(message, lines[1:], lines[0] + "\n",
                                  footer_kb=self._back_kb("main"))

    async def cmd_both_owners(self, message: Message, user_id: Optional[int] = None) -> None:
        """Show owners who have both a house and an apartment (on the default server)."""
        uid = user_id or message.from_user.id
        d_sid, d_name = await self._default_sid_for_user(uid)
        if not d_sid:
            await message.answer("⚠️ Сначала выберите сервер.")
            return
        from app.database.repository import RealEstateRepository
        from sqlalchemy import select, func
        from app.database.models import RealEstateObject
        from app.scraper.realestate_client import sid_to_server_name

        server_name = d_name or sid_to_server_name(d_sid) or f"sid {d_sid}"

        async with DatabaseSession.get_session_context() as session:
            repo = RealEstateRepository(session)
            result = await session.execute(
                select(
                    RealEstateObject.owner_name,
                    func.count().filter(RealEstateObject.kind == "house").label("houses"),
                    func.count().filter(RealEstateObject.kind == "apartment").label("apts"),
                )
                .where(
                    RealEstateObject.server_sid == d_sid,
                    RealEstateObject.is_occupied == True,
                    RealEstateObject.owner_name.isnot(None),
                )
                .group_by(RealEstateObject.owner_name)
                .having(
                    func.count().filter(RealEstateObject.kind == "house") >= 1,
                    func.count().filter(RealEstateObject.kind == "apartment") >= 1,
                )
                .order_by(RealEstateObject.owner_name)
            )
            rows = result.all()
        if not rows:
            await message.answer("Нет игроков с домом и квартирой одновременно.",
                                 reply_markup=self._back_kb("catalog"))
            return
        lines = [f"<b>🏠+🏢 Игроки с домом и квартирой · {server_name} ({len(rows)})</b>"]
        for r in rows:
            lines.append(f"• {r.owner_name} — 🏠 {r.houses} · 🏢 {r.apts}")
        await self._reply_chunked(message, lines[1:], lines[0] + "\n",
                                  footer_kb=self._back_kb("catalog"))

    async def cmd_map_check(self, message: Message, user_id: Optional[int] = None) -> None:
        if not self._is_admin(user_id or message.from_user.id):
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
        await message.answer(text, parse_mode="HTML", reply_markup=self._back_kb("admin"))


async def send_notification(
    bot: Bot,
    chat_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> Optional[Message]:
    """
    Send a notification to a specific user.

    Args:
        bot: Bot instance.
        chat_id: Telegram user ID.
        text: Message text.
        reply_markup: Optional inline keyboard.

    Returns:
        The sent Message if successful, None otherwise.
    """
    try:
        msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
        return msg
    except TelegramForbiddenError:
        logger.warning(f"User {chat_id} blocked the bot")
        return None
    except TelegramRetryAfter as e:
        logger.warning(f"Rate limited, waiting {e.retry_after}s")
        await asyncio.sleep(e.retry_after)
        return await send_notification(bot, chat_id, text)
    except Exception as e:
        logger.error(f"Failed to send notification to {chat_id}: {e}")
        return None


async def run_bot(scheduler: Optional[SmartScheduler] = None) -> None:
    """Run the Telegram bot."""
    bot = ApartmentBot(scheduler)
    await bot.start()