import sys
from pathlib import Path

import pikepdf
import pytest
from pikepdf import Dictionary, Name

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def make_font(pdf, base_font, subtype="/Type1"):
    return pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name(subtype), BaseFont=Name("/" + base_font)))


def make_pdf(content, fonts, xobjects=None, pages=1):
    """A PDF whose pages all draw `content` with the given {resource name: BaseFont}."""
    pdf = pikepdf.new()
    font_dict = Dictionary({"/" + k: make_font(pdf, v) for k, v in fonts.items()})
    for _ in range(pages):
        pdf.add_blank_page(page_size=(200, 200))
        page = pdf.pages[-1]
        page.obj.Resources = Dictionary(Font=font_dict)
        if xobjects:
            page.obj.Resources.XObject = Dictionary(xobjects)
        page.obj.Contents = pdf.make_stream(content)
    return pdf


def make_form(pdf, content, fonts):
    font_dict = Dictionary({"/" + k: make_font(pdf, v) for k, v in fonts.items()})
    return pdf.make_stream(content, Type=Name.XObject, Subtype=Name.Form,
                           BBox=[0, 0, 100, 100],
                           Resources=Dictionary(Font=font_dict))


def ops(stream_owner):
    """Content stream as a list of (operator, operands) with plain Python values."""
    return [(str(op), [_plain(o) for o in operands])
            for operands, op in pikepdf.parse_content_stream(stream_owner)]


def _plain(o):
    if isinstance(o, pikepdf.Array):
        return [_plain(x) for x in o]
    if isinstance(o, Name):
        return str(o)
    if isinstance(o, pikepdf.String):
        return bytes(o)
    try:
        return float(o)
    except (TypeError, ValueError):
        return o


@pytest.fixture
def tmp_pdf(tmp_path):
    """Save a pikepdf.Pdf to a temp file and return its path."""
    def save(pdf, name="in.pdf"):
        path = tmp_path / name
        pdf.save(path)
        return path
    return save
