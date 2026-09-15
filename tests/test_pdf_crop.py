from __future__ import annotations

from PIL import Image, ImageDraw

from app.pdf.crop import compose_crop, crop_manual, redact_name
from app.pdf.locate import Band, LocateResult
from app.pdf.render import SCALE


def _save_test_image(path, width=300, height=200):
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, 50], fill=(255, 0, 0))  # rouge : en-tête
    draw.rectangle([0, 150, width, height], fill=(0, 0, 255))  # bleu : ligne
    image.save(path)


def test_compose_crop_stacks_header_above_line(tmp_path):
    image_path = tmp_path / "page.png"
    _save_test_image(image_path, width=300, height=200)

    result = LocateResult(
        page_index=0,
        table_left=0,
        table_right=100 / SCALE,
        header=Band(top=0, bottom=50 / SCALE),
        line=Band(top=150 / SCALE, bottom=200 / SCALE),
        matched_text="TEST",
        candidate_used="TEST",
        name_x0=10 / SCALE,
        name_x1=40 / SCALE,
    )

    composed = compose_crop(image_path, result)

    assert composed.width == 100
    assert composed.getpixel((10, 5)) == (255, 0, 0)
    assert composed.getpixel((10, composed.height - 5)) == (0, 0, 255)


def test_redact_name_blacks_out_name_region_only(tmp_path):
    """L'image envoyée au modèle ne doit jamais porter le nom (voir
    _ai_notice.html) ; celle montrée en prévisualisation (compose_crop)
    reste, elle, intacte."""
    image_path = tmp_path / "page.png"
    _save_test_image(image_path, width=300, height=200)

    result = LocateResult(
        page_index=0,
        table_left=0,
        table_right=100 / SCALE,
        header=Band(top=0, bottom=50 / SCALE),
        line=Band(top=150 / SCALE, bottom=200 / SCALE),
        matched_text="TEST",
        candidate_used="TEST",
        name_x0=10 / SCALE,
        name_x1=40 / SCALE,
    )
    composed = compose_crop(image_path, result)

    redacted = redact_name(composed, result)

    # Zone du nom (x entre 10 et 40 px, plus une marge) : grisée sur la ligne.
    assert redacted.getpixel((20, redacted.height - 5)) == (0, 0, 0)
    # En dehors de la zone du nom : la ligne garde sa couleur d'origine.
    assert redacted.getpixel((90, redacted.height - 5)) == (0, 0, 255)
    # L'en-tête n'est jamais touché par le masquage du nom.
    assert redacted.getpixel((20, 5)) == (255, 0, 0)
    # L'image montrée à l'utilisateur (compose_crop) n'est jamais modifiée.
    assert composed.getpixel((20, composed.height - 5)) == (0, 0, 255)


def test_crop_manual_extracts_rectangle(tmp_path):
    image_path = tmp_path / "page.png"
    _save_test_image(image_path, width=300, height=200)

    cropped = crop_manual(image_path, x=0, y=0, w=300, h=50)

    assert cropped.width == 300
    assert cropped.height == 50
    assert cropped.getpixel((10, 10)) == (255, 0, 0)
