import asyncio
import logging
import time

from . import config, db, mq
from .scoring import score
from .sources import habr, hh, remoteok, telegram

SOURCES = [habr, remoteok, hh, telegram]
log = logging.getLogger("collect")


async def run_once(cfg: dict, channel):
    for src in SOURCES:
        if not src.enabled(cfg):
            continue
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
    await mq.declare(channel)
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
