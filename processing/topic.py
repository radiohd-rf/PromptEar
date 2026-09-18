"""Re-export from core.topic for backward compatibility."""

from core.topic import DEFAULT_TOPIC_KEYWORDS as TOPIC_KEYWORDS
from core.topic import TopicDetector

_detector = TopicDetector()
detect_topic = _detector.detect

__all__ = ["detect_topic", "TopicDetector", "TOPIC_KEYWORDS"]
