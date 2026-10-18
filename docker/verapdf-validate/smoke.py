"""Build-time smoke test: validate an untagged PDF through the server's own code.

The PDF has no structure tree, so veraPDF must report it as not compliant with
PDF/UA-1. Passing proves the JRE, veraPDF, its JSON report and the
normalization all work together.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import server


def minimal_pdf() -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
    ]
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "document.pdf"
        pdf.write_bytes(minimal_pdf())
        report = server.run_verapdf(pdf, "ua1")
    assert report["compliant"] is False, report
    assert report["failures"], report
    assert server.toolchain()["verapdf"], "veraPDF did not report its version"
    print(f"veraPDF {server.toolchain()['verapdf']}: {report['statement']}")


if __name__ == "__main__":
    main()
