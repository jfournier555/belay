#!/usr/bin/env python3
"""Belay banner v2: carabiner logo to the left of 'Belay', group centered.
Red rock base + white carabiner logo + 'Belay' + 'A post sales AI harness'.
White text/logo with a dark drop shadow for readability over sky/clouds.
Output: assets/belay-banner.jpg (and .png intermediate).
"""
import math
from PIL import Image, ImageDraw, ImageFont, ImageFilter

SRC = "/home/hatch/workspace/harness-v1/repo/assets/media-generation-red-rock-banner-base-v2-0-0898ef46-020b-4c2a-bed8-30736ebc13d3.png"
OUT_PNG = "/home/hatch/workspace/harness-v1/repo/assets/belay-banner.png"
OUT_JPG = "/home/hatch/workspace/harness-v1/repo/assets/belay-banner.jpg"

SANS_BOLD = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
WHITE = (255, 255, 255, 255)
BLACK_SHADOW = (0, 0, 0, 180)

img = Image.open(SRC).convert("RGB")
W, H = img.size
print("size:", W, H)

f_title = ImageFont.truetype(SANS_BOLD, 150)
f_sub = ImageFont.truetype(SANS_BOLD, 54)

cx = W // 2
title_y = 95   # anchor "ma": top of ascenders
sub_y = 300

# --- Measure "Belay" so the [logo + Belay] group can be centered. ---
probe = ImageDraw.Draw(img)
text_w = probe.textlength("Belay", font=f_title)
print("Belay width:", text_w)

# --- Carabiner logo, drawn on its own transparent layer. ---
# D-shape: tall oval, taller than wide. Gate on the upper-left.
# Steel-blue to read as a distinct logo mark against the white title.
LOGO_W, LOGO_H = 120, 160
ACCENT = (0x5B, 0x9D, 0xFF, 255)
logo = Image.new("RGBA", (LOGO_W, LOGO_H), (0, 0, 0, 0))
ld = ImageDraw.Draw(logo)
ccx, ccy = LOGO_W // 2, LOGO_H // 2
rx, ry = 48, 65
STROKE = 14
# Arc: start 260 -> clockwise through 0, 90, 180 -> end 190.
# Leaves a 70-degree gap (190 to 260) in the upper-left for the gate.
ld.arc([(ccx - rx, ccy - ry), (ccx + rx, ccy + ry)],
       start=260, end=190, fill=ACCENT, width=STROKE)

def pt(deg):
    a = math.radians(deg)
    return (ccx + rx * math.cos(a), ccy + ry * math.sin(a))

gate_a = pt(190)
gate_b = pt(260)
ld.line([gate_a, gate_b], fill=ACCENT, width=STROKE)

# --- Drop shadow for the logo: black copy, blurred, composited first. ---
logo_shadow = Image.new("RGBA", (LOGO_W, LOGO_H), (0, 0, 0, 0))
lsd = ImageDraw.Draw(logo_shadow)
lsd.arc([(ccx - rx, ccy - ry), (ccx + rx, ccy + ry)],
        start=260, end=190, fill=BLACK_SHADOW, width=STROKE)
lsd.line([gate_a, gate_b], fill=BLACK_SHADOW, width=STROKE)
logo_shadow = logo_shadow.filter(ImageFilter.GaussianBlur(6))

# --- Layout: [logo] gap [Belay], group centered on cx. ---
GAP = 45
group_w = LOGO_W + GAP + text_w
group_left = cx - group_w / 2
logo_x = int(group_left)
text_x = int(group_left + LOGO_W + GAP)

# Vertical: center the logo on the title's vertical center.
# Title bbox (anchor ma at title_y): measure with textbbox.
bbox = probe.textbbox((0, 0), "Belay", font=f_title, anchor="ma")
# bbox is relative to (0,0) with anchor ma; top is bbox[1], bottom bbox[3].
title_top = title_y + bbox[1]
title_bottom = title_y + bbox[3]
title_cy = (title_top + title_bottom) / 2
logo_y = int(title_cy - LOGO_H / 2)
print("title bbox top/bottom/cy:", title_top, title_bottom, title_cy)
print("logo at:", logo_x, logo_y, "text_x:", text_x)

# --- Composite: shadow layer (logo + text), then crisp white on top. ---
base = img.convert("RGBA")

shadow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
shadow_layer.paste(logo_shadow, (logo_x + 5, logo_y + 6), logo_shadow)
sd = ImageDraw.Draw(shadow_layer)
sd.text((text_x + 5, title_y + 6), "Belay", font=f_title, fill=BLACK_SHADOW, anchor="la")
sd.text((cx + 4, sub_y + 4), "A post sales AI harness", font=f_sub, fill=BLACK_SHADOW, anchor="ma")
shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(6))
base = Image.alpha_composite(base, shadow_layer)

base.paste(logo, (logo_x, logo_y), logo)
d = ImageDraw.Draw(base)
d.text((text_x, title_y), "Belay", font=f_title, fill=WHITE, anchor="la")
d.text((cx, sub_y), "A post sales AI harness", font=f_sub, fill=WHITE, anchor="ma")

base.convert("RGB").save(OUT_PNG)
base.convert("RGB").save(OUT_JPG, quality=85, optimize=True)
import os
print("saved", OUT_PNG, os.path.getsize(OUT_PNG))
print("saved", OUT_JPG, os.path.getsize(OUT_JPG))
