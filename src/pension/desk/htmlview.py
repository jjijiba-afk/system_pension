"""평가보고서를 이 창 안에서 읽는다.

보고서 본문은 :mod:`pension.webreport` 가 만든다 — 인쇄용 HTML 한 벌이다.
같은 내용을 화면용으로 한 번 더 짜면 두 벌이 어긋나므로, **그 HTML 을 그대로
받아** 글자로 옮겨 그린다. 인쇄물과 화면이 한 소스에서 나온다.

표는 고정폭 글꼴로 칸을 맞춰 그린다. 그대로 긁어서 메일이나 조서에 붙여도
모양이 흐트러지지 않는다.
"""

from __future__ import annotations

import tkinter as tk
from html.parser import HTMLParser
from tkinter import ttk
from typing import Any

from .. import hidpi
from . import theme

__all__ = ["Reader", "to_blocks"]

_SKIP = {"style", "script", "head", "title"}
_BLOCK = {"p", "div", "h1", "h2", "h3", "li", "dt", "dd", "section", "tr"}


class _Reader(HTMLParser):
    """HTML → (종류, 내용) 목록.

    종류는 ``h1``·``h2``·``h3``·``p``·``note``·``list``·``table`` 중 하나다.
    표는 ``("table", [줄, 줄, …])`` 로, 첫 줄이 머리글이다.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, Any]] = []
        self._skip = 0
        self._kind = ""
        self._buffer: list[str] = []
        self._classes: list[str] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    # ── 흐름 ────────────────────────────────────────────────────
    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        classes = dict(attrs).get("class", "") or ""
        if tag == "table":
            self._flush()
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("th", "td") and self._row is not None:
            self._cell = []
        elif tag == "br":
            self._buffer.append("\n")
        elif tag in _BLOCK:
            self._flush()
            self._classes = classes.split()
            if tag in ("h1", "h2", "h3"):
                self._kind = tag
            elif tag == "li":
                self._kind = "list"
            elif tag == "dt":
                self._kind = "h3"
            elif "unit" in self._classes or "note" in self._classes:
                self._kind = "note"
            else:
                self._kind = "p"

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag in ("th", "td") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(cell for cell in self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.blocks.append(("table", self._table))
            self._table = None
        elif tag in _BLOCK:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._cell is not None:
            self._cell.append(data)
        elif self._table is None:
            self._buffer.append(data)

    # ── 마무리 ──────────────────────────────────────────────────
    def _flush(self) -> None:
        text = "".join(self._buffer)
        self._buffer = []
        lines = [" ".join(part.split()) for part in text.split("\n")]
        body = "\n".join(line for line in lines if line)
        if body:
            self.blocks.append((self._kind or "p", body))
        self._kind = ""
        self._classes = []

    def close(self) -> None:
        super().close()
        self._flush()


def to_blocks(html: str) -> list[tuple[str, Any]]:
    """보고서 HTML 한 벌을 화면이 그릴 블록 목록으로."""
    reader = _Reader()
    reader.feed(html)
    reader.close()
    return reader.blocks


def _widths(rows: list[list[str]]) -> list[int]:
    count = max(len(row) for row in rows)
    widths = [0] * count
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], _width(cell))
    return widths


def _width(cell: str) -> int:
    """한글은 두 칸을 먹는다. 그걸 세지 않으면 칸이 어긋난다."""
    return sum(2 if ord(ch) > 0x2000 else 1 for ch in cell)


def _pad(cell: str, width: int, right: bool) -> str:
    gap = " " * max(0, width - _width(cell))
    return (gap + cell) if right else (cell + gap)


class Reader(ttk.Frame):
    """보고서를 읽는 자리. :meth:`show` 에 HTML 을 넘기면 그려 준다."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.text = tk.Text(self, wrap="word", relief="flat", bg=theme.CARD,
                            padx=hidpi.px(self, 22), pady=hidpi.px(self, 16),
                            spacing1=2, spacing3=3, cursor="arrow")
        self.text.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=bar.set)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        face = theme.pick_font(self)
        mono = theme.pick_font(self, mono=True)
        self.text.tag_configure("h1", font=(face, 15, "bold"), foreground=theme.ACCENT,
                                spacing1=hidpi.px(self, 14), spacing3=hidpi.px(self, 8))
        self.text.tag_configure("h2", font=(face, 12, "bold"), foreground=theme.ACCENT,
                                spacing1=hidpi.px(self, 16), spacing3=hidpi.px(self, 6))
        self.text.tag_configure("h3", font=(face, 10, "bold"), foreground="#44546A",
                                spacing1=hidpi.px(self, 10), spacing3=hidpi.px(self, 3))
        self.text.tag_configure("p", font=(face, 9), foreground=theme.TEXT,
                                spacing3=hidpi.px(self, 5))
        self.text.tag_configure("note", font=(face, 8), foreground=theme.MUTED)
        self.text.tag_configure("list", font=(face, 9), foreground=theme.TEXT,
                                lmargin1=hidpi.px(self, 14), lmargin2=hidpi.px(self, 26))
        self.text.tag_configure("table", font=(mono, 9), foreground=theme.TEXT,
                                spacing1=0, spacing3=0)
        self.text.tag_configure("thead", font=(mono, 9, "bold"),
                                foreground=theme.ACCENT, spacing1=hidpi.px(self, 6))
        self.text.configure(state="disabled")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def show(self, html: str) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        for kind, payload in to_blocks(html):
            if kind == "table":
                self._table(payload)
            elif kind == "list":
                self.text.insert("end", f"· {payload}\n", "list")
            else:
                self.text.insert("end", payload + "\n", kind)
        self.text.configure(state="disabled")
        self.text.yview_moveto(0.0)

    def _table(self, rows: list[list[str]]) -> None:
        widths = _widths(rows)
        head, *body = rows
        line = "  ".join(_pad(cell, widths[i], False) for i, cell in enumerate(head))
        self.text.insert("end", line.rstrip() + "\n", "thead")
        self.text.insert("end", "─" * min(_width(line), 110) + "\n", "note")
        for row in body:
            cells = [
                _pad(cell, widths[index], index > 0)
                for index, cell in enumerate(row)
            ]
            self.text.insert("end", "  ".join(cells).rstrip() + "\n", "table")
        self.text.insert("end", "\n", "table")
