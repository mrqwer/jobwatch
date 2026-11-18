import json
import time

import httpx

from jobwatch import db, llm


def test_parse_strips_think_and_fences_and_drops_unknown():
    text = '<think>hmm</think>```json\n{"results": [{"id": 1, "verdict": "fit"}, {"id": 2, "verdict": "great"},' \
           ' {"id": 9, "verdict": "no"}]}\n```'
    assert list(llm.parse(text, [1, 2])) == [1]


def test_parse_json_inside_prose():
    assert llm.parse('Here you go: {"id": 3, "verdict": "maybe"} done', [3])[3]["verdict"] == "maybe"


def test_limit_wait_daily_vs_minute():
    day = httpx.Response(429, text='{"error": "Quota exceeded for GenerateRequestsPerDayPerProjectPerModel"}')
    minute = httpx.Response(429, text="rate limit, try again")
    header = httpx.Response(429, headers={"retry-after": "7"})
    assert llm.limit_wait(day) > 60
    assert llm.limit_wait(minute) == 60
    assert llm.limit_wait(header) == 8


def test_slots_need_a_key(monkeypatch):
    cfg = {"llm": {"slots": [
        {"name": "a", "url": "https://x/v1/", "key": "K_A", "model": "m", "batch": 3},
        {"name": "b", "url": "https://y/v1", "key": "K_B", "model": "m"},
    ]}}
    monkeypatch.setenv("K_A", "secret")
    monkeypatch.delenv("K_B", raising=False)
    [s] = llm.slots(cfg)
    assert (s.name, s.key, s.url, s.batch) == ("a", "secret", "https://x/v1", 3)


def test_extra_accounts_get_own_slots(monkeypatch):
    cfg = {"llm": {"slots": [{"name": "cf", "url": "https://x/acc/${ACC}/v1", "key": "K", "model": "m"}]}}
    for k, v in {"K": "k1", "K_2": "k2", "K_10": "k10", "K_3": "", "ACC": "a1", "ACC_2": "a2"}.items():
        monkeypatch.setenv(k, v)
    got = [(s.name, s.key, s.url) for s in llm.slots(cfg)]
    assert got == [("cf", "k1", "https://x/acc/a1/v1"), ("cf-2", "k2", "https://x/acc/a2/v1"),
                   ("cf-10", "k10", "https://x/acc/a1/v1")]


def test_rejected_by_title():
    cfg = {"llm": {"reject_titles": ["php", "менеджер по продажам"]}}
    assert llm.rejected("Senior PHP Developer", cfg)
    assert not llm.rejected("Python developer (PHPUnit not needed)", cfg)


def test_llm_verdict_never_touches_your_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init()
    mine = db.add(db.Vacancy("hh", "1", "Python dev", "u", company="A"), 1, "yes")
    new = db.add(db.Vacancy("hh", "2", "LLM engineer", "u", company="B"), 1, "pending")
    assert not db.set_llm(mine, "no", "{}", "skipped")
    assert db.set_llm(new, "fit", json.dumps({"verdict": "fit"}), "queued")
    assert db.get(mine).status == "yes"
    assert (db.get(new).status, db.get(new).llm_verdict) == ("queued", "fit")


def test_next_midnight_is_ahead():
    assert 0 < llm.next_midnight_utc() - time.time() <= 86400


class Msg:
    def __init__(self, vid):
        self.body, self.acked = str(vid).encode(), False

    async def ack(self):
        self.acked = True


def test_limited_slot_hands_work_to_another(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    (tmp_path / "profile.md").write_text("python dev")
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "config.yaml"))
    db.init()
    vid = db.add(db.Vacancy("hh", "1", "LLM engineer", "u", company="A"), 1, "pending")
    published = []

    async def fake_publish(channel, v, prio, queue="vacancies"):
        published.append((v, prio))

    monkeypatch.setattr(llm.mq, "publish", fake_publish)

    def handler(req: httpx.Request):
        if req.url.host == "limited":
            return httpx.Response(429, text="per day quota")
        answer = {"results": [{"id": vid, "verdict": "fit", "why": "LLM роль"}]}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    async def main():
        inbox = asyncio.Queue()
        msg = Msg(vid)
        inbox.put_nowait(msg)
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        a = llm.Slot("a", "https://limited/v1", "m", "k", rpm=600)
        b = llm.Slot("b", "https://ok/v1", "m", "k", rpm=600)
        b.until = time.time() + 0.2  # a goes first and gets the 429
        tries = {}
        tasks = [asyncio.create_task(llm.worker(s, inbox, http, None, tries)) for s in (a, b)]
        for _ in range(50):
            await asyncio.sleep(0.05)
            if msg.acked:
                break
        for t in tasks:
            t.cancel()
        return msg, a

    msg, a = asyncio.run(main())
    assert msg.acked
    assert a.until >= llm.next_midnight_utc() - 5  # daily quota -> asleep till midnight UTC
    assert db.get(vid).status == "queued" and published == [(vid, 40)]
