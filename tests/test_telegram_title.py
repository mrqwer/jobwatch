from jobwatch.sources.telegram import title_of


def test_skips_hashtags_emoji_and_mentions():
    assert title_of("#jobs\n#Астана\n#Remote\nPython Developer (FastAPI)\nОписание") == "Python Developer (FastAPI)"
    assert title_of("🔍\n🧰\nАссистент менеджера по продажам") == "Ассистент менеджера по продажам"
    assert title_of("@devs_kz\nSenior ML Engineer") == "Senior ML Engineer"


def test_skips_channel_header():
    header = "Daily Jobs IT. современные вакансии"
    assert title_of(f"{header}\n🤖\n Middle ML Engineer", frozenset({header})) == "Middle ML Engineer"


def test_falls_back_to_text_when_nothing_qualifies():
    assert title_of("#a #b\n🔥🔥") == "#a #b\n🔥🔥"
