"""Public Telegram channels, read through a user account (bots cannot read channels).

One-time login (creates the session file in the data volume):
    docker compose run --rm -it collector python -m jobwatch.sources.telegram
List public channels/groups you are subscribed to (pick from them for config.yaml):
    docker compose run --rm -it collector python -m jobwatch.sources.telegram channels
"""

import asyncio
import logging
import os
import re
from collections import Counter

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


def title_of(text: str, boilerplate: frozenset = frozenset()) -> str:
    """First line that carries words: skips '#jobs', '🔍', '@channel' and a channel's repeated header."""
    for line in text.splitlines():
        line = line.strip()
        if line not in boilerplate and sum(c.isalpha() for c in re.sub(r"[#@]\w+", "", line)) >= 6:
            return line[:120]
    return text.strip()[:120]


async def fetch(cfg: dict) -> list[Vacancy]:
    out = []
    client = _client()
    await client.connect()
    try:
        if not await client.is_authorized():
            log.warning("telegram session is not authorized, run the login command (see module docstring)")
            return out
        if (await client.get_me()).bot:
            log.warning("telegram session is a BOT, it cannot read channels; delete data/tg.session and log in with your phone number")
            return out
        for ch in cfg["telegram_channels"]:
            ch = ch.lstrip("@")
            key = f"tg:{ch}"
            last = db.cursor_get(key)
            msgs = await client.get_messages(ch, limit=30 if last is None else 100, min_id=last or 0)
            texts = [(m, (m.message or "").strip()) for m in msgs]
            texts = [(m, t) for m, t in texts if len(t) >= 40]  # reactions, "thanks", pictures without text
            # a first line shared by 3+ posts of the batch is the channel's header, not a title
            firsts = Counter(t.splitlines()[0].strip() for _, t in texts)
            boiler = frozenset(line for line, n in firsts.items() if n >= 3)
            for m, text in texts:
                out.append(
                    Vacancy(
                        source=NAME,
                        ext_id=f"{ch}:{m.id}",
                        title=title_of(text, boiler),
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


async def _cli(list_channels: bool):
    async with _client() as c:  # first run prompts for phone / code interactively
        me = await c.get_me()
        print("authorized as", me.username)
        if me.bot:
            raise SystemExit("this is a BOT account and cannot read channels. Delete data/tg.session, "
                             "run again and enter your PHONE NUMBER (+country code), not the bot token.")
        if list_channels:
            async for d in c.iter_dialogs():
                e = d.entity
                if getattr(e, "username", None) and (getattr(e, "broadcast", False) or getattr(e, "megagroup", False)):
                    kind = "channel" if e.broadcast else "group"
                    print(f"@{e.username}\t{kind}\t{d.name}")


if __name__ == "__main__":
    import sys

    asyncio.run(_cli(sys.argv[1:] == ["channels"]))
