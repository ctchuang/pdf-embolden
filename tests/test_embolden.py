import re

import pikepdf
import pytest
from pikepdf import Dictionary, Name

import embolden
from conftest import make_font, make_form, make_pdf, ops

CM = {"F1": "ABCDEF+CMR10"}
CM_AND_HELV = {"F1": "ABCDEF+CMR10", "F2": "Helvetica"}
DEFAULT = re.compile(embolden.DEFAULT_FONT_PATTERN)


def rewrite(content, fonts=CM, width=0.15, pattern=DEFAULT):
    """Run rewrite_content on a one-page PDF; return parsed ops, or None if unchanged."""
    pdf = make_pdf(content, fonts)
    page = pdf.pages[0]
    data = embolden.rewrite_content(page, page.obj.Resources, width, pattern)
    if data is None:
        return None
    return ops(pdf.make_stream(data))


def names(op_list):
    return [op for op, _ in op_list]


def is_subsequence(needle, haystack):
    it = iter(haystack)
    return all(item in it for item in needle)


def stroke_colour_before(op_list, target_index):
    """(operator, operands) of the last stroke-colour operator before target_index."""
    for op, vals in reversed(op_list[:target_index]):
        if op in ("G", "RG", "K", "SC", "SCN"):
            return op, vals
    return None


def last_before(op_list, target_index, op_name):
    """Operands of the last `op_name` before position target_index."""
    for op, vals in reversed(op_list[:target_index]):
        if op == op_name:
            return vals
    return None


def index_of(op_list, op_name, start=0):
    for i in range(start, len(op_list)):
        if op_list[i][0] == op_name:
            return i
    raise ValueError(op_name)


# --- font names and matching ------------------------------------------------

@pytest.mark.parametrize("name", [
    "CMR10", "CMMI10", "CMMIB10", "CMSY10", "CMEX10", "CMBX12", "CMTI10",
    "CMSL10", "CMTT10", "CMCSC10", "LMRoman10-Regular", "LMRomanSlant10-Bold",
    "LMRomanCaps10-Regular", "LMMono10-Regular", "MSAM10", "MSBM10",
])
def test_default_pattern_matches_tex_fonts(name):
    assert DEFAULT.match(name)


@pytest.mark.parametrize("name", [
    "Helvetica", "Times-Roman", "TimesNewRomanPSMT", "ArialMT",
    "CMRoman", "XCMR10", "Inconsolata-Identity-H", "DejaVuSans",
    "SFProText-Regular", "txtt", "LCIRCLE10",
])
def test_default_pattern_skips_other_fonts(name):
    assert not DEFAULT.match(name)


@pytest.mark.parametrize("name", [
    "SFRM1000", "SFTI1095", "SFBX1200", "LMMathItalic10-Italic",
    "LMMathSymbols10-Italic", "LMMathExtension10-Regular", "CMSS10", "CMSSBX10",
])
def test_default_pattern_matches_more_tex_fonts(name):
    assert DEFAULT.match(name)


@pytest.mark.parametrize("name", [
    "TeXGyreTermesX-Regular", "TeXGyreTermesX-Bold-Identity-H", "TeXGyreTermes-Italic",
    "NimbusRomNo9L-Regu", "NimbusRoman-Regular", "STIXGeneral-Regular", "STIXTwoText",
    "NewTXMI", "NewTXBMI7", "txmiaX", "txbmiaX", "txsya", "txsys", "txexas", "txr",
])
def test_default_pattern_matches_times_family(name):
    assert DEFAULT.match(name)


def test_base_font_name_strips_subset_tag():
    pdf = pikepdf.new()
    assert embolden.base_font_name(make_font(pdf, "ABCDEF+CMR10")) == "CMR10"
    assert embolden.base_font_name(make_font(pdf, "CMR10")) == "CMR10"
    # Only a six-capital tag followed by + counts as a subset prefix.
    assert embolden.base_font_name(make_font(pdf, "ABC+CMR10")) == "ABC+CMR10"


def test_base_font_name_without_basefont():
    assert embolden.base_font_name(Dictionary(Type=Name.Font)) == ""


def test_matching_fonts():
    pdf = make_pdf(b"", CM_AND_HELV)
    res = pdf.pages[0].obj.Resources
    assert embolden.matching_fonts(res, DEFAULT) == {Name("/F1")}
    assert embolden.matching_fonts(res, re.compile("Helv")) == {Name("/F2")}
    assert embolden.matching_fonts(None, DEFAULT) == set()
    assert embolden.matching_fonts(Dictionary(), DEFAULT) == set()


def test_page_resources_inherited_from_page_tree():
    pdf = make_pdf(b"", CM)
    page = pdf.pages[0].obj
    res = page.Resources
    del page["/Resources"]
    pdf.Root.Pages.Resources = res
    assert embolden.page_resources(page) == res


def test_page_resources_missing():
    pdf = make_pdf(b"", CM)
    del pdf.pages[0].obj["/Resources"]
    assert embolden.page_resources(pdf.pages[0].obj) is None


# --- content stream rewriting -----------------------------------------------

def test_no_matching_font_returns_none():
    assert rewrite(b"BT /F2 12 Tf (Hi) Tj ET", {"F2": "Helvetica"}) is None


def test_basic_text_gets_fill_and_stroke():
    out = rewrite(b"BT /F1 12 Tf 10 10 Td (Hello) Tj ET", width=0.2)
    tj = index_of(out, "Tj")
    assert last_before(out, tj, "Tr") == [2]
    assert last_before(out, tj, "w") == [0.2]
    assert last_before(out, tj, "j") == [1]
    assert last_before(out, tj, "J") == [1]
    assert last_before(out, tj, "d") == [[], 0]
    assert last_before(out, tj, "G") == [0]  # default black fill mirrored


def test_original_operators_preserved_in_order():
    src = (b"1 0 0 1 0 0 cm 0.5 g BT /F1 12 Tf 10 10 Td [(A) -20 (B)] TJ "
           b"0 -14 TD (C) ' 1 2 (D) \" ET 0 0 m 10 10 l S")
    pdf = make_pdf(src, CM)
    original = ops(pdf.pages[0])
    out = rewrite(src)
    assert is_subsequence(original, out)


def test_text_layer_strings_unchanged():
    src = b"BT /F1 12 Tf 10 10 Td (Hello) Tj [(W) 50 (orld)] TJ ET"
    out = rewrite(src)
    shown = [vals for op, vals in out if op in ("Tj", "TJ", "'", '"')]
    assert shown == [[b"Hello"], [[b"W", 50.0, b"orld"]]]


def test_no_position_operators_added():
    src = b"BT /F1 12 Tf 10 10 Td (A) Tj 5 0 Td (B) Tj ET"
    out = rewrite(src)
    for op in ("Td", "TD", "Tm", "T*", "cm", "Tc", "Tw", "Tz", "TL", "Ts"):
        assert names(out).count(op) == src.decode().split().count(op)


def test_rgb_fill_mirrored_to_stroke():
    out = rewrite(b"0.1 0.2 0.3 rg BT /F1 12 Tf (A) Tj ET")
    assert last_before(out, index_of(out, "Tj"), "RG") == pytest.approx([0.1, 0.2, 0.3])


def test_cmyk_fill_mirrored_to_stroke():
    out = rewrite(b"0 0.5 0.5 0 k BT /F1 12 Tf (A) Tj ET")
    assert last_before(out, index_of(out, "Tj"), "K") == pytest.approx([0, 0.5, 0.5, 0])


def test_colour_space_fill_mirrored_to_stroke():
    out = rewrite(b"/DeviceRGB cs 0 1 0 sc BT /F1 12 Tf (A) Tj ET")
    tj = index_of(out, "Tj")
    assert last_before(out, tj, "CS") == ["/DeviceRGB"]
    assert last_before(out, tj, "SC") == [0, 1, 0]


def test_fill_change_inside_text_is_mirrored():
    out = rewrite(b"BT /F1 12 Tf (A) Tj 1 0 0 rg (B) Tj ET")
    second_tj = index_of(out, "Tj", index_of(out, "Tj") + 1)
    assert last_before(out, second_tj, "RG") == [1, 0, 0]


def test_stroke_state_restored_after_text():
    src = b"2 w 0 J 0 j [3 1] 0 d 0 0 1 RG BT /F1 12 Tf (A) Tj ET 0 0 m 10 10 l S"
    out = rewrite(src)
    s = index_of(out, "S")
    assert last_before(out, s, "Tr") == [0]
    assert last_before(out, s, "w") == [2]
    assert last_before(out, s, "J") == [0]
    assert last_before(out, s, "j") == [0]
    assert last_before(out, s, "d") == [[3, 1], 0]
    assert last_before(out, s, "RG") == [0, 0, 1]


def test_stroke_colour_space_restored_after_text():
    src = b"/DeviceCMYK CS 0 0 0 1 SC BT /F1 12 Tf (A) Tj ET 0 0 m S"
    out = rewrite(src)
    s = index_of(out, "S")
    assert last_before(out, s, "CS") == ["/DeviceCMYK"]
    assert last_before(out, s, "SC") == [0, 0, 0, 1]


def test_default_state_restored_when_never_set():
    out = rewrite(b"BT /F1 12 Tf (A) Tj ET 0 0 m S")
    s = index_of(out, "S")
    assert last_before(out, s, "w") == [1]
    assert last_before(out, s, "G") == [0]


def test_non_matching_text_is_untouched():
    src = b"BT /F2 12 Tf (A) Tj ET BT /F1 12 Tf (B) Tj ET"
    out = rewrite(src, CM_AND_HELV)
    first_et = index_of(out, "ET")
    assert "Tr" not in names(out[:first_et])


def test_font_switch_inside_text_toggles_mode():
    out = rewrite(b"BT /F1 12 Tf (A) Tj /F2 12 Tf (B) Tj /F1 12 Tf (C) Tj ET",
                  CM_AND_HELV)
    tjs = [i for i, (op, _) in enumerate(out) if op == "Tj"]
    assert [last_before(out, i, "Tr") for i in tjs] == [[2], [0], [2]]


def test_font_set_before_bt_carries_over():
    out = rewrite(b"BT /F1 12 Tf (A) Tj ET BT (B) Tj ET")
    second_bt = index_of(out, "BT", 1)
    tj = index_of(out, "Tj", second_bt)
    assert last_before(out, tj, "Tr") == [2]


@pytest.mark.parametrize("mode", [1, 3, 7])
def test_existing_render_mode_left_alone(mode):
    src = b"BT %d Tr /F1 12 Tf (A) Tj ET" % mode
    out = rewrite(src)
    assert [vals for op, vals in out if op == "Tr"][:1] == [[mode]]
    assert "w" not in names(out)


def test_render_mode_change_inside_text_disables():
    out = rewrite(b"BT /F1 12 Tf (A) Tj 3 Tr (B) Tj 0 Tr (C) Tj ET")
    tjs = [i for i, (op, _) in enumerate(out) if op == "Tj"]
    assert [last_before(out, i, "Tr") for i in tjs] == [[2], [3], [2]]


def test_document_stroke_change_inside_text_reasserted():
    out = rewrite(b"BT /F1 12 Tf (A) Tj 5 w 1 0 0 RG (B) Tj ET", width=0.15)
    second_tj = index_of(out, "Tj", index_of(out, "Tj") + 1)
    assert last_before(out, second_tj, "w") == [0.15]
    # The effective stroke colour mirrors the fill (black), not the document's red RG.
    assert stroke_colour_before(out, second_tj) == ("G", [0])


def test_document_stroke_change_inside_text_restored_after():
    out = rewrite(b"BT /F1 12 Tf (A) Tj 5 w (B) Tj ET 0 0 m S")
    assert last_before(out, index_of(out, "S"), "w") == [5]


def test_q_Q_restores_tracked_state():
    # The width set inside q/Q must not leak into the restore after the later text.
    src = b"q 4 w Q BT /F1 12 Tf (A) Tj ET 0 0 m S"
    out = rewrite(src)
    assert last_before(out, index_of(out, "S"), "w") == [1]


def test_q_Q_restores_font_match():
    src = b"BT /F2 12 Tf ET q BT /F1 12 Tf ET Q BT (A) Tj ET"
    out = rewrite(src, CM_AND_HELV)
    last_bt = max(i for i, (op, _) in enumerate(out) if op == "BT")
    assert "Tr" not in names(out[last_bt:])


def test_unbalanced_Q_does_not_crash():
    assert rewrite(b"Q BT /F1 12 Tf (A) Tj ET") is not None


def test_inline_image_survives():
    src = b"BT /F1 12 Tf (A) Tj ET q 1 0 0 1 0 0 cm BI /W 1 /H 1 /CS /G /BPC 8 ID \x80 EI Q"
    out = rewrite(src)
    assert "INLINE IMAGE" in names(out)


def test_width_is_configurable():
    out = rewrite(b"BT /F1 12 Tf (A) Tj ET", width=0.3)
    assert last_before(out, index_of(out, "Tj"), "w") == [0.3]


def test_custom_pattern():
    src = b"BT /F2 12 Tf (A) Tj ET"
    out = rewrite(src, CM_AND_HELV, pattern=re.compile("Helvetica"))
    assert last_before(out, index_of(out, "Tj"), "Tr") == [2]


@pytest.mark.xfail(strict=True, reason="line width set via ExtGState (gs) is not tracked")
def test_extgstate_line_width_restored():
    pdf = make_pdf(b"/GS1 gs BT /F1 12 Tf (A) Tj ET 0 0 m 10 10 l S", CM)
    page = pdf.pages[0]
    page.obj.Resources.ExtGState = Dictionary(GS1=Dictionary(Type=Name.ExtGState, LW=3))
    data = embolden.rewrite_content(page, page.obj.Resources, 0.15, DEFAULT)
    out = ops(pdf.make_stream(data))
    # After the text, the line should still be drawn with the ExtGState's width of 3.
    assert last_before(out, index_of(out, "S"), "w") in (None, [3])


@pytest.mark.xfail(strict=True, reason="form XObjects inherit the caller's state, not defaults")
def test_form_does_not_reset_inherited_stroke_state():
    pdf = pikepdf.new()
    form = make_form(pdf, b"BT /F1 12 Tf (A) Tj ET 0 0 m 10 10 l S", CM)
    data = embolden.rewrite_content(form, form.Resources, 0.15, DEFAULT)
    out = ops(pdf.make_stream(data))
    # The form never set w, so it must not force the default 1 w on the caller's line.
    assert last_before(out, index_of(out, "S"), "w") in (None, [0.15]) and \
        "w" not in names(out[index_of(out, "ET"):])


# --- whole-file processing --------------------------------------------------

def test_embolden_end_to_end(tmp_pdf, tmp_path):
    src = tmp_pdf(make_pdf(b"BT /F1 12 Tf (A) Tj ET", CM, pages=3))
    dst = tmp_path / "out.pdf"
    assert embolden.embolden(src, dst, 0.15, embolden.DEFAULT_FONT_PATTERN) == (3, 0)
    with pikepdf.open(dst) as pdf:
        assert len(pdf.pages) == 3
        for page in pdf.pages:
            assert "Tr" in names(ops(page))


def test_embolden_preserves_page_geometry_and_annotations(tmp_pdf, tmp_path):
    pdf = make_pdf(b"BT /F1 12 Tf (A) Tj ET", CM)
    page = pdf.pages[0].obj
    page.MediaBox = [0, 0, 300, 400]
    page.CropBox = [10, 10, 290, 390]
    page.Rotate = 90
    page.Annots = pdf.make_indirect(pikepdf.Array([pdf.make_indirect(Dictionary(
        Type=Name.Annot, Subtype=Name.Highlight, Rect=[1, 2, 3, 4],
        QuadPoints=[1, 4, 3, 4, 1, 2, 3, 2]))]))
    src = tmp_pdf(pdf)
    dst = tmp_path / "out.pdf"
    embolden.embolden(src, dst, 0.15, embolden.DEFAULT_FONT_PATTERN)
    with pikepdf.open(dst) as out:
        p = out.pages[0].obj
        assert list(p.MediaBox) == [0, 0, 300, 400]
        assert list(p.CropBox) == [10, 10, 290, 390]
        assert p.Rotate == 90
        annot = p.Annots[0]
        assert list(annot.Rect) == [1, 2, 3, 4]
        assert list(annot.QuadPoints) == [1, 4, 3, 4, 1, 2, 3, 2]


def test_embolden_multiple_content_streams(tmp_pdf, tmp_path):
    pdf = make_pdf(b"", CM)
    pdf.pages[0].obj.Contents = pikepdf.Array([
        pdf.make_stream(b"BT /F1 12 Tf"), pdf.make_stream(b"(A) Tj ET")])
    src = tmp_pdf(pdf)
    dst = tmp_path / "out.pdf"
    assert embolden.embolden(src, dst, 0.15, embolden.DEFAULT_FONT_PATTERN) == (1, 0)
    with pikepdf.open(dst) as out:
        out_ops = ops(out.pages[0])
        assert last_before(out_ops, index_of(out_ops, "Tj"), "Tr") == [2]


def test_embolden_no_match_leaves_file_equivalent(tmp_pdf, tmp_path):
    content = b"BT /F2 12 Tf (A) Tj ET"
    src = tmp_pdf(make_pdf(content, {"F2": "Helvetica"}))
    dst = tmp_path / "out.pdf"
    assert embolden.embolden(src, dst, 0.15, embolden.DEFAULT_FONT_PATTERN) == (0, 0)
    with pikepdf.open(src) as a, pikepdf.open(dst) as b:
        assert ops(a.pages[0]) == ops(b.pages[0])


def _pdf_with_form(form_content=b"BT /F1 10 Tf (Fig) Tj ET"):
    pdf = make_pdf(b"q /Fm1 Do Q", {"F2": "Helvetica"})
    form = make_form(pdf, form_content, CM)
    pdf.pages[0].obj.Resources.XObject = Dictionary(Fm1=form)
    return pdf


@pytest.mark.xfail(strict=True, reason="streams are rewritten and counted whenever a "
                   "matching font is in their resources, even if no text uses it")
def test_stream_without_text_not_counted(tmp_pdf, tmp_path):
    src = tmp_pdf(make_pdf(b"0 0 m 10 10 l S", CM))
    assert embolden.embolden(src, tmp_path / "out.pdf", 0.15,
                             embolden.DEFAULT_FONT_PATTERN) == (0, 0)


def test_embolden_processes_forms(tmp_pdf, tmp_path):
    src = tmp_pdf(_pdf_with_form())
    dst = tmp_path / "out.pdf"
    assert embolden.embolden(src, dst, 0.15, embolden.DEFAULT_FONT_PATTERN) == (0, 1)
    with pikepdf.open(dst) as pdf:
        form = pdf.pages[0].Resources.XObject.Fm1
        assert "Tr" in names(ops(form))


def test_embolden_no_forms_option(tmp_pdf, tmp_path):
    src = tmp_pdf(_pdf_with_form())
    dst = tmp_path / "out.pdf"
    assert embolden.embolden(src, dst, 0.15, embolden.DEFAULT_FONT_PATTERN,
                             include_forms=False) == (0, 0)
    with pikepdf.open(dst) as pdf:
        assert "Tr" not in names(ops(pdf.pages[0].Resources.XObject.Fm1))


def test_shared_form_processed_once(tmp_pdf, tmp_path):
    pdf = make_pdf(b"q /Fm1 Do Q", {"F2": "Helvetica"}, pages=3)
    form = make_form(pdf, b"BT /F1 10 Tf (Fig) Tj ET", CM)
    for page in pdf.pages:
        page.obj.Resources = Dictionary(page.obj.Resources)
        page.obj.Resources.XObject = Dictionary(Fm1=form)
    src = tmp_pdf(pdf)
    dst = tmp_path / "out.pdf"
    assert embolden.embolden(src, dst, 0.15, embolden.DEFAULT_FONT_PATTERN) == (0, 1)
    with pikepdf.open(dst) as out:
        form_ops = ops(out.pages[0].Resources.XObject.Fm1)
        assert names(form_ops).count("Tr") == 2  # one enable, one restore


def test_nested_forms(tmp_pdf, tmp_path):
    pdf = make_pdf(b"q /Fm1 Do Q", {"F2": "Helvetica"})
    outer = make_form(pdf, b"/Inner Do", {"F2": "Helvetica"})
    pdf.pages[0].obj.Resources.XObject = Dictionary(Fm1=outer)
    inner = make_form(pdf, b"BT /F1 10 Tf (In) Tj ET", CM)
    outer.Resources.XObject = Dictionary(Inner=inner)
    src = tmp_pdf(pdf)
    dst = tmp_path / "out.pdf"
    assert embolden.embolden(src, dst, 0.15, embolden.DEFAULT_FONT_PATTERN) == (0, 1)


def test_self_referencing_form_does_not_loop(tmp_pdf, tmp_path):
    pdf = _pdf_with_form()
    form = pdf.pages[0].obj.Resources.XObject.Fm1
    form.Resources.XObject = Dictionary(Self=form)
    src = tmp_pdf(pdf)
    dst = tmp_path / "out.pdf"
    assert embolden.embolden(src, dst, 0.15, embolden.DEFAULT_FONT_PATTERN) == (0, 1)


def test_image_xobject_ignored(tmp_pdf, tmp_path):
    pdf = make_pdf(b"BT /F1 12 Tf (A) Tj ET q /Im1 Do Q", CM)
    image = pdf.make_stream(b"\x00", Type=Name.XObject, Subtype=Name.Image,
                            Width=1, Height=1, ColorSpace=Name.DeviceGray,
                            BitsPerComponent=8)
    pdf.pages[0].obj.Resources.XObject = Dictionary(Im1=image)
    src = tmp_pdf(pdf)
    dst = tmp_path / "out.pdf"
    assert embolden.embolden(src, dst, 0.15, embolden.DEFAULT_FONT_PATTERN) == (1, 0)
    with pikepdf.open(dst) as out:
        assert out.pages[0].Resources.XObject.Im1.read_bytes() == b"\x00"


# --- list_fonts and CLI -----------------------------------------------------

def test_list_fonts(tmp_pdf, capsys):
    src = tmp_pdf(_pdf_with_form())
    embolden.list_fonts(src, embolden.DEFAULT_FONT_PATTERN)
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("* CMR10") and "Type1" in line for line in lines)
    assert any(line.startswith("  Helvetica") for line in lines)


def test_main_writes_output(tmp_pdf, tmp_path, capsys):
    src = tmp_pdf(make_pdf(b"BT /F1 12 Tf (A) Tj ET", CM))
    dst = tmp_path / "out.pdf"
    assert embolden.main([str(src), str(dst), "--width", "0.2"]) == 0
    assert "1 pages" in capsys.readouterr().out
    with pikepdf.open(dst) as pdf:
        out = ops(pdf.pages[0])
        assert last_before(out, index_of(out, "Tj"), "w") == [0.2]


def test_main_warns_when_nothing_matches(tmp_pdf, tmp_path, capsys):
    src = tmp_pdf(make_pdf(b"BT /F2 12 Tf (A) Tj ET", {"F2": "Helvetica"}))
    assert embolden.main([str(src), str(tmp_path / "out.pdf")]) == 0
    assert "--list-fonts" in capsys.readouterr().err


def test_main_fonts_option(tmp_pdf, tmp_path):
    src = tmp_pdf(make_pdf(b"BT /F2 12 Tf (A) Tj ET", {"F2": "Helvetica"}))
    dst = tmp_path / "out.pdf"
    embolden.main([str(src), str(dst), "--fonts", "Helvetica"])
    with pikepdf.open(dst) as pdf:
        assert "Tr" in names(ops(pdf.pages[0]))


def test_main_list_fonts_needs_no_output(tmp_pdf, capsys):
    src = tmp_pdf(make_pdf(b"", CM))
    assert embolden.main([str(src), "--list-fonts"]) == 0
    assert "CMR10" in capsys.readouterr().out


def test_main_requires_output(tmp_pdf):
    src = tmp_pdf(make_pdf(b"", CM))
    with pytest.raises(SystemExit) as exc:
        embolden.main([str(src)])
    assert exc.value.code == 2


def test_main_rejects_same_input_and_output(tmp_pdf):
    src = tmp_pdf(make_pdf(b"", CM))
    with pytest.raises(SystemExit) as exc:
        embolden.main([str(src), str(src)])
    assert exc.value.code == 2


@pytest.mark.xfail(strict=True, reason="same-file check compares path strings only")
def test_main_rejects_same_file_via_different_path(tmp_pdf):
    src = tmp_pdf(make_pdf(b"", CM))
    alias = f"{src.parent}/./{src.name}"
    with pytest.raises(SystemExit) as exc:
        embolden.main([str(src), alias])
    assert exc.value.code == 2


def test_main_version(capsys):
    with pytest.raises(SystemExit) as exc:
        embolden.main(["--version"])
    assert exc.value.code == 0
    assert embolden.__version__ in capsys.readouterr().out
