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


def alternate_texts(pdf: bytes) -> list[str]:
    texts = []
    for match in re.finditer(rb"/Alt\s*<([0-9A-Fa-f]+)>", pdf):
        raw = bytes.fromhex(match.group(1).decode())
        texts.append(raw.decode("utf-16") if raw.startswith(b"\xfe\xff") else raw.decode("latin-1"))
    return texts


if __name__ == "__main__":
    texts = alternate_texts(Path(sys.argv[1]).read_bytes())
    if EXPECTED not in texts:
        sys.exit(f"formula alternate text missing; /Alt found: {texts}")
    print("smoke: formula alternate text present")
