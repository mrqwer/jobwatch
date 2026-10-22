from jobwatch.scoring import score

W = {"python": 2, "fastapi": 2, "php": -3, "1c": -4}


def test_title_counts_double_body_once():
    assert score("Python dev", "", W) == 4
    assert score("Dev", "we use python", W) == 2
    assert score("Python dev", "python python", W) == 4  # keyword counts once


def test_whole_word_only():
    assert score("Pythonista", "", W) == 0
    assert score("Dev", "phpstorm", W) == 0


def test_negative_pushes_down_not_out():
    assert score("Python PHP dev", "", W) == 4 - 6


def test_cyrillic_and_punctuation():
    assert score("Senior Python-разработчик (FastAPI)", "", W) == 8
