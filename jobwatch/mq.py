import aio_pika

QUEUE = "vacancies"
# priority = score, so the highest score is delivered first; equal scores keep arrival order.
# ponytail: RabbitMQ advises few priority levels for throughput; at hundreds of messages it does not matter.
# Changing it needs the queue rebuilt: python -m jobwatch.requeue
MAX_PRIORITY = 50


async def declare(channel: aio_pika.abc.AbstractChannel):
    # priority queue: the best-scored vacancy is delivered first
    return await channel.declare_queue(QUEUE, durable=True, arguments={"x-max-priority": MAX_PRIORITY})


async def publish(channel: aio_pika.abc.AbstractChannel, vid: int, score: float):
    """The message is just the DB row id; the bot reads the details from SQLite."""
    await channel.default_exchange.publish(
        aio_pika.Message(
            str(vid).encode(),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            priority=int(min(max(round(score), 0), MAX_PRIORITY)),
        ),
        routing_key=QUEUE,
    )
