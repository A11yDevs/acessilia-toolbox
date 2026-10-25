"""Build-time check of the pp-formulanet image: load the model and recognize a formula.

Loading the model downloads the pinned weights, which the non-root runtime
user could not do. Recognizing a formula then exercises the whole inference
path, post-processing included: PaddleX imports some optional dependencies
(``ftfy``) only there, so an image that merely loads the model can pass the
build and ``/health`` and still fail every ``/predict``.

The crop carries real glyphs and the result must be non-empty LaTeX, so the
check does not depend on how PaddleX treats an empty prediction. The pinned
optional dependencies are also imported by name, so a missing one fails here
with its own name even if a future PaddleX stops importing it on this path.
"""

from __future__ import annotations

import os
import sys

import numpy as np
from formula_result import recognized_latex
from paddleocr import FormulaRecognition
from PIL import Image, ImageDraw, ImageFont

#: Optional paddleocr dependencies (PaddleX's "ocr" extra) that the formula
#: decoder needs and the Dockerfile pins.
REQUIRED_MODULES = ("tokenizers", "ftfy")


def formula_crop() -> np.ndarray:
    """A small black-on-white formula, in the BGR array PaddleOCR reads."""
    image = Image.new("RGB", (320, 96), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.text((20, 20), "x + 1 = y", fill=(0, 0, 0), font=ImageFont.load_default(size=44))
    return np.ascontiguousarray(np.asarray(image)[:, :, ::-1])


def main() -> int:
    for name in REQUIRED_MODULES:
        __import__(name)

    model = FormulaRecognition(
        model_name=os.environ.get("PP_FORMULANET_MODEL", "PP-FormulaNet_plus-M"),
        device="cpu",
    )
    latex = recognized_latex(model.predict(input=formula_crop(), batch_size=1))
    if not latex:
        print("build check: the model recognized no formula in the test crop", file=sys.stderr)
        return 1
    print(f"build check: recognized {latex!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
