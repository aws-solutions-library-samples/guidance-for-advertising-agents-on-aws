#!/usr/bin/env python3
"""Generate display-ad test fixtures for reference-seller `display_300x250`."""
import os
from PIL import Image, ImageDraw, ImageFont

#: The directory the fixtures already live in -- this script's own. Was the cwd-relative literal
#: `"fixtures"`, which writes to a different place depending on where the script is run from, and would
#: silently create a stray `fixtures/` tree rather than regenerating the checked-in assets.
OUT = os.path.dirname(os.path.abspath(__file__))
os.makedirs(OUT, exist_ok=True)

# --- WCAG contrast helpers -------------------------------------------------
def _lin(c):
    c /= 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

def luminance(rgb):
    r, g, b = (_lin(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b

def contrast(fg, bg):
    l1, l2 = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)

def measured_contrast(img):
    """Contrast the way the GOVERNANCE EVALUATOR measures it: the two most frequent colours after a
    thumbnail to 150x150.

    Mirrors `main._measure_dominant_contrast` deliberately, because asserting the DESIGN contrast
    (`contrast(DARK, CREAM)`) proves nothing about what the evaluator will report. The first 728x90
    leaderboard here designed to 15.19:1 and was measured at 1.04:1 — its text covered so little of a
    wide unit that the second most frequent colour was a near-cream anti-alias shade rather than the ink.
    A real number carrying a false implication is worse than no number, so the fixture is now checked
    against the method that will actually read it.
    """
    sample = img.copy()
    sample.thumbnail((150, 150))
    colors = sample.getcolors(maxcolors=256 * 256 * 256)
    if not colors or len(colors) < 2:
        return None
    colors.sort(key=lambda item: item[0], reverse=True)
    (_, bg), (_, fg) = colors[0], colors[1]
    return contrast(fg, bg)

# --- fonts (macOS first) ---------------------------------------------------
CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

_resolved = None

def font(size):
    global _resolved
    if _resolved is None:
        for p in CANDIDATES:
            if os.path.exists(p):
                _resolved = p
                print(f"[font] using {p}")
                break
        else:
            _resolved = ""
            print("[font] no system font found; using Pillow default")
    if _resolved:
        return ImageFont.truetype(_resolved, size)
    return ImageFont.load_default(size=size)  # Pillow >= 10.1, scalable

def centered(draw, y, text, f, fill, width):
    w = draw.textbbox((0, 0), text, font=f)[2]
    draw.text(((width - w) // 2, y), text, font=f, fill=fill)

# --- shared banner shell ---------------------------------------------------
BRAND = "NORTHWIND COFFEE CO."

def banner(size, bg, fg, accent, lines, cta, cta_bg, cta_fg, border):
    W, H = size
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W - 1, H - 1], outline=border, width=2)
    centered(d, 22, BRAND, font(15), accent, W)
    y = 74
    for text, sz in lines:
        centered(d, y, text, font(sz), fg, W)
        y += sz + 12
    bw, bh = 168, 40
    bx, by = (W - bw) // 2, H - 62
    d.rectangle([bx, by, bx + bw, by + bh], fill=cta_bg)
    f = font(17)
    tb = d.textbbox((0, 0), cta, font=f)
    d.text((bx + (bw - tb[2]) / 2, by + (bh - tb[3]) / 2 - 2), cta, font=f, fill=cta_fg)
    return img

def leaderboard(size, bg, fg, accent, headline, sub, cta, cta_bg, cta_fg, border):
    """A horizontal banner: brand and copy on the left, CTA on the right.

    A separate shell rather than `banner(size=...)` because `banner` is laid out for a tall unit -- copy
    starts at y=74 and the CTA sits at H-62, which at 90px high puts the copy off the bottom edge and the
    CTA above the top. Passing a wide size to it produces an image whose own text is not visible, and the
    evaluator would then measure a blank rectangle and honestly report low contrast for the wrong reason.
    """
    W, H = size
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)

    # A solid dark brand panel across the left third.
    #
    # Not decoration: the evaluator approximates foreground/background from the two most frequent
    # colours, and on a wide unit text alone covers too little area for the ink to BE the second colour.
    # Without a real block of it, a fixture designed to 15:1 measures 1.04:1 (see `measured_contrast`).
    # A dark panel is also a normal leaderboard treatment, so this is not a shape invented to game the
    # measurement — it makes the image genuinely two-tone, which is what the method assumes.
    panel_w = int(W * 0.34)
    d.rectangle([0, 0, panel_w, H - 1], fill=fg)
    d.rectangle([0, 0, W - 1, H - 1], outline=border, width=2)

    # Brand reversed out of the panel.
    f_brand = font(15)
    tb = d.textbbox((0, 0), BRAND, font=f_brand)
    d.text(((panel_w - tb[2]) / 2, (H - tb[3]) / 2 - 4), BRAND, font=f_brand, fill=bg)

    bw, bh = 150, 34
    bx, by = W - bw - 16, (H - bh) // 2

    # Copy sits between the panel and the CTA. The headline is fitted to that gap rather than trusted to
    # fit: at font 22 the first version of this ran underneath the CTA block, which looked like a
    # rendering bug in a fixture whose whole job is to be a known-good asset.
    text_x = panel_w + 20
    available = bx - 14 - text_x
    headline_font = None
    for size in range(22, 11, -1):
        candidate = font(size)
        if d.textbbox((0, 0), headline, font=candidate)[2] <= available:
            headline_font = candidate
            break
    assert headline_font is not None, (
        f"headline {headline!r} does not fit {available}px even at the smallest size; shorten the copy"
    )
    d.text((text_x, 22), headline, font=headline_font, fill=fg)

    sub_font = font(13)
    assert d.textbbox((0, 0), sub, font=sub_font)[2] <= available, (
        f"sub-line {sub!r} does not fit {available}px; shorten the copy"
    )
    d.text((text_x, 52), sub, font=sub_font, fill=fg)

    d.rectangle([bx, by, bx + bw, by + bh], fill=cta_bg)
    f = font(15)
    tb = d.textbbox((0, 0), cta, font=f)
    d.text((bx + (bw - tb[2]) / 2, by + (bh - tb[3]) / 2 - 2), cta, font=f, fill=cta_fg)
    return img

def save(img, name):
    path = os.path.join(OUT, name)
    img.save(path, "PNG", optimize=True)
    print(f"{name:26} {img.size[0]}x{img.size[1]}  {os.path.getsize(path)/1024:.1f} KB")

# --- palettes --------------------------------------------------------------
DARK, CREAM, GREEN = (26, 32, 30), (247, 245, 240), (22, 122, 88)
LOW_BG, LOW_FG = (255, 255, 255), (232, 232, 232)  # ratio ~1.22
# Push LOW_FG toward (248, 248, 248) for a harsher failure — but see the
# OCR caveat: if nothing is extractable, a text-region contrast check may
# never fire at all.

CLEAN_LINES = [("Single-Origin", 30), ("Roasted Weekly", 30),
               ("Free shipping over $35", 15)]

# 1. clean ------------------------------------------------------------------
save(banner((300, 250), CREAM, DARK, GREEN, CLEAN_LINES,
            "Shop Now", GREEN, (255, 255, 255), GREEN), "clean_banner.png")

# 2. urgency claim ----------------------------------------------------------
save(banner((300, 250), CREAM, (176, 26, 26), (176, 26, 26),
            [("SALE ENDS IN", 26), ("2 HOURS!", 34), ("Only 3 left in stock!", 19)],
            "Buy Before It's Gone", (176, 26, 26), (255, 255, 255), (176, 26, 26)),
     "urgency_claim_banner.png")

# 3. low contrast -----------------------------------------------------------
save(banner((300, 250), LOW_BG, LOW_FG, LOW_FG, CLEAN_LINES,
            "Shop Now", LOW_BG, LOW_FG, LOW_FG), "low_contrast_banner.png")

# 4. wrong size -------------------------------------------------------------
save(banner((320, 250), CREAM, DARK, GREEN, CLEAN_LINES,
            "Shop Now", GREEN, (255, 255, 255), GREEN), "wrong_size_banner.png")

# 5. clean leaderboard ------------------------------------------------------
# A SECOND format with a real asset, so the journey's Creative scan animates for more than one card.
# Same three features apply unchanged -- they are all image measurements, and this is still an image.
_leaderboard = leaderboard((728, 90), CREAM, DARK, GREEN,
                           "Roasted Weekly, Shipped Free", "Single-origin beans from independent farms",
                           "Shop Now", GREEN, (255, 255, 255), GREEN)
save(_leaderboard, "clean_leaderboard.png")

# --- assertions: the fixtures must actually be what they claim -------------
good = contrast(DARK, CREAM)
bad = contrast(LOW_FG, LOW_BG)
print(f"\nclean/wrong-size body text contrast: {good:.2f}:1  (AA pass)")
print(f"low_contrast body text contrast:     {bad:.2f}:1  (AA fail)")
assert good >= 4.5, "clean fixture should pass AA"
assert bad < 1.5, "low-contrast fixture is not low-contrast enough"

# The leaderboard, checked the way the EVALUATOR will read it rather than the way it was designed.
#
# `contrast(DARK, CREAM)` is 15.19:1 and says nothing about what gets reported: the first version of this
# fixture measured 1.04:1, because on a wide unit the ink covered too little area to be the second most
# frequent colour. This assertion is the one that would have caught it.
_measured = measured_contrast(_leaderboard)
print(f"leaderboard contrast AS THE EVALUATOR MEASURES IT: {_measured:.2f}:1  (AA pass)")
assert _measured is not None, "leaderboard is not two-tone enough to measure"
assert _measured >= 4.5, (
    f"the evaluator would report {_measured:.2f}:1 for the leaderboard, which reads as an accessibility "
    "failure the design does not have. Increase the ink coverage rather than relaxing this."
)

# The evaluator checks measured pixels against the FORMAT's required dimensions, so the fixture's own
# size is part of its contract. Asserted here so a layout edit cannot silently resize it.
from PIL import Image as _Image  # noqa: E402  (assertion-only import, kept beside its use)

for _name, _expected in (
    ("clean_banner.png", (300, 250)),
    ("urgency_claim_banner.png", (300, 250)),
    ("low_contrast_banner.png", (300, 250)),
    ("wrong_size_banner.png", (320, 250)),  # deliberately NOT 300x250 -- that is what it tests
    ("clean_leaderboard.png", (728, 90)),
):
    _actual = _Image.open(os.path.join(OUT, _name)).size
    assert _actual == _expected, f"{_name} is {_actual}, expected {_expected}"
print("\nall fixture dimensions match their declared formats")