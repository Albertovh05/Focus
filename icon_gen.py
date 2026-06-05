"""Generates the app icon as focus_icon.ico (Windows) and focus_icon.png (all platforms)."""
import math
import os
import subprocess
import sys
from PIL import Image, ImageDraw


def create_icon():
    sizes = [16, 32, 48, 64, 128, 256]
    images = []

    for size in sizes:
        img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        cx, cy = size // 2, size // 2
        r = size // 2 - 2

        draw.ellipse([2, 2, size - 3, size - 3], fill=(28, 28, 38, 255))
        draw.ellipse([2, 2, size - 3, size - 3], outline=(99, 179, 237, 255),
                     width=max(1, size // 20))

        tick_r_outer = r - max(1, size // 16)
        tick_r_inner = tick_r_outer - max(1, size // 10)
        for i in range(12):
            angle = math.radians(i * 30 - 90)
            x1 = cx + tick_r_outer * math.cos(angle)
            y1 = cy + tick_r_outer * math.sin(angle)
            x2 = cx + tick_r_inner * math.cos(angle)
            y2 = cy + tick_r_inner * math.sin(angle)
            color = (99, 179, 237, 255) if i % 3 == 0 else (80, 80, 100, 255)
            draw.line([x1, y1, x2, y2], fill=color, width=max(1, size // 32))

        hand_r = r * 0.45
        angle  = math.radians(-60)
        draw.line([cx, cy, cx + hand_r * math.cos(angle), cy + hand_r * math.sin(angle)],
                  fill=(220, 220, 240, 255), width=max(1, size // 20))

        hand_r = r * 0.62
        angle  = math.radians(60)
        draw.line([cx, cy, cx + hand_r * math.cos(angle), cy + hand_r * math.sin(angle)],
                  fill=(220, 220, 240, 255), width=max(1, size // 28))

        hand_r = r * 0.68
        angle  = math.radians(90)
        draw.line([cx, cy, cx + hand_r * math.cos(angle), cy + hand_r * math.sin(angle)],
                  fill=(99, 179, 237, 255), width=max(1, size // 48))

        dot = max(2, size // 20)
        draw.ellipse([cx - dot, cy - dot, cx + dot, cy + dot], fill=(99, 179, 237, 255))

        images.append(img)

    base_dir = os.path.dirname(os.path.abspath(__file__))

    # PNG — used on macOS (tray icon, app icon fallback) and as universal format
    png_path = os.path.join(base_dir, "focus_icon.png")
    images[-1].save(png_path, format="PNG")
    print(f"PNG icon saved to {png_path}")

    # ICO — Windows only
    ico_path = os.path.join(base_dir, "focus_icon.ico")
    images[0].save(ico_path, format="ICO", sizes=[(s, s) for s in sizes],
                   append_images=images[1:])
    print(f"ICO icon saved to {ico_path}")

    # ICNS — macOS app bundle icon, built via iconutil if available
    if sys.platform == 'darwin':
        _create_icns(images, sizes, base_dir)

    return ico_path


def _create_icns(images: list, sizes: list, base_dir: str) -> None:
    """Build a .icns file from the generated images using macOS iconutil."""
    iconset_dir = os.path.join(base_dir, "focus_icon.iconset")
    os.makedirs(iconset_dir, exist_ok=True)

    # iconutil expects specific filenames at 1x and @2x densities
    density_map = {
        16:  ("icon_16x16.png",   None),
        32:  ("icon_16x16@2x.png", "icon_32x32.png"),
        64:  ("icon_32x32@2x.png", None),
        128: ("icon_128x128.png", None),
        256: ("icon_128x128@2x.png", "icon_256x256.png"),
    }

    for img, size in zip(images, sizes):
        names = density_map.get(size)
        if not names:
            continue
        for name in names:
            if name:
                img.save(os.path.join(iconset_dir, name), format="PNG")

    icns_path = os.path.join(base_dir, "focus_icon.icns")
    try:
        subprocess.run(
            ["iconutil", "-c", "icns", iconset_dir, "-o", icns_path],
            check=True, capture_output=True,
        )
        print(f"ICNS icon saved to {icns_path}")
    except Exception as e:
        print(f"[WARNING] iconutil failed ({e}); .icns not generated (PNG will be used)")
    finally:
        import shutil
        shutil.rmtree(iconset_dir, ignore_errors=True)


if __name__ == "__main__":
    create_icon()
