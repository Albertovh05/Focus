"""Generates the app icon as focus_icon.ico"""
from PIL import Image, ImageDraw
import math, os

def create_icon():
    sizes = [16, 32,48, 64, 128, 256]
    images = []

    for size in sizes:
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        cx, cy = size // 2, size // 2
        r = size // 2 - 2

        # Dark background circle
        draw.ellipse([2, 2, size - 3, size - 3], fill=(28, 28, 38, 255))

        # Outer ring
        draw.ellipse([2, 2, size - 3, size - 3], outline=(99, 179, 237, 255), width=max(1, size // 20))

        # Clock face tick marks
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

        # Hour hand (pointing to 10)
        hand_r = r * 0.45
        angle = math.radians(-60)
        draw.line([cx, cy, cx + hand_r * math.cos(angle), cy + hand_r * math.sin(angle)],
                  fill=(220, 220, 240, 255), width=max(1, size // 20))

        # Minute hand (pointing to 2)
        hand_r = r * 0.62
        angle = math.radians(60)
        draw.line([cx, cy, cx + hand_r * math.cos(angle), cy + hand_r * math.sin(angle)],
                  fill=(220, 220, 240, 255), width=max(1, size // 28))

        # Second hand (pointing to 6)
        hand_r = r * 0.68
        angle = math.radians(90)
        draw.line([cx, cy, cx + hand_r * math.cos(angle), cy + hand_r * math.sin(angle)],
                  fill=(99, 179, 237, 255), width=max(1, size // 48))

        # Center dot
        dot = max(2, size // 20)
        draw.ellipse([cx - dot, cy - dot, cx + dot, cy + dot], fill=(99, 179, 237, 255))

        images.append(img)

    out_path = os.path.join(os.path.dirname(__file__), "focus_icon.ico")
    images[0].save(out_path, format="ICO", sizes=[(s, s) for s in sizes],
                   append_images=images[1:])
    print(f"Icon saved to {out_path}")
    return out_path

if __name__ == "__main__":
    create_icon()
