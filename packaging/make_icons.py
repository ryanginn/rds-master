"""Turn rdsm-icon.svg into the icon files each platform wants.

The logo is a wide mark - 561 by 353 - and every one of these slots is square,
so it is scaled to fit and centred on a transparent square canvas rather than
stretched.

The results are committed, so building an installer does not need an SVG
renderer. Run this again only when the logo changes:

    python packaging/make_icons.py

It needs `svglib` (pure Python, no native libraries) and Pillow.
"""
from __future__ import annotations

import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SOURCE = os.path.join(ROOT, "rdsm-icon.svg")
OUT = os.path.join(HERE, "icons")

# Windows .ico wants the small sizes for the taskbar and the large ones for the
# installer and Explorer. Linux installs one PNG per hicolor directory. macOS
# .icns needs the powers of two up to 1024 for a retina display.
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
PNG_SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)
ICNS_SIZES = (16, 32, 64, 128, 256, 512, 1024)


def render_svg(path: str, width: int) -> "Image.Image":
    """The SVG at a given width, as an RGBA image."""
    from PIL import Image
    from reportlab.graphics import renderPM
    from svglib.svglib import svg2rlg

    drawing = svg2rlg(path)
    if drawing is None:
        raise SystemExit(f"{path} could not be read as SVG")
    scale = width / drawing.width
    drawing.width *= scale
    drawing.height *= scale
    drawing.scale(scale, scale)
    # reportlab renders onto an opaque background, so the mark is drawn on
    # white and that white is turned back into transparency below. The logo
    # itself has no white in it, which is what makes this safe.
    data = renderPM.drawToString(drawing, fmt="PNG", bg=0xFFFFFF)
    image = Image.open(io.BytesIO(data)).convert("RGBA")

    from PIL import ImageChops
    rgb = image.convert("RGB")
    white = Image.new("RGB", rgb.size, (255, 255, 255))
    difference = ImageChops.difference(rgb, white).convert("L")
    image.putalpha(difference.point(lambda v: 255 if v else 0))
    return image


def square(image, size: int):
    """The mark scaled to fit, centred on a transparent square."""
    from PIL import Image

    fitted = image.copy()
    fitted.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(fitted, ((size - fitted.width) // 2,
                          (size - fitted.height) // 2), fitted)
    return canvas


def write_icns(images: dict, path: str) -> None:
    """A .icns file, built here because Pillow can only write one on macOS.

    The format is a header and then one chunk per size, each a PNG with the
    type code Apple uses for it.
    """
    import struct

    types = {16: b"icp4", 32: b"icp5", 64: b"icp6", 128: b"ic07",
             256: b"ic08", 512: b"ic09", 1024: b"ic10"}
    chunks = []
    for size, code in sorted(types.items()):
        if size not in images:
            continue
        buffer = io.BytesIO()
        images[size].save(buffer, format="PNG")
        payload = buffer.getvalue()
        chunks.append(code + struct.pack(">I", len(payload) + 8) + payload)
    body = b"".join(chunks)
    with open(path, "wb") as handle:
        handle.write(b"icns" + struct.pack(">I", len(body) + 8) + body)


def main() -> int:
    if not os.path.exists(SOURCE):
        print(f"{SOURCE} is not there - nothing to convert.")
        return 1
    try:
        import PIL  # noqa: F401
        import svglib  # noqa: F401
    except ImportError:
        print("This needs Pillow and svglib:")
        print("    pip install pillow svglib")
        return 1

    os.makedirs(OUT, exist_ok=True)
    # Render once, large, then scale down from that rather than re-rendering.
    master = render_svg(SOURCE, 2048)
    print(f"read {os.path.basename(SOURCE)}: {master.width}x{master.height}")

    squares = {size: square(master, size)
               for size in sorted(set(ICO_SIZES) | set(PNG_SIZES) | set(ICNS_SIZES))}

    for size in PNG_SIZES:
        name = os.path.join(OUT, f"rdsm-{size}.png")
        squares[size].save(name)
    print(f"wrote {len(PNG_SIZES)} PNGs, {PNG_SIZES[0]} to {PNG_SIZES[-1]} pixels")

    ico = os.path.join(OUT, "rdsm.ico")
    squares[256].save(ico, sizes=[(s, s) for s in ICO_SIZES])
    print(f"wrote rdsm.ico with {len(ICO_SIZES)} sizes")

    icns = os.path.join(OUT, "rdsm.icns")
    write_icns({s: squares[s] for s in ICNS_SIZES}, icns)
    print(f"wrote rdsm.icns with {len(ICNS_SIZES)} sizes")

    # The tray draws this one directly, so keep a plain copy at a size that
    # suits a menu bar on a high-density display.
    squares[64].save(os.path.join(OUT, "tray.png"))
    print("wrote tray.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
