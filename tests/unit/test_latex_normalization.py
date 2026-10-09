from acessilia_toolbox.core.normalization.latex import (
    normalize_latex,
    strip_latex_delimiters,
    wrap_latex,
)


def test_strip_delimiters() -> None:
    assert strip_latex_delimiters("$$E = mc^2$$") == "E = mc^2"
    assert strip_latex_delimiters("$E = mc^2$") == "E = mc^2"
    assert strip_latex_delimiters(r"\[E = mc^2\]") == "E = mc^2"
    assert strip_latex_delimiters(r"\(E = mc^2\)") == "E = mc^2"


def test_normalize_latex_operators_and_spacing() -> None:
    assert normalize_latex(r"$$ x +  y= z $$") == "x + y = z"
    assert normalize_latex(r"a \le  b") == r"a \le b"
    assert normalize_latex(r"x   ^  {  2  }") == "x^{2}"
    assert normalize_latex(r"x   _  {  i  }") == "x_{i}"


def test_normalize_latex_unicode_replacements() -> None:
    assert normalize_latex("a × b ≤ c") == r"a \times b \le c"  # noqa: RUF001
    assert normalize_latex("π r²") == r"\pi r²"


def test_normalize_latex_trivial_text_cleanup() -> None:
    assert normalize_latex(r"\text{x} + \text{y}") == "x + y"


def test_wrap_latex() -> None:
    assert wrap_latex("x + y = z", display=True) == "$$x + y = z$$"
    assert wrap_latex("x + y = z", display=False) == "$x + y = z$"
    assert wrap_latex("", display=True) == ""
