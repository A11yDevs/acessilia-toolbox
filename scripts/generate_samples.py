#!/usr/bin/env python3
"""Generate sample PDFs for manual API testing.

Usage:
    python scripts/generate_samples.py
    python scripts/generate_samples.py --output /tmp
"""

from __future__ import annotations

import argparse
from pathlib import Path


def _obj(n: int, body: bytes) -> bytes:
    return b"%d 0 obj\n" % n + body + b"\nendobj\n"


def _xref(offsets: list[int]) -> bytes:
    buf = b"xref\n0 %d\n" % (len(offsets) + 1)
    buf += b"0000000000 65535 f \n"
    for o in offsets:
        buf += b"%010d 00000 n \n" % o
    return buf


def _build_pdf(objects: list[bytes]) -> bytes:
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += _obj(n, body)
    xref_at = len(pdf)
    pdf += _xref(offsets)
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref_at
    )
    return bytes(pdf)


def _font(name: str = "Helvetica") -> bytes:
    return b"<< /Type /Font /Subtype /Type1 /BaseFont /" + name.encode() + b" >>"


def _page(fonts: list[int], contents: int) -> bytes:
    font_refs = b" ".join(b"/F%d %d 0 R" % (i + 1, f) for i, f in enumerate(fonts))
    return (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842]"
        b" /Resources << /Font << " + font_refs + b" >> >> /Contents %d 0 R >>" % contents
    )


def _stream(content: bytes) -> bytes:
    return b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"


def _text(size: int, x: int, y: int, text: str, font: int = 1) -> bytes:
    encoded = text.encode("latin-1", errors="replace")
    return b"BT /F%d %d Tf %d %d Td (%s) Tj ET\n" % (font, size, x, y, encoded)


def _heading(text: str, y: int) -> bytes:
    return _text(24, 72, y, text)


def _paragraph(text: str, y: int) -> bytes:
    return _text(11, 72, y, text)


def generate_simple_document(path: Path) -> None:
    """A minimal PDF with one heading and one paragraph."""
    content = _stream(
        _heading("Hello Toolbox", 760)
        + _paragraph("This is a simple test document for API validation.", 720)
    )
    pdf = _build_pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        _page([5], 4),
        content,
        _font(),
    ])
    path.write_bytes(pdf)
    print(f"  created {path.name} ({path.stat().st_size} bytes, 1 page)")


def generate_report(path: Path) -> None:
    """A multi-page PDF resembling a real report."""
    content = bytearray()
    y = 760
    content += _heading("Annual Report 2025", y)
    y -= 40
    content += _paragraph(
        "This report summarizes the financial performance of the company "
        "for the fiscal year ending December 31, 2025.", y
    )
    y -= 30
    content += _heading("1. Executive Summary", y)
    y -= 30
    content += _paragraph(
        "Revenue grew 15% year-over-year, reaching $12.4M. Operating "
        "expenses increased 8% due to investments in research and "
        "development.", y
    )
    y -= 30
    content += _paragraph(
        "The board approved a $2M share buyback program and declared a "
        "quarterly dividend of $0.25 per share.", y
    )
    y -= 30
    content += _heading("2. Financial Highlights", y)
    y -= 30
    content += _paragraph(
        "Total assets: $18.7M  |  Net income: $3.2M  |  EPS: $1.45", y
    )
    y -= 30
    content += _paragraph("Key metrics show strong performance across all segments.", y)

    pdf = _build_pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        _page([5], 4),
        _stream(bytes(content)),
        _font(),
    ])
    path.write_bytes(pdf)
    print(f"  created {path.name} ({path.stat().st_size} bytes, 1 page, ~380 words)")


def generate_mixed_content(path: Path) -> None:
    """A PDF with various structural elements: two headings, table-like data, and a code block."""
    content = bytearray()
    content += _heading("Installation Guide", 760)
    content += _paragraph("Follow the steps below to set up the environment.", 720)
    content += _heading("Requirements", 680)

    lines = [
        "Python 3.11 or later",
        "Docker Engine 24+",
        "8 GB RAM recommended",
        "macOS, Linux, or Windows WSL2",
    ]
    y = 640
    for line in lines:
        content += _text(11, 90, y, "\u2022  " + line)
        y -= 22

    # Code block
    code = (
        "# Install dependencies\n"
        "pip install acessilia-toolbox\n\n"
        "# Start the server\n"
        "uvicorn acessilia_toolbox.api.app:create_app --factory --port 8000\n"
    )
    y -= 30
    content += _text(9, 90, y, "Example configuration:")
    y -= 18
    for line in code.split("\n"):
        content += _text(8, 100, y, line)
        y -= 14

    pdf = _build_pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        _page([5], 4),
        _stream(bytes(content)),
        _font(),
        _font("Courier"),
    ])
    path.write_bytes(pdf)
    print(f"  created {path.name} ({path.stat().st_size} bytes, 1 page, lists + code)")


def generate_multi_page(path: Path) -> None:
    """A 3-page PDF to test page-level extraction."""
    pages = []
    for page_num in range(1, 4):
        content = bytearray()
        content += _heading(f"Page {page_num}", 760)
        content += _paragraph(
            f"This is page {page_num} of a multi-page document. Each page has "
            "unique content so the extraction can be verified per page.", 720
        )
        content += _paragraph(
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
            "Sed do eiusmod tempor incididunt ut labore et dolore magna "
            "aliqua. Ut enim ad minim veniam, quis nostrud exercitation "
            "ullamco laboris nisi ut aliquip ex ea commodo consequat.", 680
        )
        pages.append(_page([5], 4 + page_num))

    kids = b" ".join(b"%d 0 R" % (3 + i) for i in range(3))

    pdf = _build_pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [" + kids + b"] /Count 3 >>",
        *pages,
        _stream(bytes(_heading("Page 1", 760) + _paragraph(
            "This is page 1 of a multi-page document. Each page has "
            "unique content so the extraction can be verified per page.", 720
        ) + _paragraph(
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
            "Sed do eiusmod tempor incididunt ut labore et dolore magna "
            "aliqua. Ut enim ad minim veniam, quis nostrud exercitation "
            "ullamco laboris nisi ut aliquip ex ea commodo consequat.", 680
        ))),
        _stream(bytes(_heading("Page 2", 760) + _paragraph(
            "This is page 2 of a multi-page document. Each page has "
            "unique content so the extraction can be verified per page.", 720
        ) + _paragraph(
            "Duis aute irure dolor in reprehenderit in voluptate velit "
            "esse cillum dolore eu fugiat nulla pariatur. Excepteur sint "
            "occaecat cupidatat non proident.", 680
        ))),
        _stream(bytes(_heading("Page 3", 760) + _paragraph(
            "This is page 3 of a multi-page document. Each page has "
            "unique content so the extraction can be verified per page.", 720
        ) + _paragraph(
            "Sunt in culpa qui officia deserunt mollit anim id est laborum. "
            "Sed perspiciatis unde omnis iste natus error sit voluptatem.", 680
        ))),
        _font(),
    ])
    path.write_bytes(pdf)
    print(f"  created {path.name} ({path.stat().st_size} bytes, 3 pages)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate sample PDFs for manual testing")
    parser.add_argument("--output", "-o", type=Path, default=Path("/tmp"),
                        help="Output directory (default: /tmp)")
    args = parser.parse_args()

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    print(f"Generating sample documents in {output} ...")
    generate_simple_document(output / "sample-simple.pdf")
    generate_report(output / "sample-report.pdf")
    generate_mixed_content(output / "sample-mixed.pdf")
    generate_multi_page(output / "sample-multi-page.pdf")
    print("Done. Run the toolbox and test with:")
    print(
        "  curl -X POST http://localhost:8000/v1/capabilities/"
        "document.structure.extract:execute \\"
    )
    print(f"    -F \"file=@{output}/sample-simple.pdf\" \\")
    print("    -F \"language=en-US\" | python3 -m json.tool | head -60")


if __name__ == "__main__":
    raise SystemExit(main())
