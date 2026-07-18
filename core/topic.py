"""TopicDetector — класс для определения темы текста по ключевым словам."""

DEFAULT_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "история": [
        "история", "век", "император", "война", "революция",
        "князь", "царь", "битва", "государство", "полководец",
        "империя", "древний", "средневековье", "поход", "династия",
    ],
    "образование": [
        "лекция", "студент", "университет", "экзамен", "курс",
        "обучение", "теория", "преподаватель", "семинар", "диплом",
    ],
    "техника": [
        "код", "сервер", "алгоритм", "нейросеть", "программа",
        "данные", "система", "модель", "чип", "процессор",
    ],
    "повседневный": [],
}


class TopicDetector:
    """Определяет тему текста по ключевым словам.

    Позволяет кастомизировать словарь ключевых слов.
    """

    def __init__(self, keywords: dict[str, list[str]] | None = None) -> None:
        self._keywords = keywords if keywords is not None else DEFAULT_TOPIC_KEYWORDS.copy()

    @property
    def keywords(self) -> dict[str, list[str]]:
        return self._keywords

    @keywords.setter
    def keywords(self, value: dict[str, list[str]]) -> None:
        self._keywords = value

    def detect(self, text: str) -> str:
        """Определяет тему текста. Возвращает одну из: история, образование, техника, повседневный."""
        text_lower = text.lower()
        scores = {}
        for topic, keywords in self._keywords.items():
            if not keywords:
                continue
            scores[topic] = sum(1 for kw in keywords if kw in text_lower)

        if not scores:
            return "повседневный"

        best = max(scores, key=lambda k: scores[k])
        return best if scores[best] > 0 else "повседневный"
