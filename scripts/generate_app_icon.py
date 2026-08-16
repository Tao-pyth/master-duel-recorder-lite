from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_DIRECTORY = PROJECT_ROOT / "assets"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render(size: int) -> Image.Image:
    scale = 4
    canvas = size * scale
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def point(value: float) -> int:
        return round(value * canvas / 256)

    draw.rounded_rectangle(
        (point(12), point(12), point(244), point(244)),
        radius=point(48),
        fill="#006A6A",
    )
    left = tuple(
        (point(x), point(y))
        for x, y in ((48, 62), (100, 62), (128, 96), (100, 130), (72, 130), (72, 194), (48, 194))
    )
    right = tuple((canvas - x, y) for x, y in left)
    draw.polygon(left, fill="#D8F3EF")
    draw.polygon(right, fill="#D8F3EF")
    draw.ellipse(
        (point(99), point(117), point(157), point(175)),
        fill="#FFFFFF",
    )
    draw.ellipse(
        (point(107), point(125), point(149), point(167)),
        fill="#E23B32",
    )
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> int:
    ASSET_DIRECTORY.mkdir(parents=True, exist_ok=True)
    images = {size: render(size) for size in SIZES}
    images[256].save(ASSET_DIRECTORY / "mdrl-icon.png")
    images[256].save(
        ASSET_DIRECTORY / "mdrl.ico",
        format="ICO",
        sizes=tuple((size, size) for size in SIZES),
        append_images=[images[size] for size in SIZES[:-1]],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
