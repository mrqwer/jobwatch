"""Rebuild the RabbitMQ queue from SQLite (the source of truth): every 'queued' row, priority = score.

The collector does this by itself on start when the queue is empty but SQLite has queued rows.
Run by hand after changing queue arguments (MAX_PRIORITY), with bot and collector stopped:
    docker compose stop bot collector
    docker compose run --rm collector python -m jobwatch.requeue
    docker compose up -d
"""

import asyncio

import aio_pika

from . import config, db, mq


async def publish_all(channel) -> int:
    """Queued rows to the bot (priority from the LLM verdict, else the keyword score), pending rows to the LLM."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT id, status, CASE llm_verdict WHEN 'fit' THEN ? WHEN 'maybe' THEN ? ELSE score END"
            " FROM vacancies WHERE status IN ('queued','pending')",
            (mq.VERDICT_PRIORITY["fit"], mq.VERDICT_PRIORITY["maybe"]),
        ).fetchall()
    for vid, status, priority in rows:
        if status == "pending":
            await mq.publish(channel, vid, mq.LLM_NEW, queue=mq.LLM_QUEUE)
        else:
            await mq.publish(channel, vid, priority)
    return len(rows)


async def main():
    db.init()
    connection = await aio_pika.connect_robust(config.AMQP_URL)
    channel = await connection.channel()
    await channel.queue_delete(mq.QUEUE)  # old arguments cannot be changed in place
    await mq.declare(channel)
    print(f"requeued {await publish_all(channel)} vacancies, max priority {mq.MAX_PRIORITY}")
    await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
