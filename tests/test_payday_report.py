"""
Tests for the per-Payday gov-report (слетевшие дома + квартиры за пейдей).

Covers the pure report builder (houses + apartments summary, and the
"nothing freed" fallback) and the end-to-end trigger in ChangeNotifier that
fires exactly one report per catalog recompute (map update).
"""

from datetime import datetime, timezone, timedelta

import pytest

from app.database.repository import (
    RealEstateRepository,
    ScraperSettingsRepository,
    SubscriptionRepository,
)
from app.telegram.notifier import ChangeNotifier


def _make_notifier(
    allowed_users,
    monitored_names,
    *,
    smart_mode=False,
    payday_start_minute=59,
    payday_end_minute=10,
):
    """Build a ChangeNotifier without running __init__ side effects."""
    notifier = ChangeNotifier.__new__(ChangeNotifier)

    class _Tg:
        pass

    class _RE:
        pass

    class _Smart:
        pass

    class _Settings:
        pass

    tg = _Tg()
    tg.allowed_users = allowed_users
    re = _RE()
    re.server_names = monitored_names
    smart = _Smart()
    smart.smart_mode = smart_mode
    smart.payday_start_minute = payday_start_minute
    smart.payday_end_minute = payday_end_minute
    settings = _Settings()
    settings.telegram = tg
    settings.realestate = re
    settings.smart_mode = smart

    notifier.settings = settings
    notifier.bot = object()  # send_notification is monkeypatched, never touched
    notifier._started_at = datetime.now(timezone.utc) - timedelta(hours=2)
    notifier._map_wait_since = {}
    return notifier


async def _add_freed(session, sid, kind, name, unit_id, when=None):
    repo = RealEstateRepository(session)
    ev = await repo.create_event({
        "object_key": repo.make_key(sid, kind, unit_id),
        "server_sid": sid,
        "kind": kind,
        "event_type": "freed",
        "name": name,
        "price": 100000,
        "class_name": "A",
    })
    if when is not None:
        ev.detected_at = when
        await session.flush()
    return ev


# ---- builder ----

def test_build_payday_report_empty_says_nothing_freed():
    notifier = _make_notifier(allowed_users=[1], monitored_names=["Murrieta"])
    msg = notifier._build_payday_report([], server_sid="20")
    assert "ничего не слетело" in msg
    # Includes today's date.
    assert datetime.now(timezone.utc).strftime("%d.%m.%Y") in msg


def test_build_payday_report_counts_houses_and_apartments():
    notifier = _make_notifier(allowed_users=[1], monitored_names=["Murrieta"])

    class _E:
        def __init__(self, kind, name, key):
            self.kind = kind
            self.name = name
            self.object_key = key
            self.class_name = "S"
            self.price = 250000

    events = [
        _E("house", "Вилла №1", "20:house:1"),
        _E("house", "Вилла №2", "20:house:2"),
        _E("apartment", "Кв. 12", "20:apartment:12"),
    ]
    msg = notifier._build_payday_report(events, server_sid="20")
    assert "Слетело домов: <b>2</b>" in msg
    assert "Слетело квартир: <b>1</b>" in msg
    assert "Вилла №1" in msg
    assert "Кв. 12" in msg


# ---- trigger ----

@pytest.mark.asyncio
async def test_payday_report_fires_once_per_recompute(session, monkeypatch):
    sent = []

    async def _fake_send(bot, user_id, message, **kwargs):
        sent.append((user_id, message))
        return True

    monkeypatch.setattr("app.telegram.notifier.send_notification", _fake_send)

    settings_repo = ScraperSettingsRepository(session)
    # Adopt an initial recompute marker without reporting (first-sight rule).
    await settings_repo.set("catalog_recompute:20", "1000")

    await SubscriptionRepository(session).subscribe(
        user_id=42, server_sid="20", kind="any"
    )

    notifier = _make_notifier(allowed_users=[42], monitored_names=["Murrieta"])

    # Feed the notifier this session so it uses the same in-memory DB.
    import app.telegram.notifier as notifier_mod

    class _Ctx:
        async def __aenter__(self_inner):
            return session

        async def __aexit__(self_inner, *a):
            return False

    monkeypatch.setattr(
        notifier_mod.DatabaseSession, "get_session_context", lambda: _Ctx()
    )

    # First pass: server seen for the first time -> latch, no report.
    await notifier._maybe_send_payday_report()
    assert sent == []

    # A house frees, then the catalog recomputes (marker advances).
    await _add_freed(session, "20", "house", "Вилла", 1)
    await _add_freed(session, "20", "apartment", "Кв. 5", 5)
    await settings_repo.set("catalog_recompute:20", "2000")

    await notifier._maybe_send_payday_report()
    assert len(sent) == 1
    uid, msg = sent[0]
    assert uid == 42
    assert "Слетело домов: <b>1</b>" in msg
    assert "Слетело квартир: <b>1</b>" in msg

    # Same marker again -> no duplicate report.
    await notifier._maybe_send_payday_report()
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_payday_report_waits_for_map_scrape(session, monkeypatch):
    """The report must NOT fire until the map scrape catches up to the recompute.

    When `map_scrape_done:<sid>` lags the catalog recompute marker, the report is
    held; once the map marker reaches the recompute, it fires. This is the core
    fix for "отчёт приходит ДО того как обновилась карта".
    """
    sent = []

    async def _fake_send(bot, user_id, message, **kwargs):
        sent.append((user_id, message))
        return True

    monkeypatch.setattr("app.telegram.notifier.send_notification", _fake_send)

    settings_repo = ScraperSettingsRepository(session)
    # Server already adopted (not first sight) and map is caught up to 1000.
    await settings_repo.set("catalog_recompute:20", "1000")
    await settings_repo.set("payday_report_marker:20", "1000")
    await settings_repo.set("map_scrape_done:20", "1000")

    await SubscriptionRepository(session).subscribe(
        user_id=42, server_sid="20", kind="any"
    )

    notifier = _make_notifier(allowed_users=[42], monitored_names=["Murrieta"])

    import app.telegram.notifier as notifier_mod

    class _Ctx:
        async def __aenter__(self_inner):
            return session

        async def __aexit__(self_inner, *a):
            return False

    monkeypatch.setattr(
        notifier_mod.DatabaseSession, "get_session_context", lambda: _Ctx()
    )

    # Catalog recomputes to 2000, a house frees — but the map hasn't scraped yet
    # (map_scrape_done still 1000). The report must be held.
    await _add_freed(session, "20", "house", "Вилла", 1)
    await settings_repo.set("catalog_recompute:20", "2000")

    await notifier._maybe_send_payday_report()
    assert sent == [], "report fired before the map scrape caught up"

    # Map scrape completes for the new recompute -> report is released.
    await settings_repo.set("map_scrape_done:20", "2000")
    await notifier._maybe_send_payday_report()
    assert len(sent) == 1
    assert "Слетело домов: <b>1</b>" in sent[0][1]


@pytest.mark.asyncio
async def test_payday_report_fires_when_no_map_marker(session, monkeypatch):
    """With no map_scrape_done marker (map scraper/gate off) the report still fires.

    Single-source deployments that don't run the browser map scrape must not have
    their Payday report blocked forever.
    """
    sent = []

    async def _fake_send(bot, user_id, message, **kwargs):
        sent.append((user_id, message))
        return True

    monkeypatch.setattr("app.telegram.notifier.send_notification", _fake_send)

    settings_repo = ScraperSettingsRepository(session)
    await settings_repo.set("catalog_recompute:20", "1000")
    await settings_repo.set("payday_report_marker:20", "1000")
    # Note: no map_scrape_done:20 set at all.

    await SubscriptionRepository(session).subscribe(
        user_id=42, server_sid="20", kind="any"
    )

    notifier = _make_notifier(allowed_users=[42], monitored_names=["Murrieta"])

    import app.telegram.notifier as notifier_mod

    class _Ctx:
        async def __aenter__(self_inner):
            return session

        async def __aexit__(self_inner, *a):
            return False

    monkeypatch.setattr(
        notifier_mod.DatabaseSession, "get_session_context", lambda: _Ctx()
    )

    await _add_freed(session, "20", "house", "Вилла", 1)
    await settings_repo.set("catalog_recompute:20", "2000")

    await notifier._maybe_send_payday_report()
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_payday_report_empty_when_nothing_freed(session, monkeypatch):
    sent = []

    async def _fake_send(bot, user_id, message, **kwargs):
        sent.append((user_id, message))
        return True

    monkeypatch.setattr("app.telegram.notifier.send_notification", _fake_send)

    settings_repo = ScraperSettingsRepository(session)
    await settings_repo.set("catalog_recompute:20", "1000")

    await SubscriptionRepository(session).subscribe(
        user_id=7, server_sid="20", kind="any"
    )

    notifier = _make_notifier(allowed_users=[7], monitored_names=["Murrieta"])

    import app.telegram.notifier as notifier_mod

    class _Ctx:
        async def __aenter__(self_inner):
            return session

        async def __aexit__(self_inner, *a):
            return False

    monkeypatch.setattr(
        notifier_mod.DatabaseSession, "get_session_context", lambda: _Ctx()
    )

    await notifier._maybe_send_payday_report()  # first sight -> latch only
    assert sent == []

    # Recompute with no freed events at all.
    await settings_repo.set("catalog_recompute:20", "2000")
    await notifier._maybe_send_payday_report()

    assert len(sent) == 1
    _, msg = sent[0]
    assert "ничего не слетело" in msg


@pytest.mark.asyncio
async def test_payday_report_respects_disable_toggle(session, monkeypatch):
    sent = []

    async def _fake_send(bot, user_id, message, **kwargs):
        sent.append((user_id, message))
        return True

    monkeypatch.setattr("app.telegram.notifier.send_notification", _fake_send)

    settings_repo = ScraperSettingsRepository(session)
    await settings_repo.set("payday_report", "0")  # disabled
    await settings_repo.set("catalog_recompute:20", "2000")

    notifier = _make_notifier(allowed_users=[7], monitored_names=["Murrieta"])

    import app.telegram.notifier as notifier_mod

    class _Ctx:
        async def __aenter__(self_inner):
            return session

        async def __aexit__(self_inner, *a):
            return False

    monkeypatch.setattr(
        notifier_mod.DatabaseSession, "get_session_context", lambda: _Ctx()
    )

    await notifier._maybe_send_payday_report()
    assert sent == []


# ---- Payday-window suppression of instant pings ----

def test_in_payday_window_off_when_smart_mode_disabled():
    notifier = _make_notifier([1], ["Murrieta"], smart_mode=False)
    assert notifier._in_payday_window() is False


def test_in_payday_window_wrapping_range(monkeypatch):
    """Window 59..10 wraps the hour; minute 5 and 59 are inside, 30 outside."""
    import app.telegram.notifier as notifier_mod

    notifier = _make_notifier(
        [1], ["Murrieta"],
        smart_mode=True, payday_start_minute=59, payday_end_minute=10,
    )

    class _FixedDT(datetime):
        _minute = 5

        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 12, cls._minute, tzinfo=tz)

    monkeypatch.setattr(notifier_mod, "datetime", _FixedDT)
    _FixedDT._minute = 5
    assert notifier._in_payday_window() is True
    _FixedDT._minute = 59
    assert notifier._in_payday_window() is True
    _FixedDT._minute = 30
    assert notifier._in_payday_window() is False


@pytest.mark.asyncio
async def test_freed_event_suppressed_during_payday_window(session, monkeypatch):
    """A 'freed' catalog event inside the Payday window is drained but NOT sent
    instantly — the recompute-gated report is authoritative during churn."""
    sent = []

    async def _fake_send(bot, user_id, message, **kwargs):
        sent.append((user_id, message))
        return True

    import app.telegram.notifier as notifier_mod
    monkeypatch.setattr(notifier_mod, "send_notification", _fake_send)

    notifier = _make_notifier([42], ["Murrieta"], smart_mode=True)
    notifier._in_payday_window = lambda: True  # force "inside window"

    await _add_freed(session, "20", "house", "Вилла", 1)

    await notifier._process_realestate_events(session, "1")

    # No instant ping fired, but the event is drained (marked notified).
    assert sent == []
    repo = RealEstateRepository(session)
    assert await repo.get_unnotified_events() == []


@pytest.mark.asyncio
async def test_freed_event_sent_instantly_outside_payday_window(session, monkeypatch):
    """Outside the Payday window a 'freed' event still pings instantly."""
    sent = []

    async def _fake_send(bot, user_id, message, **kwargs):
        sent.append((user_id, message))
        return True

    import app.telegram.notifier as notifier_mod
    monkeypatch.setattr(notifier_mod, "send_notification", _fake_send)

    notifier = _make_notifier([42], ["Murrieta"], smart_mode=True)
    notifier._in_payday_window = lambda: False

    await SubscriptionRepository(session).subscribe(
        user_id=42, server_sid="20", kind="any"
    )

    await _add_freed(session, "20", "house", "Вилла", 1)

    await notifier._process_realestate_events(session, "1")

    assert len(sent) == 1
    assert sent[0][0] == 42
