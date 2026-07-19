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
)
from app.telegram.notifier import ChangeNotifier


def _make_notifier(allowed_users, monitored_names):
    """Build a ChangeNotifier without running __init__ side effects."""
    notifier = ChangeNotifier.__new__(ChangeNotifier)

    class _Tg:
        pass

    class _RE:
        pass

    class _Settings:
        pass

    tg = _Tg()
    tg.allowed_users = allowed_users
    re = _RE()
    re.server_names = monitored_names
    settings = _Settings()
    settings.telegram = tg
    settings.realestate = re

    notifier.settings = settings
    notifier.bot = object()  # send_notification is monkeypatched, never touched
    notifier._started_at = datetime.now(timezone.utc) - timedelta(hours=2)
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

    async def _fake_send(bot, user_id, message):
        sent.append((user_id, message))
        return True

    monkeypatch.setattr("app.telegram.notifier.send_notification", _fake_send)

    settings_repo = ScraperSettingsRepository(session)
    # Adopt an initial recompute marker without reporting (first-sight rule).
    await settings_repo.set("catalog_recompute:20", "1000")

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
async def test_payday_report_empty_when_nothing_freed(session, monkeypatch):
    sent = []

    async def _fake_send(bot, user_id, message):
        sent.append((user_id, message))
        return True

    monkeypatch.setattr("app.telegram.notifier.send_notification", _fake_send)

    settings_repo = ScraperSettingsRepository(session)
    await settings_repo.set("catalog_recompute:20", "1000")

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

    async def _fake_send(bot, user_id, message):
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
