"""PaddleOCR result parsing used by the pp-formulanet sidecar."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

RESULT_PATH = (
    Path(__file__).resolve().parents[2]
    / "docker"
    / "pp-formulanet"
    / "formula_result.py"
)


@pytest.fixture(scope="module")
def result_module() -> Any:
    spec = importlib.util.spec_from_file_location("pp_formulanet_result", RESULT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Result:
    """Stand-in for a PaddleOCR result: the server reads ``.json`` only."""

    def __init__(self, payload: Any) -> None:
        self.json = payload


class TestRecognizedLatex:
    def test_reads_the_res_wrapper(self, result_module: Any) -> None:
        results = [Result({"res": {"rec_formula": r"E=mc^2"}})]

        assert result_module.recognized_latex(results) == "E=mc^2"

    def test_reads_an_unwrapped_payload(self, result_module: Any) -> None:
        results = [Result({"rec_formula": r"\frac{a}{b}"})]

        assert result_module.recognized_latex(results) == r"\frac{a}{b}"

    def test_skips_empty_results_until_the_first_formula(self, result_module: Any) -> None:
        results = [
            Result({"res": {"rec_formula": "   "}}),
            Result({"res": {"rec_formula": r"x^2"}}),
        ]

        assert result_module.recognized_latex(results) == "x^2"

    def test_returns_empty_when_no_formula_was_recognized(self, result_module: Any) -> None:
        assert result_module.recognized_latex([]) == ""
        assert result_module.recognized_latex([Result({"res": {}})]) == ""
        assert result_module.recognized_latex([Result({"res": {"rec_formula": 3}})]) == ""

    def test_strips_a_wrapping_delimiter_pair(self, result_module: Any) -> None:
        results = [Result({"res": {"rec_formula": "$$x+1$$"}})]

        assert result_module.recognized_latex(results) == "x+1"

    def test_keeps_delimiters_that_are_part_of_the_expression(self, result_module: Any) -> None:
        results = [Result({"res": {"rec_formula": "$a$ + $b$"}})]

        assert result_module.recognized_latex(results) == "$a$ + $b$"


class TestStripMathDelimiters:
    def test_strips_double_then_single(self, result_module: Any) -> None:
        assert result_module.strip_math_delimiters("$$x$$") == "x"
        assert result_module.strip_math_delimiters("$x$") == "x"

    def test_leaves_a_single_character_alone(self, result_module: Any) -> None:
        assert result_module.strip_math_delimiters("$") == "$"
        assert result_module.strip_math_delimiters("$$") == "$$"
