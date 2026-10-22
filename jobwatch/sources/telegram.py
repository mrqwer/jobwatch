"""Public Telegram channels, read through a user account (bots cannot read channels).

One-time login (creates the session file in the data volume):
    docker compose run --rm -it collector python -m jobwatch.sources.telegram
"""

import asyncio
import logging
import os

from telethon import TelegramClient

from .. import db
from ..db import Vacancy

NAME = "telegram"
SESSION = os.environ.get("TG_SESSION", "data/tg")
log = logging.getLogger(NAME)


def _client() -> TelegramClient:
    return TelegramClient(SESSION, int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])


def enabled(cfg: dict) -> bool:
    return bool(
        os.environ.get("TG_API_ID") and os.environ.get("TG_API_HASH")
        and cfg.get("telegram_channels") and os.path.exists(SESSION + ".session")
    )


async def fetch(cfg: dict) -> list[Vacancy]:
    out = []
    client = _client()
    await client.connect()
    try:
        if not await client.is_authorized():
            log.warning("telegram session is not authorized, run the login command (see module docstring)")
            return out
        for ch in cfg["telegram_channels"]:
            ch = ch.lstrip("@")
            key = f"tg:{ch}"
            last = db.cursor_get(key)
            msgs = await client.get_messages(ch, limit=30 if last is None else 100, min_id=last or 0)
            for m in msgs:
                text = (m.message or "").strip()
                if len(text) < 40:  # reactions, "thanks", pictures without text
                    continue
                out.append(
                    Vacancy(
                        source=NAME,
                        ext_id=f"{ch}:{m.id}",
                        title=text.splitlines()[0][:120],
                        company="@" + ch,
                        url=f"https://t.me/{ch}/{m.id}",
                        text=text,
                    )
                )
            if msgs:
                db.cursor_set(key, max(m.id for m in msgs))
            await asyncio.sleep(2)
    finally:
        await client.disconnect()
    return out


if __name__ == "__main__":
    with _client() as c:  # prompts for phone / code interactively
        print("authorized as", c.loop.run_until_complete(c.get_me()).username)
