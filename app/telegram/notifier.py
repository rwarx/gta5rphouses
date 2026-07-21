"""
Change notification system for Telegram.
Monitors detected changes and sends notifications to admin users.
"""

import asyncio
from typing import Optional, List
from datetime import datetime, timezone, timedelta

from loguru import logger
from aiogram import Bot

from app.config import get_settings
from app.database.session import DatabaseSession
from app.database.repository import (
    ChangeRepository,
    NotificationRepository,
    ScraperSettingsRepository,
    RealEstateRepository,
    SubscriptionRepository,
)
from app.scraper.realestate_client import sid_to_server_name, server_name_to_sid
from app.telegram.bot import send_notification


class ChangeNotifier:
    """
    Monitors for unsent change notifications and delivers them to Telegram users.
    Runs as a background task to ensure timely delivery of alerts.
    """

    # How long the Payday report waits for the browser map scrape to catch up to
    # a catalog recompute before firing anyway. The catalog advances first and
    # the map scrape lands seconds-to-minutes later; this bounds that wait so a
    # broken/disabled scraper can never swallow a Payday summary forever.
    _MAP_WAIT_GRACE = timedelta(minutes=10)

    def __init__(self, bot: Bot):
        self.bot = bot
        self.settings = get_settings()
        self._running = False
        # UTC hour (YYYY-MM-DD-HH) for which the hourly report was last sent, so
        # we emit exactly one report per clock hour.
        self._last_report_hour: Optional[str] = None
        # UTC time this notifier started; the per-Payday report only considers
        # events at/after this, so a restart never re-reports an old Payday.
        self._started_at: Optional[datetime] = None
        # When we first saw a still-pending catalog recompute per server, keyed
        # by "<sid>:<marker>". Drives the bounded grace period in _map_caught_up
        # so a never-arriving map scrape still lets the report out eventually.
        self._map_wait_since: dict = {}

    def _in_payday_window(self) -> bool:
        """Whether the current minute falls inside the configured Payday window.

        During this window the catalog churns (objects blip out and back in as
        the wiki recomputes), so instant per-event pings and the wall-clock
        hourly report are unreliable — they can fire before the map has fully
        settled. We suppress both here and let the single, recompute-gated
        Payday report (`_maybe_send_payday_report`) be the source of truth once
        the catalog's `fetchedAtMs` has actually advanced.
        """
        smart = self.settings.smart_mode
        if not smart.smart_mode:
            return False
        minute = datetime.now(timezone.utc).minute
        start, end = smart.payday_start_minute, smart.payday_end_minute
        return (
            start <= minute <= end if start <= end
            else (minute >= start or minute <= end)
        )

    async def _recipients_for_server(
        self, session, server_sid: Optional[str], kind: Optional[str] = None
    ) -> List[int]:
        """Resolve who should receive an alert for a server.

        Users subscribed to the server (and matching kind) get it; if the server
        has no subscribers we fall back to the globally allowed users so alerts
        are never silently dropped. When no sid is known we also fall back.
        """
        allowed = list(self.settings.telegram.allowed_users)
        if not server_sid:
            return allowed

        sub_repo = SubscriptionRepository(session)
        subs = await sub_repo.get_subscribers(server_sid, kind=kind)
        subscriber_ids = [s.user_id for s in subs]
        if subscriber_ids:
            # De-duplicate while preserving order.
            seen = set()
            unique = []
            for uid in subscriber_ids:
                if uid not in seen:
                    seen.add(uid)
                    unique.append(uid)
            return unique
        return allowed

    async def start(self) -> None:
        """Start the notification monitoring loop."""
        if not self.settings.telegram.bot_token:
            logger.warning("BOT_TOKEN not set, notifications disabled")
            return

        self._running = True
        self._started_at = datetime.now(timezone.utc)
        logger.info("Change notifier started")

        while self._running:
            try:
                await self._process_pending_notifications()
                await self._maybe_send_hourly_report()
                await self._maybe_send_payday_report()
                await asyncio.sleep(5)  # Check every 5 seconds
            except Exception as e:
                logger.error(f"Notification loop error: {e}")
                await asyncio.sleep(10)

    async def _maybe_send_hourly_report(self) -> None:
        """Once per clock hour, send a single compact Payday summary.

        Instead of spamming one message per catalog change during Payday, we
        roll the last hour of events into one report: how many houses/apartments
        freed, and how many owner-nickname changes (possible silent free-ups)
        were seen.
        """
        now = datetime.now(timezone.utc)
        hour_key = now.strftime("%Y-%m-%d-%H")
        if self._last_report_hour == hour_key:
            return

        # Don't fire mid-Payday: the catalog is still churning and the map may
        # not have refreshed yet. Defer (without latching the hour) so the report
        # goes out once the window passes; the recompute-gated Payday report is
        # the authoritative summary for the window itself.
        if self._in_payday_window():
            return

        # On the very first loop iteration just latch the current hour; don't
        # fire a report immediately on startup.
        if self._last_report_hour is None:
            self._last_report_hour = hour_key
            return

        async with DatabaseSession.get_session_context() as session:
            settings_repo = ScraperSettingsRepository(session)
            enabled = await settings_repo.get("hourly_report")
            if enabled is None:
                await settings_repo.set("hourly_report", "1")
                enabled = "1"
            if enabled != "1":
                self._last_report_hour = hour_key
                return

            realestate_repo = RealEstateRepository(session)
            since = now - timedelta(hours=1)
            events = await realestate_repo.get_events_since(since)

            # Group the hour's events by server so each server's subscribers get
            # a report scoped to their server. Servers with no events this hour
            # still get a short "no changes" report if someone is subscribed.
            monitored = self.settings.realestate.server_names
            monitored_sids = [
                sid for sid in (
                    server_name_to_sid(n) for n in monitored
                ) if sid
            ]
            by_server: dict = {sid: [] for sid in monitored_sids}
            for e in events:
                by_server.setdefault(e.server_sid, []).append(e)

            self._last_report_hour = hour_key
            for sid, server_events in by_server.items():
                recipients = await self._recipients_for_server(session, sid)
                if not recipients:
                    continue
                message = self._build_hourly_report(server_events, since, now, sid)
                for user_id in recipients:
                    await send_notification(self.bot, user_id, message)

    def _build_hourly_report(self, events, since, now, server_sid=None) -> str:
        """Render the last hour of catalog events as one compact report."""
        freed_houses = [e for e in events if e.event_type == "freed" and e.kind == "house"]
        freed_apts = [e for e in events if e.event_type == "freed" and e.kind == "apartment"]
        possibly = [e for e in events if e.event_type == "possibly_freed"]

        period = f"{since.strftime('%H:%M')}–{now.strftime('%H:%M')} UTC"
        server = sid_to_server_name(server_sid) if server_sid else None
        header = "🕐 <b>Почасовой отчёт (пейдей)</b>"
        if server:
            header = f"🕐 <b>Почасовой отчёт (пейдей) — {server}</b>"
        lines = [
            header,
            f"<i>{period}</i>",
            "━━━━━━━━━━━━━━━",
            f"🏠 Слетело домов: <b>{len(freed_houses)}</b>",
            f"🏢 Слетело квартир: <b>{len(freed_apts)}</b>",
            f"🔄 Смен ников (возможные слёты): <b>{len(possibly)}</b>",
        ]

        if not events:
            lines.append("\n<i>За этот час изменений не было.</i>")
            return "\n".join(lines)

        if possibly:
            lines.append("\n<b>Возможные слёты:</b>")
            for e in possibly:
                kind_ru = "дом" if e.kind == "house" else "кв."
                name = e.name or e.object_key
                lines.append(f"• {kind_ru} {name}: {e.old_owner or '—'} → {e.new_owner or '—'}")

        return "\n".join(lines)

    async def _map_caught_up(self, settings_repo, sid: str, catalog_marker: str) -> bool:
        """Whether the browser map scrape has caught up to this catalog recompute.

        Returns True once `map_scrape_done:<sid>` (the fetchedAtMs the map was
        last scraped for) is >= the catalog recompute marker — i.e. the map has
        refreshed for this Payday. Returns True immediately if map-gating cannot
        apply (no completion marker was ever written, e.g. the map scraper /
        MAP_UPDATE_GATE is disabled), so single-source deployments still report.

        Safety valve: if the map marker exists but lags, we hold the report — but
        only up to `_MAP_WAIT_GRACE`. If the browser scrape is stuck/broken and
        never catches up, we release the report after the grace period so a
        Payday summary is never lost. The first-seen time per (sid, marker) is
        tracked in-memory; a restart just resets the clock, which is harmless.
        """
        done = await settings_repo.get(f"map_scrape_done:{sid}")

        # No completion marker has ever been written for this server → the map
        # scraper isn't correlating fetchedAtMs (gate off or map disabled). Don't
        # gate; fall back to reporting on the catalog recompute alone.
        if done is None:
            return True

        def _as_int(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        done_i, cat_i = _as_int(done), _as_int(catalog_marker)
        # If either marker isn't a comparable integer, don't block the report.
        if done_i is None or cat_i is None:
            return True

        # Grace timer is keyed per (sid, marker): a new recompute while we're
        # still waiting gets its own fresh clock rather than inheriting the old
        # one. Stale keys for this sid (older markers) are dropped as we go.
        wait_key = f"{sid}:{catalog_marker}"

        if done_i >= cat_i:
            self._map_wait_since.pop(wait_key, None)
            return True

        # Map still behind. Start / check the grace timer for this recompute.
        now = datetime.now(timezone.utc)
        first_seen = self._map_wait_since.get(wait_key)
        if first_seen is None:
            self._map_wait_since[wait_key] = now
            logger.info(
                f"Payday report for sid {sid} held: waiting for map scrape "
                f"(catalog={cat_i}, map={done_i})"
            )
            return False
        if (now - first_seen) >= self._MAP_WAIT_GRACE:
            logger.warning(
                f"Payday report for sid {sid} released after grace period — map "
                f"scrape never caught up (catalog={cat_i}, map={done_i}); "
                f"reporting anyway"
            )
            self._map_wait_since.pop(wait_key, None)
            return True
        return False

    async def _maybe_send_payday_report(self) -> None:
        """Emit one report per server per Payday map update.

        Each time the wiki recomputes the catalog (a "map update", which is when
        Payday churn settles) the realestate scheduler bumps the persisted
        `catalog_recompute:<sid>` marker. When that marker advances past the one
        we last reported on, we summarise everything that freed this Payday —
        houses *and* apartments — and send it to that server's gov-notification
        recipients. If nothing freed, we still send a short "<date>: за этот
        пейдей ничего не слетело" so subscribers know the update was processed.

        Gated behind the `payday_report` setting (on by default). The catalog's
        fetchedAtMs only advances when the wiki recomputes (around Payday), so
        firing on each advance is effectively one report per Payday.

        The report is additionally held until the browser MAP scrape for the
        same recompute has finished (`map_scrape_done:<sid>` >= the catalog
        marker), so it never goes out before the map itself has refreshed. See
        `_map_caught_up` for the bounded grace fallback.
        """
        async with DatabaseSession.get_session_context() as session:
            settings_repo = ScraperSettingsRepository(session)
            enabled = await settings_repo.get("payday_report")
            if enabled is None:
                await settings_repo.set("payday_report", "1")
                enabled = "1"
            if enabled != "1":
                return

            # Only report on freed-object notifications when gov-notifications
            # are on; the map-update marker is still latched below regardless.
            gov_on = await settings_repo.get("notify_free_found")
            if gov_on is None:
                await settings_repo.set("notify_free_found", "1")
                gov_on = "1"

            realestate_repo = RealEstateRepository(session)
            monitored = self.settings.realestate.server_names
            monitored_sids = [
                sid for sid in (server_name_to_sid(n) for n in monitored) if sid
            ]

            for sid in monitored_sids:
                marker = await settings_repo.get(f"catalog_recompute:{sid}")
                if marker is None:
                    continue  # scheduler hasn't diffed this server yet

                reported_key = f"payday_report_marker:{sid}"
                since_key = f"payday_report_at:{sid}"
                last_reported = await settings_repo.get(reported_key)
                if last_reported == marker:
                    continue  # already reported this recompute

                # Wait for the MAP to actually refresh before reporting. The
                # catalog recompute (`catalog_recompute:<sid>`) only means the
                # HTTP catalog advanced; the browser map scrape runs in its own
                # loop and lands a moment later. Firing now would send the
                # gov-report BEFORE the map updated — exactly the bug we're
                # fixing. The map scraper stamps `map_scrape_done:<sid>` with the
                # fetchedAtMs it scraped for, so we hold the report until that
                # marker has caught up to (>=) this recompute. A bounded grace
                # period is the safety valve: if the browser scrape is broken or
                # disabled and never catches up, we still emit the report after
                # `_MAP_WAIT_GRACE` so the Payday summary is never lost.
                if not await self._map_caught_up(settings_repo, sid, marker):
                    continue

                now = datetime.now(timezone.utc)
                # Window to summarise: everything freed since the previous report
                # (or since startup on the first run), so consecutive Paydays
                # never double-count each other's freed objects.
                prev_at = await settings_repo.get(since_key)
                if prev_at:
                    try:
                        since = datetime.fromisoformat(prev_at)
                    except ValueError:
                        since = self._started_at or (now - timedelta(hours=6))
                else:
                    since = self._started_at or (now - timedelta(hours=6))

                # detected_at is stored as naive, whole-second UTC (func.now()),
                # while a bound param renders with sub-second precision — so an
                # exact boundary drops events landing in the same second. Drop
                # the tz and back off one second; the overlap is harmless given
                # a Payday window spans minutes.
                since = since.replace(tzinfo=None) - timedelta(seconds=1)

                # Latch marker + window first so a delivery failure can't spam
                # the report on the next 5s loop iteration. Floor to whole
                # seconds: detected_at (func.now()) has no sub-second precision,
                # so a microsecond-precise boundary would drop same-second events.
                await settings_repo.set(reported_key, marker)
                await settings_repo.set(
                    since_key, now.replace(microsecond=0).isoformat()
                )

                if last_reported is None:
                    # First time we see this server (e.g. right after startup):
                    # adopt the current marker without reporting a Payday we may
                    # only have partially observed.
                    continue

                if gov_on != "1":
                    continue

                events = await realestate_repo.get_events_since(
                    since, event_types=["freed"], server_sid=sid
                )
                recipients = await self._recipients_for_server(session, sid)
                if not recipients:
                    continue

                message = self._build_payday_report(events, sid)
                for user_id in recipients:
                    await send_notification(self.bot, user_id, message)

    def _build_payday_report(self, events, server_sid=None) -> str:
        """Render the freed houses + apartments of one Payday as a report.

        `events` are the "freed" RealEstateEvents for this server since the last
        report. When empty, returns the "nothing freed this Payday" message.
        """
        now = datetime.now(timezone.utc)
        date = now.strftime("%d.%m.%Y")
        server = sid_to_server_name(server_sid) if server_sid else None
        suffix = f" — {server}" if server else ""

        freed_houses = [e for e in events if e.kind == "house"]
        freed_apts = [e for e in events if e.kind == "apartment"]

        if not events:
            return (
                f"🏛 <b>Гос-отчёт за пейдей{suffix}</b>\n"
                f"<i>{date}</i>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"✅ {date}: за этот пейдей ничего не слетело."
            )

        lines = [
            f"🏛 <b>Гос-отчёт за пейдей{suffix}</b>",
            f"<i>{date}</i>",
            "━━━━━━━━━━━━━━━",
            f"🏠 Слетело домов: <b>{len(freed_houses)}</b>",
            f"🏢 Слетело квартир: <b>{len(freed_apts)}</b>",
        ]

        def _fmt(e) -> str:
            name = e.name or f"#{e.object_key.split(':')[-1]}"
            extra = f" ({e.class_name})" if e.class_name else ""
            if e.price:
                extra += f" · 💰 {e.price:,}".replace(",", " ")
            return f"• {name}{extra}"

        if freed_houses:
            lines.append("\n<b>🏠 Дома:</b>")
            lines.extend(_fmt(e) for e in freed_houses)
        if freed_apts:
            lines.append("\n<b>🏢 Квартиры:</b>")
            lines.extend(_fmt(e) for e in freed_apts)

        return "\n".join(lines)

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

            changes = await change_repo.get_unnotified()

            for change in changes:
                try:
                    # Only a real free-up ("apartment_freed") is worth an instant
                    # ping. Every other map-scraper field change (type counts,
                    # timestamps, occupied/free deltas) is noise that spams every
                    # Payday — swallow it silently; the hourly report covers trends.
                    if change.field_name != "apartment_freed":
                        await change_repo.mark_notified(change.id)
                        continue

                    if notify_free != "1":
                        await change_repo.mark_notified(change.id)
                        continue

                    if self._in_payday_window():
                        # Suppress instant map-scraper free-ups mid-Payday for the
                        # same reason as catalog events: the map is still settling.
                        # The recompute-gated Payday report is authoritative.
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

            # Deliver realestate catalog events: instant "freed" only; the rest
            # (possibly_freed / occupied / owner_changed) is folded into the
            # hourly Payday report instead of being sent one-by-one.
            await self._process_realestate_events(session, notify_free)

    async def _process_realestate_events(self, session, notify_free: str) -> None:
        """
        Deliver pending /realestate catalog events.

        Only a real "freed" (object vanished from the catalog -> available to buy)
        is sent instantly, since that's the time-critical "успей первым" signal.
        Everything else (possibly_freed / occupied / owner_changed) is marked
        notified without a per-event message — it is summarised in the hourly
        Payday report instead, so Payday churn no longer spams the chat.
        """
        realestate_repo = RealEstateRepository(session)
        events = await realestate_repo.get_unnotified_events()
        in_payday = self._in_payday_window()

        for event in events:
            try:
                if event.event_type != "freed":
                    # Folded into the hourly report; no instant message.
                    await realestate_repo.mark_event_notified(event.id)
                    continue

                if notify_free != "1":
                    await realestate_repo.mark_event_notified(event.id)
                    continue

                if in_payday:
                    # Inside the Payday window the catalog churns: objects blip
                    # out (looking "freed") and back in as the wiki recomputes,
                    # so an instant ping here is often a false alarm fired before
                    # the map settled. Swallow the instant message and let the
                    # recompute-gated Payday report — which counts freed events
                    # by detected_at once fetchedAtMs advances — be authoritative.
                    await realestate_repo.mark_event_notified(event.id)
                    continue

                message = self._build_realestate_message(event)
                recipients = await self._recipients_for_server(
                    session, event.server_sid, kind=event.kind
                )
                for user_id in recipients:
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
        server = sid_to_server_name(event.server_sid) if event.server_sid else None
        if event.event_type == "freed":
            parts.append(f"🎉 <b>Освободилось: {kind_ru.lower()}!</b>")
            parts.append("━━━━━━━━━━━━━━━")
            if server:
                parts.append(f"🌐 Сервер: {server}")
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