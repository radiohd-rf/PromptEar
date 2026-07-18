"""Re-export from core.topic for backward compatibility."""

from core.topic import TopicDetector, DEFAULT_TOPIC_KEYWORDS as TOPIC_KEYWORDS

_detector = TopicDetector()
detect_topic = _detector.detect

__all__ = ["detect_topic", "TopicDetector", "TOPIC_KEYWORDS"]
