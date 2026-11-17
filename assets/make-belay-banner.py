#!/usr/bin/env python3
"""Overlay the Belay logo text on the red rock banner.
'Belay' large, 'A post sales AI harness' below it, centered in the sky.
White text with a dark drop shadow for readability over sky/clouds.
Output: assets/belay-banner.png
"""
from PIL import Image, ImageDraw, ImageFont, ImageFilter

SRC = "/home/hatch/workspace/harness-v1/repo/assets/media-generation-red-rock-banner-base-0-efbb0e31-4ee4-49f5-8384-eb4ff4cd75e6.png"
OUT = "/home/hatch/workspace/harness-v1/repo/assets/belay-banner.png"

SANS_BOLD = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"

img = Image.open(SRC).convert("RGB")
W, H = img.size
print("size:", W, H)

# Build the text on a transparent overlay so we can blur just the shadow.
overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
d = ImageDraw.Draw(overlay)

f_title = ImageFont.truetype(SANS_BOLD, 150)
f_sub = ImageFont.truetype(SANS_BOLD, 54)

cx = W // 2
title_y = 95
sub_y = 270

# Drop shadow: draw text in black on the overlay, blur it, composite, then
# draw crisp white text on top.
shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
ds = ImageDraw.Draw(shadow)
ds.text((cx + 5, title_y + 6), "Belay", font=f_title, fill=(0, 0, 0, 180), anchor="ma")
ds.text((cx + 4, sub_y + 4), "A post sales AI harness", font=f_sub, fill=(0, 0, 0, 180), anchor="ma")
shadow = shadow.filter(ImageFilter.GaussianBlur(6))
img = Image.alpha_composite(img.convert("RGBA"), shadow)

d = ImageDraw.Draw(img)
d.text((cx, title_y), "Belay", font=f_title, fill=(255, 255, 255, 255), anchor="ma")
d.text((cx, sub_y), "A post sales AI harness", font=f_sub, fill=(255, 255, 255, 255), anchor="ma")

img.convert("RGB").save(OUT)
print("saved", OUT)
