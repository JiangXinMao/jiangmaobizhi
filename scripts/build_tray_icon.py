from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageOps


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "jiangmao_wallpaper" / "ui" / "assets"


def build() -> None:
    source = Image.open(ASSETS / "app_icon.png").convert("RGBA")
    grayscale = source.convert("L")
    line_alpha = ImageOps.invert(grayscale)
    line_alpha = ImageEnhance.Contrast(line_alpha).enhance(2.2)
    line_alpha = line_alpha.resize((190, 190), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    painter = ImageDraw.Draw(canvas)
    painter.rounded_rectangle((8, 8, 248, 248), radius=52, fill=(14, 19, 25, 255))
    painter.rounded_rectangle((9, 9, 247, 247), radius=51, outline=(255, 255, 255, 42), width=3)

    cat = Image.new("RGBA", (190, 190), (247, 248, 249, 0))
    cat.putalpha(line_alpha)
    canvas.alpha_composite(cat, (33, 37))
    painter.ellipse((202, 28, 226, 52), fill=(240, 91, 60, 255))

    canvas.save(ASSETS / "tray_icon.png")
    canvas.save(
        ASSETS / "tray_icon.ico",
        sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


if __name__ == "__main__":
    build()
