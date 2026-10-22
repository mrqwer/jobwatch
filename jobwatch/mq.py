import aio_pika

QUEUE = "vacancies"
MAX_PRIORITY = 10


async def declare(channel: aio_pika.abc.AbstractChannel):
    # priority queue: the best-scored vacancy is delivered first
    return await channel.declare_queue(QUEUE, durable=True, arguments={"x-max-priority": MAX_PRIORITY})


async def publish(channel: aio_pika.abc.AbstractChannel, vid: int, score: float):
    """The message is just the DB row id; the bot reads the details from SQLite."""
    await channel.default_exchange.publish(
        aio_pika.Message(
            str(vid).encode(),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            priority=int(min(max(score, 0), MAX_PRIORITY)),
        ),
        routing_key=QUEUE,
    )
