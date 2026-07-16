"""Тесты определения темы."""

from processing.topic import detect_topic


def test_history():
    text = "история войны и императоры древнего государства"
    assert detect_topic(text) == "история"


def test_education():
    text = "лекция в университете для студентов и преподавателей"
    assert detect_topic(text) == "образование"


def test_tech():
    text = "нейросеть обрабатывает данные с помощью алгоритмов"
    # tech = 2 (нейросеть, данные), education = 1 (данные) при совпадении — tech выше
    assert detect_topic(text) == "техника"


def test_daily():
    text = "погода сегодня хорошая пойду гулять"
    assert detect_topic(text) == "повседневный"


def test_empty():
    assert detect_topic("") == "повседневный"


def test_mixed_topics():
    text = "история развития алгоритмов в университете"
    result = detect_topic(text)
    assert result in ("история", "образование", "техника")


def test_case_insensitive():
    text = "ИСТОРИЯ ВОЙНЫ"
    assert detect_topic(text) == "история"


def test_partial_word():
    text = "историчка сказала"
    assert detect_topic(text) == "повседневный"  # "история" не сабстринг "исторички"


def test_no_keywords():
    text = "абракадабра ксепфа"
    assert detect_topic(text) == "повседневный"
