import asyncio
import logging
import time

from . import config, db, mq
from .requeue import publish_all
from .scoring import score
from .sources import boards, habr, hh, remoteok, telegram

SOURCES = [habr, remoteok, hh, telegram, *boards.BOARDS]
log = logging.getLogger("collect")


async def run_once(cfg: dict, channel):
    for src in SOURCES:
        if not src.enabled(cfg):
            continue
        last_ok = db.cursor_get(f"ok:{src.NAME}")
        if last_ok and time.time() - last_ok < getattr(src, "EVERY", 0):
            continue  # heavy / polite-use source, fetched recently
        try:
            items = await src.fetch(cfg)
        except Exception:  # one broken source must not stop the others
            log.exception("%s failed", src.NAME)
            continue
        db.cursor_set(f"ok:{src.NAME}", int(time.time()))  # shown in /stats
        queued = skipped = 0
        for v in items:
            s = score(v.title, v.text, cfg["keywords"])
            status = "queued" if s >= cfg["threshold"] else "skipped"
            vid = db.add(v, s, status)
            if vid is None:  # already seen
                continue
            if status == "skipped":
                skipped += 1
                continue
            try:
                await mq.publish(channel, vid, s)
            except Exception:
                db.delete(vid)  # not published -> forget it, the next cycle retries
                raise
            queued += 1
        log.info("%s: fetched %d, new queued %d, new skipped %d", src.NAME, len(items), queued, skipped)


async def main():
    import aio_pika

    db.init()
    connection = await aio_pika.connect_robust(config.AMQP_URL)
    channel = await connection.channel()
    queue = await mq.declare(channel)
    if queue.declaration_result.message_count == 0:
        # broker lost its data (new node, deleted volume): SQLite still knows what is waiting.
        # ponytail: the one card the bot may hold unanswered can come twice; harmless, the bot skips decided ones
        restored = await publish_all(channel)
        if restored:
            log.warning("queue was empty, restored %d vacancies from SQLite", restored)
    while True:
        cfg = config.load()
        try:
            await run_once(cfg, channel)
        except Exception:
            log.exception("cycle failed")
        await asyncio.sleep(cfg.get("poll_seconds", 900))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(main())
