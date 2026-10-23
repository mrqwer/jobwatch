import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import create_autospec

from telethon import TelegramClient


def test_fetch_uses_real_telethon_api(tmp_path, monkeypatch):
    """Autospec of the real TelegramClient: a call to a method Telethon does not have fails here,
    not in production (that is how 'is_authorized' slipped through)."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    from jobwatch import db
    from jobwatch.sources import telegram

    importlib.reload(db)
    db.init()
    client = create_autospec(TelegramClient, instance=True)
    client.disconnect.side_effect = lambda: asyncio.sleep(0)  # real one: plain def returning a coroutine
    client.is_user_authorized.return_value = True
    client.get_me.return_value = SimpleNamespace(bot=False)
    async def get_messages(ch, **kw):
        if ch == "gone":
            raise ValueError('No user has "gone" as username')  # what Telethon raises for a renamed channel
        return [
            SimpleNamespace(id=5, message="#jobs\nSenior Python Developer\nУдалённо, FastAPI, PostgreSQL, RabbitMQ"),
            SimpleNamespace(id=6, message="👍"),
        ]

    client.get_messages.side_effect = get_messages
    monkeypatch.setattr(telegram, "_client", lambda: client)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(telegram.asyncio, "sleep", lambda s: real_sleep(0))

    # a broken channel BEFORE a good one must not cost the good one its posts
    out = asyncio.run(telegram.fetch({"telegram_channels": ["gone", "@pydevjob"]}))

    assert [(v.ext_id, v.title, v.url) for v in out] == [
        ("pydevjob:5", "Senior Python Developer", "https://t.me/pydevjob/5")
    ]
    assert db.cursor_get("tg:pydevjob") == 6
