from jobwatch.sources.telegram import title_of


def test_skips_hashtags_emoji_and_mentions():
    assert title_of("#jobs\n#Астана\n#Remote\nPython Developer (FastAPI)\nОписание") == "Python Developer (FastAPI)"
    assert title_of("🔍\n🧰\nАссистент менеджера по продажам") == "Ассистент менеджера по продажам"
    assert title_of("@devs_kz\nSenior ML Engineer") == "Senior ML Engineer"


def test_skips_channel_header():
    header = "Daily Jobs IT. современные вакансии"
    assert title_of(f"{header}\n🤖\n Middle ML Engineer", frozenset({header})) == "Middle ML Engineer"


def test_labelled_title_wins_over_first_line():
    post = "#jobs\n#Астана\n📍\nФормат/working arrangement: удалённо\n✔️\nДолжность/position: Data/AI Engineer\n🏢"
    assert title_of(post) == "Data/AI Engineer"
    assert title_of("🔥 Горячее\nВакансия: ML-инженер (LLM / Python)\nЗП 500к") == "ML-инженер (LLM / Python)"
    assert title_of("Daily Jobs IT. современные вакансии\n\n🚀 Открыта вакансия:  Data Engineer") == "Data Engineer"


def test_falls_back_to_text_when_nothing_qualifies():
    assert title_of("#a #b\n🔥🔥") == "#a #b\n🔥🔥"
