import aio_pika

QUEUE = "vacancies"
# priority = score, so the highest score is delivered first; equal scores keep arrival order.
# ponytail: RabbitMQ advises few priority levels for throughput; at hundreds of messages it does not matter.
# Changing it needs the queue rebuilt: python -m jobwatch.requeue
MAX_PRIORITY = 50
VERDICT_PRIORITY = {"fit": 40, "maybe": 20}  # LLM verdict -> bot queue priority

# vacancies waiting for the LLM; new ones go before the backlog
LLM_QUEUE = "llm"
LLM_NEW, LLM_BACKLOG = 2, 1


async def declare(channel: aio_pika.abc.AbstractChannel):
    # priority queue: the best-scored vacancy is delivered first
    return await channel.declare_queue(QUEUE, durable=True, arguments={"x-max-priority": MAX_PRIORITY})


async def declare_llm(channel: aio_pika.abc.AbstractChannel):
    return await channel.declare_queue(LLM_QUEUE, durable=True, arguments={"x-max-priority": LLM_NEW})


async def publish(channel: aio_pika.abc.AbstractChannel, vid: int, score: float, queue: str = QUEUE):
    """The message is just the DB row id; the bot (or the LLM scorer) reads the details from SQLite."""
    await channel.default_exchange.publish(
        aio_pika.Message(
            str(vid).encode(),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            priority=int(min(max(round(score), 0), MAX_PRIORITY)),
        ),
        routing_key=queue,
    )
