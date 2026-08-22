from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT_ROOT / "assets" / "branding" / "jarvis-shortcut-icon.pdf"
PNG_OUTPUT = PROJECT_ROOT / "assets" / "branding" / "jarvis.png"
ICO_OUTPUT = PROJECT_ROOT / "assets" / "branding" / "jarvis.ico"


def isolate_logo(rendered: Image.Image) -> Image.Image:
    rgb = rendered.convert("RGB")
    luminance = rgb.convert("L")
    traversable = luminance.point(lambda value: 255 if value >= 190 else 0)
    ImageDraw.floodfill(traversable, (0, 0), 128, thresh=0)
    alpha = traversable.point(lambda value: 0 if value == 128 else 255)
    bounds = alpha.getbbox()
    if bounds is None:
        raise RuntimeError("The PDF render does not contain a visible logo.")
    logo = rgb.convert("RGBA").crop(bounds)
    logo.putalpha(alpha.crop(bounds))
    return logo


def compose_icon(logo: Image.Image, size: int = 1024) -> Image.Image:
    target = int(size * 0.88)
    scale = min(target / logo.width, target / logo.height)
    dimensions = (
        max(1, round(logo.width * scale)),
        max(1, round(logo.height * scale)),
    )
    resized = logo.resize(dimensions, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    position = ((size - resized.width) // 2, (size - resized.height) // 2)
    canvas.alpha_composite(resized, position)
    return canvas


def render_pdf(pdftoppm: Path, source: Path) -> Image.Image:
    with tempfile.TemporaryDirectory(prefix="jarvis-icon-") as temporary:
        prefix = Path(temporary) / "render"
        completed = subprocess.run(
            [
                str(pdftoppm),
                "-png",
                "-r",
                "300",
                "-singlefile",
                str(source),
                str(prefix),
            ],
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"pdftoppm failed with exit code {completed.returncode}."
            )
        with Image.open(prefix.with_suffix(".png")) as image:
            return image.copy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build JARVIS Windows icon assets.")
    parser.add_argument("--pdftoppm", required=True, type=Path)
    parser.add_argument("--source", type=Path, default=SOURCE)
    arguments = parser.parse_args(argv)
    icon = compose_icon(isolate_logo(render_pdf(arguments.pdftoppm, arguments.source)))
    PNG_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    icon.save(PNG_OUTPUT, format="PNG", optimize=True)
    icon.save(
        ICO_OUTPUT,
        format="ICO",
        sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40),
               (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
