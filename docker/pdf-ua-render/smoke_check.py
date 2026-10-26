"""Build-time check: the smoke PDF carries the formula's alternate text.

smoke.json marks its first formula with an ``accessilia-formula`` span; the
image only builds if filters/formula_alt.lua and \\AccessiliaFormulaAlt in
pdf_ua.tex turned it into that Formula's /Alt. A latex-lab update that renames
the alt template would otherwise silently bring back "LaTeX formula starts ...".
"""

import re
import sys
from pathlib import Path

EXPECTED = "conjunto dos h reais com h maior ou igual a menos 2 e h diferente de 0"


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


if __name__ == "__main__":
    texts = alternate_texts(Path(sys.argv[1]).read_bytes())
    if EXPECTED not in texts:
        sys.exit(f"formula alternate text missing; /Alt found: {texts}")
    print("smoke: formula alternate text present")
