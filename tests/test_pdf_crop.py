from __future__ import annotations

from PIL import Image, ImageDraw

from app.pdf.crop import BLOCK_GAP_PX, compose_crop, crop_manual, redact_name
from app.pdf.locate import Block, LocateResult, Rect
from app.pdf.render import SCALE

RED = (255, 0, 0)
BLUE = (0, 0, 255)
GREEN = (0, 128, 0)
BLACK = (0, 0, 0)


def _save_test_image(path, width=300, height=200):
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, 50], fill=RED)  # rouge : en-tête
    draw.rectangle([0, 150, width, height], fill=BLUE)  # bleu : ligne
    image.save(path)


def _rect(x0, top, x1, bottom) -> Rect:
    """Rectangle donné en pixels de l'image rendue, converti en points PDF."""
    return Rect(x0 / SCALE, top / SCALE, x1 / SCALE, bottom / SCALE)


def _rows_result(page_index: int = 0) -> LocateResult:
    block = Block(
        page_index=page_index,
        orientation="rows",
        header=_rect(0, 0, 100, 50),
        line=_rect(0, 150, 100, 200),
        name=_rect(10, 160, 40, 190),
    )
    return LocateResult(blocks=(block,), matched_text="TEST", candidate_used="TEST")


def test_compose_crop_stacks_header_above_line(tmp_path):
    image_path = tmp_path / "page.png"
    _save_test_image(image_path, width=300, height=200)

    composed = compose_crop([image_path], _rows_result())

    assert composed.width == 100
    assert composed.height == 100
    assert composed.getpixel((10, 5)) == RED
    assert composed.getpixel((10, composed.height - 5)) == BLUE


def test_redact_name_blacks_out_name_region_only(tmp_path):
    """L'image envoyée au modèle ne doit jamais porter le nom (voir
    _ai_notice.html) ; celle montrée en prévisualisation (compose_crop)
    reste, elle, intacte."""
    image_path = tmp_path / "page.png"
    _save_test_image(image_path, width=300, height=200)
    result = _rows_result()
    composed = compose_crop([image_path], result)

    redacted = redact_name(composed, result)

    # Zone du nom (x entre 10 et 40 px, plus une marge) : grisée sur la ligne.
    assert redacted.getpixel((20, redacted.height - 5)) == BLACK
    # En dehors de la zone du nom : la ligne garde sa couleur d'origine.
    assert redacted.getpixel((90, redacted.height - 5)) == BLUE
    # L'en-tête n'est jamais touché par le masquage du nom.
    assert redacted.getpixel((20, 5)) == RED
    # L'image montrée à l'utilisateur (compose_crop) n'est jamais modifiée.
    assert composed.getpixel((20, composed.height - 5)) == BLUE


def _save_columns_image(path):
    """Colonne des dates en rouge (x 0-60), colonne d'un voisin en vert
    (x 60-120), colonne de la personne en bleu (x 120-180) ; le nom en haut."""
    image = Image.new("RGB", (240, 200), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, 59, 199], fill=RED)
    draw.rectangle([60, 0, 119, 199], fill=GREEN)
    draw.rectangle([120, 0, 179, 199], fill=BLUE)
    image.save(path)


def _columns_result() -> LocateResult:
    block = Block(
        page_index=0,
        orientation="columns",
        header=_rect(0, 0, 60, 200),
        line=_rect(120, 0, 180, 200),
        name=_rect(125, 5, 170, 20),
    )
    return LocateResult(blocks=(block,), matched_text="TEST", candidate_used="TEST")


def test_compose_crop_puts_date_column_left_of_person_column(tmp_path):
    """Une colonne par personne : dates à gauche, la colonne de la personne
    juste à droite, sans celle du voisin entre les deux."""
    image_path = tmp_path / "page.png"
    _save_columns_image(image_path)

    composed = compose_crop([image_path], _columns_result())

    assert (composed.width, composed.height) == (120, 200)
    assert composed.getpixel((30, 100)) == RED
    assert composed.getpixel((90, 100)) == BLUE
    assert GREEN not in {composed.getpixel((x, 100)) for x in range(composed.width)}


def test_redact_name_in_a_column_blacks_out_the_name_rows_only(tmp_path):
    image_path = tmp_path / "page.png"
    _save_columns_image(image_path)
    result = _columns_result()
    composed = compose_crop([image_path], result)

    redacted = redact_name(composed, result)

    assert redacted.getpixel((61, 10)) == BLACK
    assert redacted.getpixel((119, 10)) == BLACK
    # Sous le nom : les horaires restent lisibles ; les dates jamais masquées.
    assert redacted.getpixel((90, 100)) == BLUE
    assert redacted.getpixel((30, 10)) == RED


def test_compose_crop_stacks_one_block_per_week_across_pages(tmp_path):
    """Une semaine par page : un couple en-tête + ligne par page, empilés
    dans l'ordre, séparés d'un blanc."""
    first, second = tmp_path / "page_1.png", tmp_path / "page_2.png"
    _save_test_image(first)
    Image.new("RGB", (300, 200), GREEN).save(second)
    two_weeks = LocateResult(
        blocks=(_rows_result(0).blocks[0], _rows_result(1).blocks[0]),
        matched_text="TEST",
        candidate_used="TEST",
    )

    composed = compose_crop([first, second], two_weeks)

    assert composed.height == 100 + BLOCK_GAP_PX + 100
    assert composed.getpixel((10, 5)) == RED
    assert composed.getpixel((10, 95)) == BLUE
    assert composed.getpixel((10, 100 + BLOCK_GAP_PX // 2)) == (255, 255, 255)
    assert composed.getpixel((10, 100 + BLOCK_GAP_PX + 5)) == GREEN

    redacted = redact_name(composed, two_weeks)
    assert redacted.getpixel((20, 95)) == BLACK
    assert redacted.getpixel((20, composed.height - 5)) == BLACK
    assert redacted.getpixel((20, 100 + BLOCK_GAP_PX + 5)) == GREEN


def test_crop_manual_extracts_rectangle(tmp_path):
    image_path = tmp_path / "page.png"
    _save_test_image(image_path, width=300, height=200)

    cropped = crop_manual(image_path, x=0, y=0, w=300, h=50)

    assert cropped.width == 300
    assert cropped.height == 50
    assert cropped.getpixel((10, 10)) == RED
