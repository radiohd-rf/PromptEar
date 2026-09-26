"""Тесты определения темы текста (core/topic.py)."""

from core.topic import DEFAULT_TOPIC_KEYWORDS, TopicDetector


def test_default_keywords_shape() -> None:
    assert set(DEFAULT_TOPIC_KEYWORDS) == {"история", "образование", "техника", "повседневный"}
    assert DEFAULT_TOPIC_KEYWORDS["повседневный"] == []


def test_detect_history() -> None:
    detector = TopicDetector()
    assert detector.detect("Век императора, великая революция и война") == "история"


def test_detect_education() -> None:
    detector = TopicDetector()
    assert detector.detect("Лекция для студентов университета, семинар") == "образование"


def test_detect_tech() -> None:
    detector = TopicDetector()
    assert detector.detect("Код на сервере, алгоритм нейросети") == "техника"


def test_detect_daily() -> None:
    detector = TopicDetector()
    assert detector.detect("Вчера гулял в парке и пил кофе") == "повседневный"


def test_detect_empty_text() -> None:
    assert TopicDetector().detect("") == "повседневный"


def test_detect_case_insensitive() -> None:
    assert TopicDetector().detect("ЛЕКЦИЯ") == "образование"


def test_custom_keywords() -> None:
    detector = TopicDetector(keywords={"спорт": ["футбол", "гол"], "повседневный": []})
    assert detector.detect("футбольный гол в овертайме") == "спорт"
    assert detector.detect("обычный день") == "повседневный"


def test_keywords_setter() -> None:
    detector = TopicDetector()
    detector.keywords = {"спорт": ["футбол"]}
    assert detector.keywords == {"спорт": ["футбол"]}
