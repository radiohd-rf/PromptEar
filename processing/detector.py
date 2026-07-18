"""Re-export from core.detector for backward compatibility."""

from core.detector import AudioDetector

detect_quiet_audio = AudioDetector.is_quiet
preprocess_audio = AudioDetector.preprocess

__all__ = ["detect_quiet_audio", "preprocess_audio", "AudioDetector"]
