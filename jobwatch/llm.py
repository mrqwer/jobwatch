"""LLM verdict for every vacancy, spread over many free API "slots".

A slot is one OpenAI-compatible endpoint + one model with its own free limits (config.yaml -> llm.slots).
All slots pull from the `llm` RabbitMQ queue at their own pace. A slot that hits its limit (HTTP 429)
sleeps until the limit resets and the others keep going; a slot without a key in .env is off.

    python -m jobwatch.llm              # the scorer service (docker compose: scorer)
    python -m jobwatch.llm models       # which model ids each key can use
    python -m jobwatch.llm calibrate [slot]  # the model's verdicts next to your ✅/❌, nothing is written
    python -m jobwatch.llm backfill     # send everything not judged yet to the queue
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import aio_pika
import httpx

from . import config, db, mq
from .scoring import score

log = logging.getLogger("llm")
VERDICTS = ("fit", "maybe", "no")
MAX_TRIES = 5  # a vacancy no model manages to answer goes to you as "maybe" instead of looping forever

PROMPT = """You screen job vacancies for one candidate. His profile:
---
{profile}
---
For EACH vacancy below give a verdict:
- "fit": he should apply: the role, level and conditions match the profile.
- "maybe": worth a look: adjacent role, stretch level, or the conditions are unclear.
- "no": another profession or stack, internship, or he cannot take it (location, citizenship, office he cannot reach).
Judge by the whole text, not by keywords: "Data Analyst, Python is a plus" is not a Python developer role.

Answer with JSON only, one item per vacancy, same ids:
{{"results": [{{"id": 123, "verdict": "fit|maybe|no", "summary": "...", "why": "...", "missing": "...",
"level": "junior|middle|senior|lead|unknown", "work": "remote|relocation|office|hybrid|unknown",
"cv": "Backend|AIML|Go|Generalist"}}]}}
summary: what the job is, one short line in Russian. why: the reason for the verdict, one short line in Russian.
missing: requirements he does not have, short, in Russian, "" if none. cv: which of his CV variants fits best."""


@dataclass
class Slot:
    name: str
    url: str
    model: str
    key: str
    rpm: float = 10  # requests per minute this slot is allowed
    rpd: int = 0  # requests per day, 0 = only the provider's 429 tells
    batch: int = 1  # vacancies per request: multiplies a per-request daily limit
    max_chars: int = 3000  # vacancy text is cut to this
    json: bool = True  # response_format=json_object; some providers/models reject it
    system: bool = True  # Gemma on Google AI Studio rejects a system message
    extra: dict = field(default_factory=dict)  # merged into the request body, e.g. reasoning_effort
    until: float = 0  # asleep until this time (rate limit)
    fails: int = 0


class SlotError(Exception):
    def __init__(self, msg: str, wait: float):
        super().__init__(msg)
        self.wait = wait


def slots(cfg: dict) -> list[Slot]:
    """One slot per key in .env. Extra accounts: KEY_2, KEY_3, ... (any number) give slots name-2, name-3, ...
    with their own limits; ${VAR} in the url prefers VAR_2 for account 2 (Cloudflare account id)."""
    out = []
    for s in cfg.get("llm", {}).get("slots", []):
        extra = sorted((k for k in os.environ if re.fullmatch(re.escape(s["key"]) + r"_\d+", k)),
                       key=lambda k: int(k.rsplit("_", 1)[1]))
        for env in [s["key"], *extra]:
            key = os.environ.get(env, "")
            if not key:
                continue  # no key in .env -> slot off
            n = env[len(s["key"]):]  # "" or "_2"
            url = re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m[1] + n) or os.environ.get(m[1], ""), s["url"])
            known = {k: v for k, v in s.items() if k in Slot.__dataclass_fields__ and k not in ("key", "url", "name")}
            out.append(Slot(name=s["name"] + n.replace("_", "-"), url=url.rstrip("/"), key=key, **known))
    return out


def enabled(cfg: dict) -> bool:
    return bool(slots(cfg))


def rejected(title: str, cfg: dict) -> bool:
    """Obvious junk by title (1C, PHP, sales...): not worth a request."""
    words = cfg.get("llm", {}).get("reject_titles", [])
    return score(title, "", {w: 1 for w in words}) > 0


def profile() -> str:
    path = os.path.join(os.path.dirname(os.environ.get("CONFIG_PATH", "config.yaml")), "profile.md")
    with open(path, encoding="utf-8") as f:
        return f.read()


def next_midnight_utc() -> float:
    now = datetime.now(timezone.utc)
    return (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def used_today(name: str) -> int:
    return db.cursor_get(f"llm:{name}:{_today()}") or 0


def _count(name: str):
    db.cursor_set(f"llm:{name}:{_today()}", used_today(name) + 1)  # one scorer process, no race


def limit_wait(r: httpx.Response) -> float:
    """How long a slot sleeps after a refusal."""
    if r.status_code == 429:
        ra = r.headers.get("retry-after", "")
        if ra.replace(".", "", 1).isdigit():
            return float(ra) + 1
        if re.search(r"per.?day|daily|\bday\b|RPD|PerDay", r.text, re.I):
            return next_midnight_utc() - time.time()
        return 60
    if r.status_code in (401, 403, 404):  # bad key, no access to this model, wrong model id
        return 6 * 3600
    if r.status_code == 400:  # often this batch (too long for the model); retry later, backoff grows
        return 300
    return 60  # 5xx


def render(vs: list[db.Vacancy], max_chars: int) -> str:
    return "\n\n".join(
        f"### id={v.id}\ntitle: {v.title}\ncompany: {v.company} | source: {v.source} | salary: {v.salary or '-'}\n"
        f"{(v.text or '')[:max_chars]}"
        for v in vs
    )


def parse(text: str, ids: list[int]) -> dict[int, dict]:
    """The answers we can use, by vacancy id. Raises ValueError when there is no JSON at all."""
    text = re.sub(r"<think>.*?</think>|```(?:json)?", "", text or "", flags=re.S).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError(f"no JSON in answer: {text[:200]!r}")
        data = json.loads(m.group(0))
    items = data.get("results", [data]) if isinstance(data, dict) else data
    out = {}
    for it in items if isinstance(items, list) else []:
        try:
            vid = int(it["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if vid in ids and it.get("verdict") in VERDICTS:
            out[vid] = it
    return out


async def ask(http: httpx.AsyncClient, s: Slot, vs: list[db.Vacancy]) -> dict[int, dict]:
    prompt = PROMPT.format(profile=profile())
    work = render(vs, s.max_chars)
    messages = (
        [{"role": "system", "content": prompt}, {"role": "user", "content": work}]
        if s.system
        else [{"role": "user", "content": f"{prompt}\n\n{work}"}]
    )
    body = {"model": s.model, "messages": messages, "temperature": 0, "max_tokens": 1500 + 300 * len(vs), **s.extra}
    if s.json:
        body["response_format"] = {"type": "json_object"}
    try:
        r = await http.post(f"{s.url}/chat/completions", json=body, headers={"Authorization": f"Bearer {s.key}"})
    except httpx.HTTPError as e:
        raise SlotError(f"network: {e!r}", 60)
    if r.status_code != 200:
        raise SlotError(f"HTTP {r.status_code}: {r.text[:300]}", limit_wait(r))
    try:
        return parse(r.json()["choices"][0]["message"]["content"], [v.id for v in vs])
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise SlotError(f"bad answer: {e}", 30)


async def apply(channel, v: db.Vacancy, answer: dict, slot: str):
    verdict = answer["verdict"]
    answer["model"] = slot
    status = "queued" if verdict in mq.VERDICT_PRIORITY else "skipped"
    stored = db.set_llm(v.id, verdict, json.dumps(answer, ensure_ascii=False), status)
    if stored and status == "queued" and v.status != "queued":  # an already queued row is in the bot queue
        await mq.publish(channel, v.id, mq.VERDICT_PRIORITY[verdict])


async def worker(s: Slot, inbox: asyncio.Queue, http: httpx.AsyncClient, channel, tries: dict[int, int]):
    while True:
        await asyncio.sleep(max(0.0, s.until - time.time()))
        if s.rpd and used_today(s.name) >= s.rpd:
            s.until = next_midnight_utc()
            db.cursor_set(f"llm_until:{s.name}", int(s.until))
            continue
        msgs = [await inbox.get()]
        while len(msgs) < s.batch and not inbox.empty():
            msgs.append(inbox.get_nowait())
        todo = []
        for m in msgs:
            v = db.get(int(m.body))
            if v is None or v.llm_verdict or v.status not in ("pending", "skipped", "queued"):
                await m.ack()  # judged already (backfill sent it twice) or you answered it
            else:
                todo.append((v, m))
        if not todo:
            continue
        started = time.time()
        _count(s.name)
        try:
            res = await ask(http, s, [v for v, _ in todo])
        except SlotError as e:
            s.fails += 1
            wait = e.wait if e.wait > 3600 else min(e.wait * 2 ** (s.fails - 1), 3600)
            s.until = time.time() + wait
            db.cursor_set(f"llm_until:{s.name}", int(s.until))
            log.warning("%s: %s -> sleep %.0fs", s.name, e, wait)
            for _, m in todo:
                inbox.put_nowait(m)  # another slot takes them
            continue
        s.fails = 0
        for v, m in todo:
            if v.id in res:
                await apply(channel, v, res[v.id], s.name)
                await m.ack()
                continue
            tries[v.id] = tries.get(v.id, 0) + 1
            if tries[v.id] >= MAX_TRIES:
                await apply(channel, v, {"verdict": "maybe", "why": "модели не смогли оценить, посмотри сам"}, s.name)
                await m.ack()
            else:
                inbox.put_nowait(m)
        log.info("%s: %d/%d judged", s.name, len(res), len(todo))
        s.until = started + 60 / s.rpm


async def run():
    db.init()
    active = slots(config.load())
    if not active:
        log.warning("no LLM keys in .env: scorer idle, the collector keeps keyword scoring")
        while True:
            await asyncio.sleep(3600)
    connection = await aio_pika.connect_robust(config.AMQP_URL)
    channel = await connection.channel()
    # every slot can hold a full batch and have the next one ready; unacked messages return on a crash
    await channel.set_qos(prefetch_count=sum(s.batch for s in active) * 2)
    await mq.declare(channel)
    queue = await mq.declare_llm(channel)
    inbox: asyncio.Queue = asyncio.Queue()
    await queue.consume(inbox.put)
    log.info("slots: %s", ", ".join(f"{s.name} ({s.model})" for s in active))
    tries: dict[int, int] = {}
    async with httpx.AsyncClient(timeout=180) as http:
        await asyncio.gather(*(worker(s, inbox, http, channel, tries) for s in active))


async def models():
    """One real request per slot: proves key, url and model id together. Lists the provider's models on failure."""
    with db.conn() as c:
        row = c.execute("SELECT id FROM vacancies WHERE status IN ('yes','no') ORDER BY id DESC LIMIT 1").fetchone() \
            or c.execute("SELECT id FROM vacancies ORDER BY id DESC LIMIT 1").fetchone()
    v = db.get(row[0])
    print(f"тестовая вакансия: {v.id} {v.title}")
    async with httpx.AsyncClient(timeout=180) as http:
        for s in slots(config.load()):
            try:
                answer = (await ask(http, s, [v])).get(v.id)
                print(f"OK   {s.name}: {s.model} -> {answer['verdict'] if answer else 'ответ без этой вакансии'}")
                continue
            except SlotError as e:
                print(f"FAIL {s.name}: {s.model} -> {e}")
            try:  # what the provider does have
                r = await http.get(f"{s.url}/models", headers={"Authorization": f"Bearer {s.key}"})
                data = r.json()
                items = data.get("data", []) if isinstance(data, dict) else data
                ids = sorted(m["id"] for m in items if isinstance(m, dict) and m.get("id"))
                print("     есть: " + ", ".join(ids))
            except (httpx.HTTPError, ValueError, AttributeError):
                pass


async def calibrate(name: str | None):
    active = [s for s in slots(config.load()) if name in (None, s.name)]
    if not active:
        sys.exit(f"нет слота {name!r} с ключом" if name else "нет ни одного ключа в .env")
    s = active[0]
    with db.conn() as c:
        rows = c.execute("SELECT id FROM vacancies WHERE status IN ('yes','no') ORDER BY id").fetchall()
    vs = [db.get(r[0]) for r in rows]
    agree = 0
    async with httpx.AsyncClient(timeout=180) as http:
        for i in range(0, len(vs), s.batch):
            chunk = vs[i : i + s.batch]
            res = {}
            for _ in range(3):  # 503 "high demand" is common on free tiers and passes in a minute
                try:
                    res = await ask(http, s, chunk)
                    break
                except SlotError as e:
                    print(f"{s.name}: {str(e)[:150]} — повтор через {min(e.wait, 60):.0f}с")
                    await asyncio.sleep(min(e.wait, 60))
            for v in chunk:
                a = res.get(v.id, {})
                you = "✅" if v.status == "yes" else "❌"
                ok = (v.status == "yes") == (a.get("verdict") in ("fit", "maybe"))
                agree += ok
                print(f"{'  ' if ok else '!!'} {you} {a.get('verdict', '—'):5} #{v.id} {v.title[:55]} | {a.get('why', '')}")
            await asyncio.sleep(60 / s.rpm)
    print(f"\n{s.name}: совпало {agree} из {len(vs)} (✅ = fit/maybe, ❌ = no)")


async def backfill():
    cfg = config.load()
    db.init()
    connection = await aio_pika.connect_robust(config.AMQP_URL)
    channel = await connection.channel()
    await mq.declare_llm(channel)
    with db.conn() as c:
        # keyword-queued rows go through the LLM too; their old bot messages are dropped on delivery
        c.execute("UPDATE vacancies SET status='pending' WHERE status='queued' AND llm_verdict IS NULL")
        rows = c.execute(
            "SELECT id, title, status FROM vacancies WHERE status IN ('pending','skipped') AND llm_verdict IS NULL"
            " ORDER BY id DESC"  # newest first: they are still open
        ).fetchall()
    sent = 0
    for vid, title, status in rows:
        if rejected(title, cfg):
            if status == "pending":
                db.set_status(vid, "skipped", only_from="pending")
            continue
        await mq.publish(channel, vid, mq.LLM_BACKLOG, queue=mq.LLM_QUEUE)
        sent += 1
    await connection.close()
    print(f"в очередь LLM: {sent}, отброшено по заголовку: {len(rows) - sent}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "models":
        asyncio.run(models())
    elif cmd == "calibrate":
        db.init()
        asyncio.run(calibrate(sys.argv[2] if len(sys.argv) > 2 else None))
    elif cmd == "backfill":
        asyncio.run(backfill())
    else:
        asyncio.run(run())
