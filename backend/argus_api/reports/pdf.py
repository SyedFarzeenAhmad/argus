"""A deliberately tiny PDF writer for one-page reports.

Incident reports and work-order sheets are text and a few rules; a full layout library would
be a heavy dependency for that. This emits valid PDF 1.4 with the standard Helvetica fonts,
which every viewer ships, so nothing is embedded or fetched.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field

PAGE_W, PAGE_H = 595.0, 842.0  # A4 in points
MARGIN = 50.0


def _esc(s: str) -> str:
    s = s.encode("cp1252", "replace").decode("cp1252")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


@dataclass
class Page:
    ops: list[str] = field(default_factory=list)
    y: float = PAGE_H - MARGIN

    def text(self, s: str, size: float = 10.0, bold: bool = False, indent: float = 0.0) -> None:
        font = "F2" if bold else "F1"
        width_chars = int((PAGE_W - 2 * MARGIN - indent) / (size * 0.5))
        for line in textwrap.wrap(s, width=max(20, width_chars)) or [""]:
            self.ops.append(
                f"BT /{font} {size:.1f} Tf {MARGIN + indent:.1f} {self.y:.1f} Td "
                f"({_esc(line)}) Tj ET"
            )
            self.y -= size * 1.35

    def gap(self, pts: float = 6.0) -> None:
        self.y -= pts

    def rule(self) -> None:
        self.ops.append(f"0.6 w {MARGIN:.1f} {self.y:.1f} m {PAGE_W - MARGIN:.1f} {self.y:.1f} l S")
        self.y -= 10

    def kv(self, key: str, value: object) -> None:
        self.text(f"{key}: {value}", size=10)

    @property
    def full(self) -> bool:
        return self.y < MARGIN + 20


class Document:
    def __init__(self) -> None:
        self.pages: list[Page] = [Page()]

    @property
    def page(self) -> Page:
        if self.pages[-1].full:
            self.pages.append(Page())
        return self.pages[-1]

    def render(self) -> bytes:
        objs: list[bytes] = []

        def add(body: bytes) -> int:
            objs.append(body)
            return len(objs)

        font1 = add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
        )
        font2 = add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
            b"/Encoding /WinAnsiEncoding >>"
        )
        pages_id = len(objs) + 2 * len(self.pages) + 1
        kids = []
        for p in self.pages:
            stream = "\n".join(p.ops).encode("cp1252", "replace")  # = WinAnsiEncoding
            content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
            kids.append(
                add(
                    f"<< /Type /Page /Parent {pages_id} 0 R "
                    f"/MediaBox [0 0 {PAGE_W:.0f} {PAGE_H:.0f}] "
                    f"/Resources << /Font << /F1 {font1} 0 R /F2 {font2} 0 R >> >> "
                    f"/Contents {content} 0 R >>".encode()
                )
            )
        add(
            f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] "
            f"/Count {len(kids)} >>".encode()
        )
        catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objs, start=1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()
        out += (
            f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n"
        ).encode()
        return bytes(out)
