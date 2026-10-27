import importlib

from jobwatch.db import Vacancy


def test_same_post_in_two_channels_is_one_vacancy(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    from jobwatch import db

    importlib.reload(db)  # DB_PATH is read at import
    db.init()
    body = "Senior Python Developer (FastAPI)\nУдалённо, Казахстан. Стек: Python, FastAPI, PostgreSQL, RabbitMQ."
    a = Vacancy("telegram", "pydevjob:1", "Senior Python Developer", "https://t.me/pydevjob/1", "@pydevjob",
                text=f"#вакансия #remote\n{body}\nhttps://t.me/pydevjob")
    b = Vacancy("telegram", "job_python:7", "Senior Python Developer", "https://t.me/job_python/7", "@job_python",
                text=f"#senior\n{body}\n@job_python")
    assert db.add(a, 8, "queued") is not None
    assert db.add(b, 8, "queued") is None  # cross-posted copy

    # different companies with the same title and skills (Habr) stay separate
    h1 = Vacancy("habr", "1", "Python-разработчик", "u1", "Acme", text="Python, Django")
    h2 = Vacancy("habr", "2", "Python-разработчик", "u2", "Globex", text="Python, Django")
    assert db.add(h1, 4, "queued") is not None
    assert db.add(h2, 4, "queued") is not None

    # one job posted per country: same source + company + title, different id and text
    for i, country in enumerate(["Malaysia", "Singapore", "Indonesia"]):
        v = Vacancy("himalayas", f"bjak-{i}", "LLM Application Engineer", f"u{i}", "BJAK", text=f"Remote, {country}")
        assert (db.add(v, 20, "queued") is not None) == (i == 0)

    # Telegram: company is the channel, different employers post the same title -> keep both
    t1 = Vacancy("telegram", "ch:1", "Python Developer", "t1", "@pydevjob", text="Acme ищет Python Developer, FastAPI")
    t2 = Vacancy("telegram", "ch:2", "Python Developer", "t2", "@pydevjob", text="Globex ищет Python Developer, Django")
    assert db.add(t1, 8, "queued") is not None
    assert db.add(t2, 8, "queued") is not None

    # no company known -> can't tell, keep both
    n1 = Vacancy("landingjobs", "1", "Senior Data Engineer", "l1", "", text="Lisbon")
    n2 = Vacancy("landingjobs", "2", "Senior Data Engineer", "l2", "", text="Porto")
    assert db.add(n1, 7, "queued") is not None
    assert db.add(n2, 7, "queued") is not None
