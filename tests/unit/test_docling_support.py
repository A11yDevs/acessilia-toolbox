"""Unit tests for docling shared support utilities."""

from __future__ import annotations

from acessilia_toolbox.providers.docling_support import (
    extract_page_number,
    float_or_none,
    page_height,
    top_left_bbox,
)


class TestFloatOrNone:
    def test_valid_numbers(self) -> None:
        assert float_or_none(42) == 42.0
        assert float_or_none(3.14) == 3.14
        assert float_or_none("100.5") == 100.5
        assert float_or_none("-12.3") == -12.3

    def test_invalid_values(self) -> None:
        assert float_or_none(None) is None
        assert float_or_none(True) is None
        assert float_or_none(False) is None
        assert float_or_none("abc") is None
        assert float_or_none("") is None
        assert float_or_none(float("inf")) is None
        assert float_or_none(float("nan")) is None
        assert float_or_none([1]) is None


class TestExtractPageNumber:
    def test_prefers_page_no(self) -> None:
        assert extract_page_number({"page_no": 3, "page": 1}) == 3

    def test_falls_back_to_page(self) -> None:
        assert extract_page_number({"page": 2}) == 2

    def test_falls_back_to_page_number(self) -> None:
        assert extract_page_number({"page_number": 4}) == 4

    def test_converts_numeric_strings(self) -> None:
        assert extract_page_number({"page_no": "5"}) == 5

    def test_ignores_non_positive_or_invalid(self) -> None:
        assert extract_page_number({"page_no": 0}, default=1) == 1
        assert extract_page_number({"page_no": -2}, default=1) == 1
        assert extract_page_number({"page_no": "invalid"}, default=1) == 1
        assert extract_page_number({}, default=1) == 1


class TestPageHeight:
    def test_dict_with_str_key_and_height(self) -> None:
        content = {"pages": {"1": {"height": 842.0}}}
        assert page_height(content, 1) == 842.0
        assert page_height(content, "1") == 842.0

    def test_dict_with_size_dict(self) -> None:
        content = {"pages": {1: {"size": {"height": 792.0}}}}
        assert page_height(content, 1) == 792.0

    def test_list_of_pages(self) -> None:
        content = {
            "pages": [
                {"page_number": 1, "size": {"height": 842.0}},
                {"page_number": 2, "height": 600.0},
            ]
        }
        assert page_height(content, 1) == 842.0
        assert page_height(content, 2) == 600.0

    def test_list_of_pages_fallback_index(self) -> None:
        content = {
            "pages": [
                {"height": 500.0},
            ]
        }
        assert page_height(content, 1) == 500.0

    def test_missing_or_malformed_pages(self) -> None:
        assert page_height({}, 1) is None
        assert page_height({"pages": None}, 1) is None
        assert page_height({"pages": "invalid"}, 1) is None
        assert page_height({"pages": {"1": {"height": "invalid"}}}, 1) is None


class TestTopLeftBbox:
    def test_topleft_dict_with_l_t_r_b(self) -> None:
        box = {"l": 10.0, "t": 20.0, "r": 110.0, "b": 120.0, "coord_origin": "TOPLEFT"}
        assert top_left_bbox(box, page_height=800.0) == (10.0, 20.0, 110.0, 120.0)

    def test_topleft_dict_with_long_keys(self) -> None:
        box = {"left": 10.0, "top": 20.0, "right": 110.0, "bottom": 120.0}
        assert top_left_bbox(box, page_height=None) == (10.0, 20.0, 110.0, 120.0)

    def test_bottomleft_dict_flipped(self) -> None:
        # In BOTTOMLEFT, Docling gives t=522.0 (distance from bottom), b=471.0
        # When page_height=842.0:
        # new_top = 842.0 - 522.0 = 320.0
        # new_bottom = 842.0 - 471.0 = 371.0
        box = {"l": 285.3, "t": 522.0, "r": 650.0, "b": 471.0, "coord_origin": "BOTTOMLEFT"}
        result = top_left_bbox(box, page_height=842.0)
        assert result == (285.3, 320.0, 650.0, 371.0)

    def test_bottomleft_dropped_without_page_height(self) -> None:
        box = {"l": 10.0, "t": 20.0, "r": 110.0, "b": 120.0, "coord_origin": "BOTTOMLEFT"}
        assert top_left_bbox(box, page_height=None) is None

    def test_list_bbox(self) -> None:
        box = [10.0, 20.0, 110.0, 120.0]
        assert top_left_bbox(box, page_height=None) == (10.0, 20.0, 110.0, 120.0)

    def test_inverted_coordinates_normalized(self) -> None:
        # min and max are used, so even if l > r or t > b, it returns (min_x, min_y, max_x, max_y)
        box = [110.0, 120.0, 10.0, 20.0]
        assert top_left_bbox(box, page_height=None) == (10.0, 20.0, 110.0, 120.0)

    def test_unsupported_origin_or_invalid_structure(self) -> None:
        assert (
            top_left_bbox({"coord_origin": "UNKNOWN", "l": 0, "t": 0, "r": 1, "b": 1}, 100) is None
        )
        assert top_left_bbox([1, 2, 3], 100) is None
        assert top_left_bbox("not-a-box", 100) is None
        assert top_left_bbox({"l": "bad", "t": 0, "r": 1, "b": 1}, 100) is None
