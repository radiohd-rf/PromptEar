"""Динамическая Material You палитра для PromptEar.

- Системная тема Windows (реестр Personalize).
- Seed-цвет: DWMA-акцент Windows → доминирующий цвет обоев → fallback #4fc3f7.
- Тоналы из предположения о том, что функции палитры HCT брать нельзя:
  генерируем tonal palette упрощённым алгоритмом (HSL + десатурация на краях),
  без внешних зависимостей — работает в офлайн-сборке.
"""

import ctypes
import os
import winreg
from pathlib import Path

DEFAULT_SEED = 0xFF7C6FF0  # фиолетовый M3-акцент — новый seed палитры

# M3: роль -> требуемый tone (target light/dark).
ROLE_TONES = {
    "primary": (40, 80),
    "on-primary": (100, 20),
    "primary-container": (90, 30),
    "on-primary-container": (10, 90),
    "secondary": (40, 80),
    "on-secondary": (100, 20),
    "secondary-container": (90, 30),
    "on-secondary-container": (10, 90),
    "tertiary": (40, 80),
    "on-tertiary": (100, 20),
    "tertiary-container": (90, 30),
    "on-tertiary-container": (10, 90),
    "error": (40, 80),
    "on-error": (100, 20),
    "error-container": (90, 30),
    "on-error-container": (10, 90),
    "surface": (98, 10),
    "on-surface": (10, 88),
    "surface-variant": (90, 30),
    "on-surface-variant": (30, 80),
    "outline": (50, 60),
    "outline-variant": (80, 30),
    "surface-container-lowest": (100, 6),
    "surface-container-low": (96, 14),
    "surface-container": (94, 17),
    "surface-container-high": (92, 21),
    "surface-container-highest": (90, 26),
}

NEUTRAL_SAT = 0.045  # нейтральные роли почти ахроматичны (M3 neutral palette)
SUCCESS_SEED = 0xFF3E9B5A  # отдельная «зелёная» палитра для успеха


# ── Системная тема и seed ─────────────────────────────────────────


def system_dark() -> bool:
    """True если Windows в тёмной теме (AppsUseLightTheme == 0)."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        with key:
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return val == 0
    except OSError:
        return True


def _wallpaper_path() -> str | None:
    candidates = []
    transcoded = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Themes/TranscodedWallpaper"
    if transcoded.exists():
        candidates.append(transcoded)
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop")
        with key:
            wp, _ = winreg.QueryValueEx(key, "WallPaper")
        if wp and Path(wp).exists():
            candidates.append(Path(wp))
    except OSError:
        pass
    return str(candidates[0]) if candidates else None


def _dominant_color(path: str) -> int | None:
    """Доминирующий (самый частотный не-серый) цвет обоев, ARGB пиксель RGB."""
    try:
        from PIL import Image

        img = Image.open(path).convert("RGB").resize((64, 64))
        q = img.quantize(colors=8)
        palette = q.getpalette()
        counts = sorted(q.getcolors(), reverse=True)
        for _count, idx in counts:
            r, g, b = palette[idx * 3 : idx * 3 + 3]
            m, s = max(r, g, b), min(r, g, b)
            if m - s > 24:  # отсекаем серые кластеры
                return int(f"ff{r:02x}{g:02x}{b:02x}", 16)
    except Exception:
        pass
    return None


def _dwm_accent() -> int | None:
    """Акцентный цвет Windows через DwmGetColorizationColor (BGR COLORREF)."""
    try:
        color, alpha = ctypes.c_uint32(), ctypes.c_uint32()
        if (
            ctypes.windll.dwmapi.DwmGetColorizationColor(ctypes.byref(color), ctypes.byref(alpha))
            == 0
        ):
            c = color.value & 0xFFFFFF
            return 0xFF000000 | ((c & 0xFF) << 16) | (c & 0xFF00) | (c >> 16)
    except Exception:
        pass
    return None


def seed_color() -> tuple[int, str]:
    """Всегда фиксированный акцент PromptEar (системный акцент Windows игнорируем)."""
    return DEFAULT_SEED, "default"

def _dwm_accent_old() -> int | None:
    """только для справки — больше не используется."""
    return _dwm_accent()

# ── Упрощённая tonal palette (fallback-HCT) ───────────────────────


def _hex_to_hsv(argb: int) -> tuple[float, float, float]:
    r = ((argb >> 16) & 0xFF) / 255
    g = ((argb >> 8) & 0xFF) / 255
    b = (argb & 0xFF) / 255
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d == 0:
        h = 0.0
    elif mx == r:
        h = 60 * (((g - b) / d) % 6)
    elif mx == g:
        h = 60 * ((b - r) / d + 2)
    else:
        h = 60 * ((r - g) / d + 4)
    s = 0.0 if mx == 0 else d / mx
    return h, s, mx


def _hsl_to_rgb(h: float, s: float, lightness: float) -> int:
    h = h % 360
    c = (1 - abs(2 * lightness - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = lightness - c / 2
    r = g = b = 0.0
    if h < 60:
        r, g, b = c, x, 0
    elif h < 120:
        r, g, b = x, c, 0
    elif h < 180:
        r, g, b = 0, c, x
    elif h < 240:
        r, g, b = 0, x, c
    elif h < 300:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x

    def comp(v: float) -> int:
        return min(255, round((v + m) * 255))

    return int(f"ff{comp(r):02x}{comp(g):02x}{comp(b):02x}", 16)


def _tonal(hsv: tuple[float, float, float], tone: int, neutral: bool = False) -> int:
    """Цвет из тональной шкалы: светлость, по краям — десатурация (как HCT)."""
    h, s, _ = hsv
    if neutral:
        s = NEUTRAL_SAT
    # вне зоны «живого» диапазона насыщенность опадает линейно
    if tone < 20 or tone > 80:
        s *= max(0.05, 1 - 3.5 * min(tone, 100 - tone) / 120)
    lightness = tone / 100
    return _hsl_to_rgb(h, s, lightness)


def build_tokens(seed: int, dark: bool) -> dict[str, str]:
    """Роли M3 -> CSS-токены. Ключи с префиксом --md-.

    Вторичные hue-роли (secondary/tertiary) получают сдвиг оттенка от seed,
    как в настоящем M3; error — собственная красная шкала (M3 её не динамирует).
    """
    hsv = _hex_to_hsv(seed)
    idx = 1 if dark else 0

    def role(
        role: str,
        hue_shift: float = 0,
        neutral: bool = False,
        _hsv: tuple[float, float, float] | None = None,
    ) -> str:
        src = _hsv if _hsv is not None else ((hsv[0] + hue_shift) % 360, hsv[1], hsv[2])
        return f"#{_tonal(src, ROLE_TONES[role][idx], neutral=neutral) & 0xFFFFFF:06x}"

    tokens: dict[str, str] = {}
    neutral_roles = {
        "surface",
        "on-surface",
        "surface-variant",
        "on-surface-variant",
        "outline",
        "outline-variant",
        "surface-container-lowest",
        "surface-container-low",
        "surface-container",
        "surface-container-high",
        "surface-container-highest",
    }
    for r in neutral_roles:
        tokens[f"--md-{r}"] = role(r, neutral=True)

    # хроматические роли от seed (+ сдвиг оттенка для вторичных)
    for r, shift in (
        ("primary", 0),
        ("on-primary", 0),
        ("primary-container", 0),
        ("on-primary-container", 0),
        ("secondary", -35),
        ("on-secondary", -35),
        ("secondary-container", -35),
        ("on-secondary-container", -35),
        ("tertiary", 45),
        ("on-tertiary", 45),
        ("tertiary-container", 45),
        ("on-tertiary-container", 45),
    ):
        tokens[f"--md-{r}"] = role(r, shift)

    # error — фиксированная красная шкала M3 (не из seed)
    ehsv = _hex_to_hsv(0xFFB3261E)
    for r in ("error", "on-error", "error-container", "on-error-container"):
        tokens[f"--md-{r}"] = role(r, _hsv=ehsv)

    # семантический успех — отдельная зелёная scale
    sv = _hex_to_hsv(SUCCESS_SEED)
    tokens["--md-success"] = f"#{_tonal(sv, 80 if dark else 40) & 0xFFFFFF:06x}"
    tokens["--md-success-container"] = f"#{_tonal(sv, 30 if dark else 90) & 0xFFFFFF:06x}"
    return tokens


def build_theme(dark: bool | None = None) -> dict:
    """Полный ответ /api/theme. dark=None — из системной темы Windows."""
    if dark is None:
        dark = system_dark()
    seed, src = seed_color()
    return {
        "theme": "dark" if dark else "light",
        "dark": dark,
        "seed": f"#{seed & 0xFFFFFF:06x}",
        "source": src,
        "tokens": build_tokens(seed, dark),
    }
