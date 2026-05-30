"""Generate the Frag placeholder app icon.

Build-time only — uses Pillow. Outputs assets/icon.png (256x256, alpha) and
assets/icon.ico (multi-resolution). The runtime app reads the PNG and does
not depend on Pillow.

Run from the project root:
    python tools/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

# Palette — matches client/ui.py and launcher.py
BG = (14, 14, 20, 255)         # #0E0E14
PRIMARY = (139, 92, 246, 255)  # #8B5CF6
ACCENT = (236, 72, 153, 255)   # #EC4899
WHITE = (244, 244, 248, 255)   # #F4F4F8


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def render_icon(size: int = 1024) -> Image.Image:
    """Render at high resolution; downsample later for crisp small sizes."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded violet tile
    radius = int(size * 0.18)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=PRIMARY)

    # White "F" mark, built from 3 rectangles, centered
    # Bounding box ~50% of canvas
    w = h = int(size * 0.52)
    x0 = (size - w) // 2
    y0 = (size - h) // 2
    stroke = int(size * 0.10)

    # Vertical stem
    draw.rectangle((x0, y0, x0 + stroke, y0 + h), fill=WHITE)
    # Top arm
    draw.rectangle((x0, y0, x0 + w, y0 + stroke), fill=WHITE)
    # Middle arm (~75% of top arm width)
    mid_y = y0 + int(h * 0.42)
    mid_w = int(w * 0.70)
    draw.rectangle((x0, mid_y, x0 + mid_w, mid_y + stroke), fill=WHITE)

    # Small pink accent — bottom-right corner notch
    notch = int(size * 0.16)
    nx = size - notch - int(size * 0.10)
    ny = size - notch - int(size * 0.10)
    draw.ellipse((nx, ny, nx + notch, ny + notch), fill=ACCENT)

    # Apply the same rounded mask cleanly (in case of edge AA artifacts)
    rounded = _rounded_mask(size, radius)
    img.putalpha(rounded)
    return img


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "assets"
    out.mkdir(parents=True, exist_ok=True)

    master = render_icon(1024)

    # Primary PNG used by the running app's window icon
    png_path = out / "icon.png"
    master.resize((256, 256), Image.LANCZOS).save(png_path, "PNG")

    # Multi-resolution ICO used by PyInstaller for the exe metadata
    ico_path = out / "icon.ico"
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    master.save(ico_path, format="ICO", sizes=sizes)

    print(f"wrote {png_path} ({png_path.stat().st_size} bytes)")
    print(f"wrote {ico_path} ({ico_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
