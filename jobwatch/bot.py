"""Telegram bot: takes ONE vacancy at a time from RabbitMQ (prefetch=1), acks it only when you
press yes/no. Until then the rest stay in the queue; a restart re-delivers the unanswered one."""

import asyncio
import html
import logging
import os
import time

import aio_pika
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from . import config, db, mq

log = logging.getLogger("bot")
OWNER = int(os.environ.get("OWNER_CHAT_ID", "0"))
STALE_SECONDS = 3 * 3600  # a source silent for 3h (poll is 15 min) is broken, not quiet

bot = Bot(os.environ.get("TELEGRAM_BOT_TOKEN", "0:placeholder"), default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
dp.message.filter(F.chat.id == OWNER)
dp.callback_query.filter(F.message.chat.id == OWNER)

# ponytail: module-level state, single process, single owner
pending: dict[int, aio_pika.abc.AbstractIncomingMessage] = {}
channel: aio_pika.abc.AbstractChannel


def card(v: db.Vacancy) -> str:
    head = f"<b>{html.escape(v.title)}</b>\n"
    meta = " · ".join(x for x in (v.company, v.source, v.salary, f"score {v.score:g}") if x)
    body = html.escape(v.text[:400]) + ("…" if len(v.text) > 400 else "")
    return f"{head}{html.escape(meta)}\n\n{body}\n\n<a href=\"{html.escape(v.url)}\">открыть</a>"


def keyboard(vid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Подходит", callback_data=f"v:{vid}:y"),
            InlineKeyboardButton(text="❌ Нет", callback_data=f"v:{vid}:n"),
        ]]
    )


def lines(vs: list[db.Vacancy]) -> str:
    return "\n".join(
        f"#{v.id} <a href=\"{html.escape(v.url)}\">{html.escape(v.title[:70])}</a>"
        f" — {html.escape(v.company)} [{v.score:g}]"
        for v in vs
    ) or "пусто"


async def on_delivery(msg: aio_pika.abc.AbstractIncomingMessage):
    vid = int(msg.body)
    v = db.get(vid)
    if v is None or v.status != "queued":  # already decided (e.g. re-delivery after restart)
        await msg.ack()
        return
    pending[vid] = msg
    try:
        await bot.send_message(OWNER, card(v), reply_markup=keyboard(vid), disable_web_page_preview=True)
    except Exception:
        log.exception("send failed, requeue")
        pending.pop(vid, None)
        await asyncio.sleep(30)
        await msg.nack(requeue=True)


@dp.callback_query(F.data.startswith("v:"))
async def verdict(cb: CallbackQuery):
    _, vid, ans = cb.data.split(":")
    vid = int(vid)
    status = "yes" if ans == "y" else "no"
    db.set_status(vid, status)
    msg = pending.pop(vid, None)
    if msg:
        await msg.ack()  # releases the next vacancy from the queue
    mark = "✅ подходит" if status == "yes" else "❌ не подходит"
    await cb.message.edit_text(f"{cb.message.html_text}\n\n<b>{mark}</b>", reply_markup=None, disable_web_page_preview=True)
    await cb.answer()


@dp.message(Command("start", "help"))
async def help_(m: Message):
    await m.answer(
        "/stats — счётчики и длина очереди\n"
        "/liked — последние ✅\n"
        "/skipped — отсеянные по порогу, лучшие сверху (проверка фильтра)\n"
        "/promote &lt;id&gt; — вернуть отсеянную в очередь"
    )


@dp.message(Command("stats"))
async def stats(m: Message):
    q = await mq.declare(channel)
    c = db.counts()
    now = time.time()
    health = "\n".join(
        f"{'⚠️' if now - ts > STALE_SECONDS else '✓'} {name}: {int((now - ts) // 60)} мин назад"
        for name, ts in db.cursors("ok:").items()
    ) or "источники ещё ни разу не отработали"
    await m.answer(
        f"в очереди: {q.declaration_result.message_count} (+{len(pending)} ждёт ответа)\n"
        f"✅ {c.get('yes', 0)} · ❌ {c.get('no', 0)} · отсеяно {c.get('skipped', 0)}\n\n"
        f"последний успешный сбор:\n{health}"
    )


@dp.message(Command("liked"))
async def liked(m: Message):
    await m.answer(lines(db.listing("yes", 20)), disable_web_page_preview=True)


@dp.message(Command("skipped"))
async def skipped(m: Message):
    await m.answer(lines(db.listing("skipped", 15)), disable_web_page_preview=True)


@dp.message(Command("promote"))
async def promote(m: Message, command):
    try:
        vid = int(command.args)
    except (TypeError, ValueError):
        await m.answer("/promote &lt;id&gt;")
        return
    v = db.get(vid)
    if v and db.set_status(vid, "queued", only_from="skipped"):
        await mq.publish(channel, vid, 10)  # you picked it by hand -> top priority
        await m.answer("вернул в очередь")
    else:
        await m.answer("нет такой отсеянной")


async def main():
    global channel
    db.init()
    connection = await aio_pika.connect_robust(config.AMQP_URL)
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=1)  # <- the whole "one at a time" mechanism
    queue = await mq.declare(channel)
    await queue.consume(on_delivery)
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(main())
