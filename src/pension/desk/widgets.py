"""탭들이 같이 쓰는 조각.

표와 스크롤은 어느 탭에나 나온다. 탭마다 따로 만들면 열 너비도 스크롤 방향도
제각각이 되므로 여기서 한 번만 정한다.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Iterable, Sequence
from tkinter import ttk
from typing import Any

from .. import hidpi
from . import theme

__all__ = ["Metrics", "ScrollFrame", "Table", "bind_wheel", "labeled", "section"]


def bind_wheel(widget: tk.Misc, target: tk.Canvas) -> None:
    """휠을 굴리면 ``target`` 이 스크롤되게 한다.

    윈도우·맥은 ``<MouseWheel>`` 에 ``delta`` 가 오고, X11 은 버튼 4·5 로 온다.
    한쪽만 걸면 리눅스에서 휠이 죽는다.
    """

    def wheel(event: Any) -> None:
        if getattr(event, "num", None) == 4:
            step = -1
        elif getattr(event, "num", None) == 5:
            step = 1
        else:
            step = -1 if event.delta > 0 else 1
        target.yview_scroll(step, "units")

    widget.bind("<MouseWheel>", wheel, add="+")
    widget.bind("<Button-4>", wheel, add="+")
    widget.bind("<Button-5>", wheel, add="+")


class ScrollFrame(ttk.Frame):
    """세로로 긴 내용을 담는 틀. 실제 내용은 :attr:`body` 안에 넣는다."""

    def __init__(self, parent: tk.Misc, *, background: str = theme.BG) -> None:
        super().__init__(parent)
        self.canvas = tk.Canvas(self, bg=background, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        bar.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=bar.set)

        self.body = ttk.Frame(self.canvas, padding=(16, 12, 16, 16))
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)
        bind_wheel(self.canvas, self.canvas)
        bind_wheel(self.body, self.canvas)

    def _on_body(self, _event: Any) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event: Any) -> None:
        # 내용을 창 너비에 맞춘다. 이걸 안 하면 표가 왼쪽에 몰려 붙는다.
        self.canvas.itemconfigure(self._window, width=event.width)

    def adopt(self, widget: tk.Misc) -> None:
        """나중에 만든 자식에게도 휠을 걸어 준다."""
        bind_wheel(widget, self.canvas)

    def to_top(self) -> None:
        self.canvas.yview_moveto(0.0)


def section(parent: tk.Misc, title: str, *, pady: tuple[int, int] = (0, 12)) -> ttk.Labelframe:
    box = ttk.Labelframe(parent, text=f" {title} ", padding=(12, 8, 12, 10))
    box.pack(fill="x", pady=pady)
    return box


def labeled(parent: tk.Misc, text: str, *, style: str = "Hint.TLabel",
            wrap: int = 640) -> ttk.Label:
    """칸 아래에 붙이는 안내 한 줄.

    칸에 따라 안은 ``grid`` 로도 ``pack`` 으로도 짜여 있다. 둘을 한 부모에
    섞으면 Tk 가 거부하므로, 이미 격자로 짜인 칸이면 마지막 줄에 이어 붙인다.
    """
    label = ttk.Label(parent, text=text, style=style, justify="left",
                      wraplength=hidpi.px(parent, wrap))
    if parent.grid_slaves():
        columns, rows = parent.grid_size()
        label.grid(row=rows, column=0, columnspan=max(1, columns), sticky="w",
                   pady=(6, 0))
    else:
        label.pack(anchor="w", pady=(4, 0))
    return label


class Table(ttk.Frame):
    """머리글 있는 표. 값은 이미 서식이 입혀진 문자열로 받는다.

    서식을 표가 정하지 않는 이유는, 같은 열에 금액과 비율이 섞여 오는 표
    (증감분석·주석)가 있기 때문이다. 무엇을 어떻게 보일지는 부르는 쪽이 안다.
    """

    def __init__(self, parent: tk.Misc, headers: Sequence[str], *,
                 widths: Sequence[int] | None = None,
                 aligns: Sequence[str] | None = None,
                 height: int = 10,
                 stretch: int = 0,
                 on_pick: Callable[[str], None] | None = None) -> None:
        super().__init__(parent)
        columns = [f"c{i}" for i in range(len(headers))]
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=height)
        for index, (column, title) in enumerate(zip(columns, headers)):
            self.tree.heading(column, text=title)
            width = (widths[index] if widths and index < len(widths) else 120)
            align = (aligns[index] if aligns and index < len(aligns) else "e")
            self.tree.column(column, width=hidpi.px(self, width), anchor=align,
                             stretch=(index == stretch))
        self.tree.grid(row=0, column=0, sticky="nsew")

        bar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=bar.set)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.tree.tag_configure("bad", foreground=theme.BAD)
        self.tree.tag_configure("warn", foreground=theme.WARN)
        self.tree.tag_configure("good", foreground=theme.GOOD)
        self.tree.tag_configure("muted", foreground=theme.MUTED)
        self.tree.tag_configure("total", background="#EEF2F9")

        self._on_pick = on_pick
        if on_pick is not None:
            self.tree.bind("<<TreeviewSelect>>", self._picked)

    def _picked(self, _event: Any) -> None:
        picked = self.selected()
        if picked and self._on_pick is not None:
            self._on_pick(picked)

    def selected(self) -> str:
        rows = self.tree.selection()
        return rows[0] if rows else ""

    def clear(self) -> None:
        self.tree.delete(*self.tree.get_children())

    def fill(self, rows: Iterable[Sequence[Any]], *,
             tags: Callable[[int, Sequence[Any]], str] | None = None,
             keys: Callable[[int, Sequence[Any]], str] | None = None) -> int:
        """줄을 통째로 갈아 끼운다. 넣은 줄 수를 돌려준다."""
        self.clear()
        count = 0
        for index, row in enumerate(rows):
            values = ["" if v is None else str(v) for v in row]
            tag = tags(index, row) if tags else ""
            key = keys(index, row) if keys else ""
            self.tree.insert("", "end", iid=key or None, values=values,
                             tags=(tag,) if tag else ())
            count += 1
        # 줄이 없으면 표를 낮춰 자리를 아끼되, 한 줄은 남겨 머리글이 붕 뜨지 않게.
        self.tree.configure(height=max(1, min(count or 1, 18)))
        return count


class Metrics(ttk.Frame):
    """제일 위에 세우는 숫자 카드 줄."""

    def __init__(self, parent: tk.Misc, *, columns: int = 4) -> None:
        super().__init__(parent)
        self._columns = columns
        self._cells: list[tuple[ttk.Label, ttk.Label]] = []

    def show(self, items: Sequence[tuple[str, str]]) -> None:
        for child in self.winfo_children():
            child.destroy()
        self._cells.clear()
        for index, (caption, value) in enumerate(items):
            row, column = divmod(index, self._columns)
            card = ttk.Frame(self, style="Card.TFrame", padding=(12, 8, 12, 9))
            card.grid(row=row, column=column, sticky="nsew", padx=(0, 8), pady=(0, 8))
            cap = ttk.Label(card, text=caption, style="MetricCap.TLabel")
            cap.pack(anchor="w")
            val = ttk.Label(card, text=value, style="Metric.TLabel")
            val.pack(anchor="w")
            self._cells.append((cap, val))
        for column in range(self._columns):
            self.columnconfigure(column, weight=1, uniform="metric")
