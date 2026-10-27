"""Build-time check: the smoke PDF carries the formula's alternate text and math text layer.

smoke.json marks its first formula with an ``accessilia-formula`` span; the
image only builds if filters/formula_alt.lua and \\AccessiliaFormulaAlt in
pdf_ua.tex turned it into that Formula's /Alt. A latex-lab update that renames
the alt template would otherwise silently bring back "LaTeX formula starts ...".

The same formula has ``\\neq``. Set in TeX Gyre DejaVu Math by unicode-math, its
glyph maps to U+2260 in the font's ToUnicode CMap; with the legacy math fonts
it is ``\\not`` over "=" and the text layer reads "=" plus an orphan U+0338.
tagpdf's ``uncompress`` leaves the CMaps readable as plain text.
"""

import re
import sys
from pathlib import Path

EXPECTED = "conjunto dos h reais com h maior ou igual a menos 2 e h diferente de 0"
MATH_FONT = b"TeXGyreDejaVuMath"
NOT_EQUAL, COMBINING_NOT = "\u2260", "\u0338"


_ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}


def _decode(raw: bytes) -> str:
    # "utf-16" honours the byte order mark of either endianness.
    return raw.decode("utf-16") if raw[:2] in (b"\xfe\xff", b"\xff\xfe") else raw.decode("latin-1")


def _literal(pdf: bytes, start: int) -> bytes:
    """A PDF literal string from just after its "(": balanced parentheses, escapes, EOLs."""
    out, depth, i = bytearray(), 1, start
    while i < len(pdf):
        char = pdf[i : i + 1]
        if char == b"\\":
            following = pdf[i + 1 : i + 2]
            octal = re.match(rb"[0-7]{1,3}", pdf[i + 1 : i + 4])
            if octal:
                out.append(int(octal.group(0), 8) & 0xFF)
                i += 1 + len(octal.group(0))
                continue
            if following in (b"\r", b"\n"):
                # A backslash before an end of line continues the line (PDF 7.3.4.2).
                i += 3 if pdf[i + 1 : i + 3] == b"\r\n" else 2
                continue
            out += _ESCAPES.get(following, following)
            i += 2
            continue
        if char == b"(":
            depth += 1
        elif char == b")":
            depth -= 1
            if depth == 0:
                break
        elif char == b"\r":
            # Any unescaped end of line reads as a single LF (PDF 7.3.4.2).
            char = b"\n"
            if pdf[i + 1 : i + 2] == b"\n":
                i += 1
        out += char
        i += 1
    return bytes(out)


def alternate_texts(pdf: bytes) -> list[str]:
    """Every /Alt of the file, written as a hex or as a literal string."""
    texts = []
    for match in re.finditer(rb"/Alt\s*(?:<([0-9A-Fa-f\s]*)>|\()", pdf):
        if match.group(1) is not None:
            digits = re.sub(rb"\s", b"", match.group(1)).decode()
            # An odd number of digits ends with an implicit 0 (PDF 7.3.4.3).
            texts.append(_decode(bytes.fromhex(digits + "0" * (len(digits) % 2))))
        else:
            texts.append(_decode(_literal(pdf, match.end())))
    return texts


def _hex_text(digits: bytes) -> str | None:
    """The UTF-16BE text of a CMap target, or None for a malformed one.

    An odd number of digits, a lone surrogate or a range that carries its last
    code unit past FFFF has no text; skipping it keeps the check's own message.
    """
    try:
        return bytes.fromhex(digits.decode()).decode("utf-16-be")
    except ValueError:
        return None


def text_layer(pdf: bytes) -> set[str]:
    """Every text a glyph maps to in the file's uncompressed ToUnicode CMaps."""
    texts = set()
    for cmap in re.finditer(rb"begincmap(.*?)endcmap", pdf, re.S):
        body = cmap.group(1)
        for block in re.finditer(rb"beginbfchar(.*?)endbfchar", body, re.S):
            for _, target in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block.group(1)):
                texts.add(_hex_text(target))
        for block in re.finditer(rb"beginbfrange(.*?)endbfrange", body, re.S):
            ranges = re.findall(
                rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]+>|\[[^\]]*\])",
                block.group(1),
            )
            for low, high, target in ranges:
                if target.startswith(b"["):
                    texts.update(
                        _hex_text(item) for item in re.findall(rb"<([0-9A-Fa-f]+)>", target)
                    )
                    continue
                # A range target increments its last code unit (PDF 9.10.3).
                first = target[1:-1]
                for offset in range(int(high, 16) - int(low, 16) + 1):
                    unit = int(first[-4:], 16) + offset
                    texts.add(_hex_text(first[:-4] + b"%04X" % unit))
    texts.discard(None)
    return texts


if __name__ == "__main__":
    pdf = Path(sys.argv[1]).read_bytes()
    texts = alternate_texts(pdf)
    if EXPECTED not in texts:
        sys.exit(f"formula alternate text missing; /Alt found: {texts}")
    if MATH_FONT not in pdf:
        sys.exit(f"math font {MATH_FONT.decode()} not embedded: unicode-math did not take effect")
    # Document-wide: the U+0338 check reads every font's CMap, text fonts
    # included, so a smoke or alt text with a decomposed combining sequence
    # would trip it too.
    layer = text_layer(pdf)
    if NOT_EQUAL not in layer or any(COMBINING_NOT in text for text in layer):
        sys.exit("\\neq is not U+2260 in the text layer (legacy \\not overlay?)")
    print("smoke: formula alternate text present, math text layer has U+2260")
