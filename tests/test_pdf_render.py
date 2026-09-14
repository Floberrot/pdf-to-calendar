from __future__ import annotations

from PIL import Image
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

from app.pdf.render import SCALE, render_pages

PAGE_SIZE = landscape(A4)


def _build_simple_pdf(path, pages: int = 1) -> None:
    c = canvas.Canvas(str(path), pagesize=PAGE_SIZE)
    for _ in range(pages):
        c.drawString(50, PAGE_SIZE[1] - 50, "Planning")
        c.showPage()
    c.save()


def test_render_pages_creates_one_png_per_page(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    _build_simple_pdf(pdf_path, pages=2)

    paths = render_pages(pdf_path, tmp_path / "rendered")

    assert len(paths) == 2
    assert all(p.exists() for p in paths)
    assert paths[0].name == "page_1.png"
    assert paths[1].name == "page_2.png"


def test_render_pages_uses_scale_for_resolution(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    _build_simple_pdf(pdf_path, pages=1)

    paths = render_pages(pdf_path, tmp_path / "rendered")

    width_pt, height_pt = PAGE_SIZE
    with Image.open(paths[0]) as image:
        assert abs(image.width - width_pt * SCALE) <= 1
        assert abs(image.height - height_pt * SCALE) <= 1
