"""Build static/og-image.png (1200x630) for social sharing.

Uses only Pillow (already a project dependency). Run manually or via CI.
Not imported by the Streamlit app at runtime.
"""
from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
OUT = "static/og-image.png"

img = Image.new("RGB", (W, H), (15, 61, 62))
px = img.load()
top = (15, 61, 62)
bot = (20, 83, 60)
for y in range(H):
    t = y / H
    r = int(top[0] + (bot[0] - top[0]) * t)
    g = int(top[1] + (bot[1] - top[1]) * t)
    b = int(top[2] + (bot[2] - top[2]) * t)
    for x in range(W):
        px[x, y] = (r, g, b)

d = ImageDraw.Draw(img, "RGBA")


def font(size, bold=True):
    cands = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for c in cands:
        try:
            return ImageFont.truetype(c, size)
        except Exception:
            continue
    return ImageFont.load_default()


d.text((980, 70), "\u26F2", font=font(26), fill=(255, 255, 255, 28))
d.text((1040, 120), "\u26F2", font=font(26), fill=(255, 255, 255, 28))
d.text((1010, 180), "\u26F2", font=font(26), fill=(255, 255, 255, 28))
d.text((940, 150), "\u26F2", font=font(26), fill=(255, 255, 255, 28))

d.text((90, 210), "MandiIQ", font=font(96), fill=(255, 255, 255, 255))
d.rectangle([92, 320, 92 + 220, 328], fill=(120, 220, 160, 230))
d.text((92, 350), "Indian Mandi Price Intelligence", font=font(34, False), fill=(210, 224, 218, 235))
d.text((92, 410),
       "Regression-discontinuity price effects  \u2022  ML forecasts  \u2022  AI procurement",
       font=font(24, False), fill=(170, 196, 184, 220))

pts = [(92, 540), (260, 500), (430, 540), (600, 470), (770, 520), (940, 460), (1100, 500)]
for i in range(len(pts) - 1):
    d.line([pts[i], pts[i + 1]], fill=(120, 220, 160, 180), width=5)
for (x, y) in pts:
    d.ellipse([x - 5, y - 5, x + 5, y + 5], fill=(200, 255, 220, 220))

import os
os.makedirs("static", exist_ok=True)
img.save(OUT, "PNG")
print("wrote", OUT)
