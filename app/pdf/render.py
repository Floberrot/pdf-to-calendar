"""Rendu des pages PDF en PNG (plan, section 5A).

scale=3 (~216 dpi) : nécessaire pour que le modèle vision lise des horaires
en petite police.
"""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium

SCALE = 3


def render_pages(pdf_path: Path, output_dir: Path) -> list[Path]:
    """Rend chaque page de `pdf_path` en PNG dans `output_dir`.

    Retourne les chemins des PNG, dans l'ordre des pages (page_1.png, ...).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        paths = []
        for index, page in enumerate(pdf):
            bitmap = page.render(scale=SCALE)
            image = bitmap.to_pil()
            path = output_dir / f"page_{index + 1}.png"
            image.save(path, format="PNG")
            paths.append(path)
        return paths
    finally:
        pdf.close()
