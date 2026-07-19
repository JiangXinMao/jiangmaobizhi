from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage


FALLBACK_ACCENT = QColor("#D7A84E")


def constrain_accent(color: QColor) -> QColor:
    hue, saturation, lightness, alpha = color.getHslF()
    if hue < 0:
        hue = FALLBACK_ACCENT.getHslF()[0]
    saturation = min(0.699, max(0.351, saturation))
    lightness = min(0.749, max(0.551, lightness))
    return QColor.fromHslF(hue, saturation, lightness, alpha)


def extract_accent(image: QImage) -> QColor:
    if image.isNull():
        return QColor(FALLBACK_ACCENT)
    sample = image.convertToFormat(QImage.Format.Format_RGB32).scaled(
        24,
        24,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    red_total = green_total = blue_total = weight_total = 0.0
    for y in range(sample.height()):
        for x in range(sample.width()):
            color = sample.pixelColor(x, y)
            _, saturation, lightness, _ = color.getHslF()
            if lightness < 0.08 or lightness > 0.94:
                continue
            weight = 0.25 + saturation * 1.8 + (1.0 - abs(lightness - 0.55)) * 0.35
            red_total += color.red() * weight
            green_total += color.green() * weight
            blue_total += color.blue() * weight
            weight_total += weight
    if weight_total == 0:
        return QColor(FALLBACK_ACCENT)
    average = QColor(
        round(red_total / weight_total),
        round(green_total / weight_total),
        round(blue_total / weight_total),
    )
    return constrain_accent(average)
