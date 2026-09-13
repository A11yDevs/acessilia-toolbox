"""pure-code provider behavior for code normalization."""

from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.pure_code import (
    PureCodeProvider,
    _detect_language,
    _normalize_code,
)


def descriptor(**overrides: object) -> ProviderDescriptor:
    base = {
        "id": "pure-code",
        "version": "1.0",
        "transport": "in_process",
        "capabilities": ["code.normalize"],
    }
    return ProviderDescriptor.model_validate({**base, **overrides})


def extract(adapter: PureCodeProvider, code: str, **params: object) -> ExtractionResult:
    return adapter.execute(
        "code.normalize",
        code.encode("utf-8"),
        filename="code.txt",
        media_type="text/plain",
        parameters=params if params else None,
    )


class TestPureCodeProvider:
    def test_normalize_python_code(self) -> None:
        adapter = PureCodeProvider(descriptor())
        code = 'def hello():\n    print("world")'
        result = extract(adapter, code)

        assert result.backend == "pure-python"
        assert result.document["language"] == "python"
        assert "def hello():" in result.document["normalized"]

    def test_normalize_removes_markdown_fences(self) -> None:
        adapter = PureCodeProvider(descriptor())
        code = "```python\ndef foo():\n    pass\n```"
        result = extract(adapter, code)

        assert "```" not in result.document["normalized"]
        assert "def foo():" in result.document["normalized"]

    def test_normalize_removes_shell_prompts(self) -> None:
        adapter = PureCodeProvider(descriptor())
        code = "$ echo hello\n> world\n% test"
        result = extract(adapter, code)

        assert "$ " not in result.document["normalized"]
        assert "echo hello" in result.document["normalized"]

    def test_detect_language_by_content(self) -> None:
        assert _detect_language("def foo():\n    pass") == "python"
        assert _detect_language("const x = () => 1") == "javascript"
        assert _detect_language("public class Foo { }") == "java"
        assert _detect_language("fn main() { }") == "rust"
        assert _detect_language("SELECT * FROM users") == "sql"

    def test_detect_language_by_filename(self) -> None:
        assert _detect_language("print(1)", filename="test.py") == "python"
        assert _detect_language("print(1)", filename="test.js") == "javascript"
        assert _detect_language("print(1)", filename="test.java") == "java"
        assert _detect_language("print(1)", filename="test.rs") == "rust"

    def test_detect_language_unknown(self) -> None:
        assert _detect_language("Some random prose text.") == "unknown"

    def test_normalize_counts_lines(self) -> None:
        adapter = PureCodeProvider(descriptor())
        code = "line1\nline2\nline3"
        result = extract(adapter, code)

        assert result.document["line_count"] == 3

    def test_normalize_strips_leading_trailing_blank_lines(self) -> None:
        adapter = PureCodeProvider(descriptor())
        code = "\n\n\ndef foo():\n    pass\n\n\n"
        result = extract(adapter, code)

        assert result.document["line_count"] == 2

    def test_health_returns_healthy(self) -> None:
        adapter = PureCodeProvider(descriptor())
        health = adapter.health()
        assert health.healthy is True
        assert health.provider == "pure-code"

    def test_versions_returns_version(self) -> None:
        adapter = PureCodeProvider(descriptor())
        versions = adapter.versions()
        assert "provider" in versions

    def test_create_adapter_factory(self) -> None:
        adapter = create_adapter(descriptor())
        assert isinstance(adapter, PureCodeProvider)

    def test_normalize_with_explicit_language(self) -> None:
        adapter = PureCodeProvider(descriptor())
        code = "console.log('hello')"
        result = extract(adapter, code, language="javascript")

        assert result.document["language"] == "javascript"


class TestNormalizeCode:
    def test_preserves_indentation(self) -> None:
        result = _normalize_code("def foo():\n    return 42")
        assert "    return 42" in result["normalized"]

    def test_character_count(self) -> None:
        result = _normalize_code("abc")
        assert result["character_count"] == 3

    def test_blank_line_count(self) -> None:
        result = _normalize_code("a\n\n\nb")
        assert result["blank_line_count"] == 2
