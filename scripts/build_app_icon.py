from pathlib import Path

from PIL import Image, ImageDraw


SOURCE = Path(r"C:\Users\Administrator\AppData\Local\Temp\codex-clipboard-31cc0282-ed21-4a5c-a591-27b50e6091be.png")
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "jiangmao_wallpaper" / "ui" / "assets"


def main() -> None:
    image = Image.open(SOURCE).convert("RGBA")
    side = min(image.size)
    left = (image.width - side) // 2
    top = (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side))

    mask = Image.new("L", (side, side), 0)
    radius = round(side * 0.16)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, side - 1, side - 1), radius=radius, fill=255)
    image.putalpha(mask)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    png_path = OUTPUT_DIR / "app_icon.png"
    ico_path = OUTPUT_DIR / "app_icon.ico"
    image.save(png_path, optimize=True)
    image.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(png_path)
    print(ico_path)


if __name__ == "__main__":
    main()
