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
