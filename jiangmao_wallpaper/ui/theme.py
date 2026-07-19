from PySide6.QtCore import QRectF


DESIGN_WIDTH = 1200
DESIGN_HEIGHT = 800
HEADER_RECT = (56, 36, 1088, 46)
HERO_INFO_RECT = (832, 535, 368, 142)
RAIL_RECT = (126, 699, 948, 78)

CREAM = "#F6F0E3"
DARK = "#0E1319"
INK = "#151A20"
LINE = "#DED4C1"
MUTED = "#6C6255"
GOLD = "#D7A84E"
WHITE = "#FFFFFF"


def rect(values: tuple[int, int, int, int]) -> QRectF:
    return QRectF(*values)

