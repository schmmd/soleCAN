#!/usr/bin/env python3
"""Generate Android launcher icons from a source PNG with a black background.

Removes the (near-)black background by alpha-keying, crops to the tractor's
bounding box, pads to square, and emits one PNG per density plus a 432x432
foreground for the adaptive icon. Run once after replacing the source image.
"""
import sys
from pathlib import Path
from PIL import Image

SRC      = Path(sys.argv[1])
OUT_RES  = Path(__file__).parent / "app" / "src" / "main" / "res"

# Pixels with R+G+B below this become fully transparent. The tractor's darkest
# shadows are still well above ~30 in this image, so a low threshold cleanly
# kills the background without eroding the silhouette.
BLACK_THRESH = 25

LAUNCHER_SIZES = {
    "mipmap-mdpi":    48,
    "mipmap-hdpi":    72,
    "mipmap-xhdpi":   96,
    "mipmap-xxhdpi": 144,
    "mipmap-xxxhdpi":192,
}
# Adaptive-icon foreground: 108dp canvas at xxxhdpi = 432px. The visible
# "safe" zone is the inner 66dp (264px) — anything outside may be masked.
ADAPTIVE_FG_SIZE = 432
ADAPTIVE_SAFE    = 264

def alpha_key_black(img: Image.Image) -> Image.Image:
    img = img.convert("RGBA")
    px = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            r, g, b, _ = px[x, y]
            if r + g + b < BLACK_THRESH:
                px[x, y] = (0, 0, 0, 0)
    return img

def crop_to_subject(img: Image.Image, pad: int = 8) -> Image.Image:
    bbox = img.getbbox()
    if bbox is None:
        return img
    l, t, r, b = bbox
    l = max(0, l - pad); t = max(0, t - pad)
    r = min(img.width, r + pad); b = min(img.height, b + pad)
    return img.crop((l, t, r, b))

def pad_square(img: Image.Image) -> Image.Image:
    side = max(img.width, img.height)
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    return out

def make_adaptive_fg(square: Image.Image) -> Image.Image:
    """Centers the cropped tractor inside the 108dp safe zone."""
    canvas = Image.new("RGBA", (ADAPTIVE_FG_SIZE, ADAPTIVE_FG_SIZE), (0, 0, 0, 0))
    fit = square.resize((ADAPTIVE_SAFE, ADAPTIVE_SAFE), Image.LANCZOS)
    off = (ADAPTIVE_FG_SIZE - ADAPTIVE_SAFE) // 2
    canvas.paste(fit, (off, off), fit)
    return canvas

def main() -> None:
    src = Image.open(SRC)
    keyed   = alpha_key_black(src)
    cropped = crop_to_subject(keyed)
    square  = pad_square(cropped)

    for dirname, size in LAUNCHER_SIZES.items():
        out_dir = OUT_RES / dirname
        out_dir.mkdir(parents=True, exist_ok=True)
        resized = square.resize((size, size), Image.LANCZOS)
        resized.save(out_dir / "ic_launcher.png")
        resized.save(out_dir / "ic_launcher_round.png")
        print(f"  wrote {dirname}/ic_launcher.png  ({size}x{size})")

    fg = make_adaptive_fg(square)
    fg_dir = OUT_RES / "mipmap-xxxhdpi"
    fg.save(fg_dir / "ic_launcher_foreground.png")
    print(f"  wrote mipmap-xxxhdpi/ic_launcher_foreground.png  ({ADAPTIVE_FG_SIZE}x{ADAPTIVE_FG_SIZE})")

if __name__ == "__main__":
    main()
