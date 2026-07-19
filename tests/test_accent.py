from PySide6.QtGui import QColor, QImage

from jiangmao_wallpaper.ui.accent import FALLBACK_ACCENT, constrain_accent, extract_accent


def test_extract_accent_preserves_subject_hue_with_safe_bounds():
    image = QImage(24, 24, QImage.Format.Format_RGB32)
    image.fill(QColor("#008E9B"))

    color = extract_accent(image)
    hue, saturation, lightness, _ = color.getHslF()

    assert 0.47 <= hue <= 0.55
    assert 0.35 <= saturation <= 0.70
    assert 0.55 <= lightness <= 0.75


def test_constrain_accent_lifts_dark_low_saturation_color():
    color = constrain_accent(QColor("#252A30"))
    _, saturation, lightness, _ = color.getHslF()

    assert saturation >= 0.35
    assert lightness >= 0.55


def test_extract_accent_falls_back_for_null_image():
    assert extract_accent(QImage()).name() == FALLBACK_ACCENT.name()
