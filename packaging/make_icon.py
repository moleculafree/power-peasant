"""Build packaging/app.ico from packaging/app-icon.png.

If app-icon.png is missing, a simple fallback icon is drawn instead.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parent.parent
PACK = ROOT / "packaging"
SRC = PACK / "app-icon.png"
ICO = PACK / "app.ico"
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def draw_fallback() -> Image.Image:
    s = 2048
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=440, fill=(23, 26, 33, 255))
    d.rounded_rectangle((14, 14, s - 15, s - 15), radius=426, outline=(42, 48, 60, 255), width=14)
    d.rounded_rectangle((896, 470, 1152, 600), radius=28, fill=(232, 236, 243, 255))
    d.rounded_rectangle((704, 560, 1344, 1600), radius=110, outline=(232, 236, 243, 255), width=36)
    d.rounded_rectangle((760, 980, 1288, 1548), radius=70, fill=(53, 208, 127, 255))
    d.polygon([(1075, 900), (915, 1195), (1015, 1195), (975, 1450),
               (1150, 1120), (1040, 1120)], fill=(242, 163, 60, 255))
    return img.resize((256, 256), Image.LANCZOS)


def make_base() -> Image.Image:
    if SRC.exists():
        im = Image.open(SRC).convert("RGBA")
        if im.width == im.height:
            return im.resize((256, 256), Image.LANCZOS)
        return ImageOps.pad(im, (256, 256), color=(0, 0, 0, 0), method=Image.LANCZOS)
    print(f"[i] {SRC.name} not found - drawing a fallback icon")
    return draw_fallback()


def main():
    PACK.mkdir(parents=True, exist_ok=True)
    base = make_base()
    base.save(ICO, format="ICO", sizes=SIZES)
    print(f"written: {ICO} ({ICO.stat().st_size} bytes, source {SRC.name if SRC.exists() else 'fallback'})")


if __name__ == "__main__":
    main()
