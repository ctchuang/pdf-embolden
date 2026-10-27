#!/usr/bin/env python3
"""
pdf-embolden: make thin fonts in a PDF look heavier on screen, without moving anything.

Computer Modern / Latin Modern (the LaTeX defaults) and similar print fonts have
hairline strokes that look faint in viewers without font smoothing, such as
PDF.js (used by Zotero and Firefox). This script switches matching text to
text render mode 2 (fill + stroke) and strokes each glyph outline with a thin
line in the same colour as its fill.

Glyph positions, the text layer (search, selection, copy) and page geometry
are untouched, so annotations made by reference managers stay aligned.
"""
import argparse
import re
import sys

import pikepdf
from pikepdf import Name, Operator

__version__ = "1.0.0"

# Computer Modern, Latin Modern and the AMS symbol fonts.
DEFAULT_FONT_PATTERN = (
    r"CM(R|MI|MIB|SY|EX|BX|TI|SL|TT|CSC)\d+"
    r"|LMRoman\d+-|LMRomanSlant\d+-|LMRomanCaps\d+-|LMMono\d+-"
    r"|MSAM\d+|MSBM\d+"
)

FILL_TO_STROKE = {"g": "G", "rg": "RG", "k": "K", "cs": "CS", "sc": "SC", "scn": "SCN"}
FILL_OPS = set(FILL_TO_STROKE)
STROKE_OPS = set(FILL_TO_STROKE.values())
SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")


def base_font_name(font):
    """BaseFont without the leading slash and without the subset tag (ABCDEF+)."""
    return SUBSET_PREFIX.sub("", str(font.get("/BaseFont", ""))[1:])


def matching_fonts(resources, pattern):
    """Resource names (e.g. /F12) of fonts whose BaseFont matches the pattern."""
    names = set()
    if resources is None:
        return names
    fonts = resources.get("/Font", None)
    if fonts is None:
        return names
    for key, font in fonts.items():
        if pattern.match(base_font_name(font)):
            names.add(Name(str(key)))
    return names


def page_resources(page_obj):
    """Page resources, following inheritance from the page tree if needed."""
    node = page_obj
    while node is not None:
        if "/Resources" in node:
            return node.Resources
        node = node.get("/Parent", None)
    return None


def _default_state():
    return dict(
        fill_cs=None, fill_col=("g", [0]),
        stroke_cs=None, stroke_col=("G", [0]),
        w=1.0, J=0, j=0, d=([], 0), Tr=0, font_match=False,
    )


def rewrite_content(stream_owner, resources, width, pattern):
    """Return a rewritten content stream, or None if nothing in it matches."""
    targets = matching_fonts(resources, pattern)
    if not targets:
        return None

    state = _default_state()
    stack = []
    out = []
    in_text = False
    active = False    # our fill+stroke mode is on inside the current text object
    touched = False   # we changed stroke-related state inside the current text object

    def emit(op, operands=()):
        out.append((list(operands), Operator(op)))

    def mirror_fill_to_stroke():
        if state["fill_cs"] is not None:
            emit("CS", state["fill_cs"])
        op, operands = state["fill_col"]
        if op is not None:
            emit(FILL_TO_STROKE[op], operands)

    def enable():
        nonlocal active, touched
        emit("w", [width])
        emit("j", [1])                     # round joins: no miter spikes on serifs
        emit("J", [1])
        emit("d", [pikepdf.Array([]), 0])  # solid line
        mirror_fill_to_stroke()
        emit("Tr", [2])
        active = touched = True

    def disable_in_text():
        nonlocal active
        emit("Tr", [state["Tr"]])
        active = False

    def restore_after_text():
        nonlocal active, touched
        emit("Tr", [state["Tr"]])
        emit("w", [state["w"]])
        emit("j", [state["j"]])
        emit("J", [state["J"]])
        emit("d", [pikepdf.Array(state["d"][0]), state["d"][1]])
        if state["stroke_cs"] is not None:
            emit("CS", state["stroke_cs"])
        op, operands = state["stroke_col"]
        if op is not None:
            emit(op, operands)
        active = touched = False

    for operands, op in pikepdf.parse_content_stream(stream_owner):
        s = str(op)

        if s == "q":
            stack.append(dict(state))
            out.append((operands, op))
            continue
        if s == "Q":
            if stack:
                state = stack.pop()
            out.append((operands, op))
            continue

        # Track the parts of the graphics state we may need to restore.
        if s in ("g", "rg", "k"):
            state["fill_cs"], state["fill_col"] = None, (s, list(operands))
        elif s == "cs":
            state["fill_cs"], state["fill_col"] = list(operands), (None, [])
        elif s in ("sc", "scn"):
            state["fill_col"] = (s, list(operands))
        elif s in ("G", "RG", "K"):
            state["stroke_cs"], state["stroke_col"] = None, (s, list(operands))
        elif s == "CS":
            state["stroke_cs"], state["stroke_col"] = list(operands), (None, [])
        elif s in ("SC", "SCN"):
            state["stroke_col"] = (s, list(operands))
        elif s == "w":
            state["w"] = operands[0]
        elif s == "J":
            state["J"] = operands[0]
        elif s == "j":
            state["j"] = operands[0]
        elif s == "d":
            state["d"] = (list(operands[0]), operands[1])
        elif s == "Tr":
            state["Tr"] = int(operands[0])
        elif s == "Tf":
            state["font_match"] = Name(str(operands[0])) in targets

        if s == "BT":
            in_text = True
            out.append((operands, op))
            if state["font_match"] and state["Tr"] == 0:
                enable()
            continue
        if s == "ET":
            in_text = False
            out.append((operands, op))
            if touched:
                restore_after_text()
            continue

        out.append((operands, op))

        if in_text:
            wanted = state["font_match"] and state["Tr"] == 0
            if s in ("Tf", "Tr"):
                if wanted and not active:
                    enable()
                elif not wanted and active:
                    disable_in_text()
            elif s in FILL_OPS and active:
                mirror_fill_to_stroke()
            elif active and (s in STROKE_OPS or s in ("w", "J", "j", "d")):
                enable()  # the document changed stroke state mid-text; re-assert ours

    return pikepdf.unparse_content_stream(out)


def embolden(src, dst, width, pattern, include_forms=True):
    pattern = re.compile(pattern)
    pages_changed = forms_changed = 0
    seen = set()

    with pikepdf.open(src) as pdf:
        def walk_forms(resources):
            nonlocal forms_changed
            if resources is None:
                return
            for _, xobj in resources.get("/XObject", {}).items():
                if xobj.get("/Subtype") != "/Form" or xobj.objgen in seen:
                    continue
                seen.add(xobj.objgen)
                xres = xobj.get("/Resources", None)
                data = rewrite_content(xobj, xres, width, pattern)
                if data is not None:
                    xobj.write(data)
                    forms_changed += 1
                walk_forms(xres)

        for page in pdf.pages:
            resources = page_resources(page.obj)
            data = rewrite_content(page, resources, width, pattern)
            if data is not None:
                page.obj.Contents = pdf.make_stream(data)
                pages_changed += 1
            if include_forms:
                walk_forms(resources)

        pdf.save(dst, compress_streams=True,
                 object_stream_mode=pikepdf.ObjectStreamMode.generate)

    return pages_changed, forms_changed


def list_fonts(src, pattern):
    pattern = re.compile(pattern)
    found = {}
    with pikepdf.open(src) as pdf:
        def collect(resources, seen):
            if resources is None:
                return
            for _, font in resources.get("/Font", {}).items():
                found[base_font_name(font)] = str(font.get("/Subtype", ""))[1:]
            for _, xobj in resources.get("/XObject", {}).items():
                if xobj.get("/Subtype") == "/Form" and xobj.objgen not in seen:
                    seen.add(xobj.objgen)
                    collect(xobj.get("/Resources", None), seen)

        seen = set()
        for page in pdf.pages:
            collect(page_resources(page.obj), seen)

    for name in sorted(found):
        mark = "*" if pattern.match(name) else " "
        print(f"{mark} {name:45s} {found[name]}")
    print("\n* = matched by the font pattern and will be emboldened")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Embolden thin fonts in a PDF by stroking glyph outlines, "
                    "without changing layout or the text layer.")
    ap.add_argument("input", help="input PDF")
    ap.add_argument("output", nargs="?", help="output PDF")
    ap.add_argument("-w", "--width", type=float, default=0.15,
                    help="stroke width in points (default: 0.15; try 0.1 to 0.3)")
    ap.add_argument("-f", "--fonts", default=DEFAULT_FONT_PATTERN,
                    help="regex matched against font names, subset tag removed "
                         "(default: Computer Modern / Latin Modern / AMS symbols)")
    ap.add_argument("--no-forms", action="store_true",
                    help="leave form XObjects (usually figures) untouched")
    ap.add_argument("--list-fonts", action="store_true",
                    help="list the fonts in the PDF and which ones match, then exit")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = ap.parse_args(argv)

    if args.list_fonts:
        list_fonts(args.input, args.fonts)
        return 0
    if not args.output:
        ap.error("output PDF is required unless --list-fonts is given")
    if args.input == args.output:
        ap.error("output must be a different file from input")

    pages, forms = embolden(args.input, args.output, args.width, args.fonts,
                            include_forms=not args.no_forms)
    print(f"Done: {pages} pages and {forms} form XObjects updated -> {args.output}")
    if pages == 0 and forms == 0:
        print("No matching fonts found. Run with --list-fonts and adjust --fonts.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
