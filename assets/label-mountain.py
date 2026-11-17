#!/usr/bin/env python3
"""Overlay labels on the mountain-climber base image for the Post Sales
Agent Harness repo. The mountain is the goal, the rope is the LLM, the
harness is the Post Sale Harness, the person is the human agent.
Labels are drawn with PIL (precise text) over the generated base image.
Output: assets/mountain.png
"""
from PIL import Image, ImageDraw, ImageFont

SRC = "/home/hatch/workspace/harness-v1/repo/assets/media-generation-mountain-climber-base-0-68806e5e-11d5-4cb5-90c0-0c53e9b0050f.png"
OUT = "/home/hatch/workspace/harness-v1/repo/assets/mountain.png"

WHITE = (0xF2, 0xF2, 0xF2)
ACCENT = (0x5B, 0x9D, 0xFF)  # #5b9dff

SANS_BOLD = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"

img = Image.open(SRC).convert("RGB")
d = ImageDraw.Draw(img)
W, H = img.size
print("size:", W, H)

f_big = ImageFont.truetype(SANS_BOLD, 46)
f_med = ImageFont.truetype(SANS_BOLD, 42)
f_small = ImageFont.truetype(SANS_BOLD, 34)

def leader(x1, y1, x2, y2):
    """Steel-blue leader line with a dot at the target end."""
    d.line([(x1, y1), (x2, y2)], fill=ACCENT, width=3)
    r = 7
    d.ellipse([x2 - r, y2 - r, x2 + r, y2 + r], fill=ACCENT)

# 1. Mountain label, left side, pointing to the mountain face.
# Two lines: "Account renewal," / "expansion, and Value"
mx, my = 60, 430
d.text((mx, my), "Account renewal,", font=f_med, fill=WHITE)
d.text((mx, my + 52), "expansion, and Value", font=f_med, fill=WHITE)
# Leader from the right edge of the label to the mountain face.
leader(mx + 480, my + 55, 380, 560)

# 2. Rope label, right side upper, pointing to the rope.
rx, ry = 980, 330
d.text((rx, ry), "LLM", font=f_big, fill=WHITE)
d.text((rx, ry + 55), "the rope", font=f_small, fill=ACCENT)
# Leader from the label to the rope (rope at y~380 is around x~730).
leader(rx - 15, ry + 25, 745, 385)

# 3. Harness label, right side, pointing to the climber's harness.
hx, hy = 1000, 1060
d.text((hx, hy), "Post Sale", font=f_med, fill=WHITE)
d.text((hx, hy + 52), "Harness", font=f_med, fill=WHITE)
# Leader from the label to the harness (climber's waist, around x~900, y~1150).
leader(hx - 15, hy + 55, 905, 1150)

# 4. Person label, right side lower, pointing to the climber.
px, py = 1000, 1400
d.text((px, py), "The human", font=f_med, fill=WHITE)
d.text((px, py + 52), "agent", font=f_med, fill=WHITE)
# Leader from the label up to the climber's leg (around x~905, y~1300).
leader(px - 15, py + 25, 905, 1300)

img.save(OUT)
print("saved", OUT)
