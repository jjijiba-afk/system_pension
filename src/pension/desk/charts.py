"""Canvas 로 그리는 그림.

바깥 그래프 라이브러리를 쓰지 않는다. 담당자 PC 에 추가 설치 없이 EXE 하나로
돌아야 하기 때문이다. 필요한 그림은 세 가지뿐이라 직접 그리는 편이 싸다 —
가로 막대(구성비), 세로 기둥(만기), 꺾은선(곡선).

숫자를 지어내지 않는다. 축의 최대값과 눈금만 여기서 정하고, 값은 받은 것을
그대로 그린다.
"""

from __future__ import annotations

import math
import tkinter as tk
from collections.abc import Callable, Mapping, Sequence
from tkinter import ttk
from typing import Any

from .. import hidpi
from . import theme

__all__ = ["SERIES_COLORS", "Bars", "Columns", "Lines", "nice_ceiling"]

SERIES_COLORS = (
    "#4472C4", "#ED7D31", "#1E7B34", "#B42318", "#7B5EA7", "#0F8B8D",
    "#B26B00", "#5B6478",
)


def nice_ceiling(value: float) -> float:
    """축 위쪽을 사람이 읽기 좋은 수로 올린다 (1·2·2.5·5·10 × 10ⁿ)."""
    if value <= 0:
        return 1.0
    exponent = math.floor(math.log10(value))
    unit = 10.0 ** exponent
    for step in (1.0, 2.0, 2.5, 5.0, 10.0):
        if value <= step * unit * 1.0000001:
            return step * unit
    return 10.0 * unit


class _Chart(ttk.Frame):
    """크기가 바뀌면 다시 그리는 캔버스."""

    def __init__(self, parent: tk.Misc, *, height: int = 220,
                 title: str = "", background: str = theme.CARD) -> None:
        super().__init__(parent)
        self.title = title
        if title:
            ttk.Label(self, text=title, style="Head.TLabel").pack(anchor="w",
                                                                 pady=(0, 4))
        self.canvas = tk.Canvas(self, height=hidpi.px(self, height), bg=background,
                                highlightthickness=1, highlightbackground=theme.LINE)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        self._font = (theme.pick_font(self), 8)
        self._bold = (theme.pick_font(self), 8, "bold")

    # 부르는 쪽이 채운다.
    def _paint(self, width: int, height: int) -> None:  # pragma: no cover - 추상
        raise NotImplementedError

    def redraw(self) -> None:
        self.canvas.delete("all")
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width < 40 or height < 30:
            return
        self._paint(width, height)

    def _empty(self, width: int, height: int, message: str) -> None:
        self.canvas.create_text(width / 2, height / 2, text=message,
                                fill=theme.MUTED, font=self._font)


class Bars(_Chart):
    """가로 막대. 이름이 긴 한글 항목(직군·규정명)에 맞는 모양이다."""

    def __init__(self, parent: tk.Misc, *, height: int = 220, title: str = "",
                 fmt: Callable[[float], str] = theme.money) -> None:
        super().__init__(parent, height=height, title=title)
        self._fmt = fmt
        self._rows: list[tuple[str, float]] = []

    def show(self, rows: Sequence[tuple[str, float]]) -> None:
        self._rows = [(str(name), float(value)) for name, value in rows]
        self.redraw()

    def _paint(self, width: int, height: int) -> None:
        if not self._rows:
            self._empty(width, height, "표시할 값이 없습니다")
            return
        pad = hidpi.px(self, 8)
        label_w = hidpi.px(self, 92)
        value_w = hidpi.px(self, 104)
        top = pad
        usable = height - pad * 2
        step = usable / len(self._rows)
        bar_h = max(hidpi.px(self, 8), min(hidpi.px(self, 22), step * 0.62))
        span = max(1.0, width - label_w - value_w - pad * 2)
        ceiling = nice_ceiling(max(abs(v) for _n, v in self._rows) or 1.0)

        for index, (name, value) in enumerate(self._rows):
            middle = top + step * index + step / 2
            self.canvas.create_text(label_w, middle, text=name, anchor="e",
                                    fill=theme.TEXT, font=self._font)
            length = span * min(1.0, abs(value) / ceiling)
            colour = SERIES_COLORS[index % len(SERIES_COLORS)]
            x0 = label_w + pad
            self.canvas.create_rectangle(
                x0, middle - bar_h / 2, x0 + max(1.0, length), middle + bar_h / 2,
                fill=colour, outline="")
            self.canvas.create_text(x0 + length + hidpi.px(self, 6), middle,
                                    text=self._fmt(value), anchor="w",
                                    fill=theme.TEXT, font=self._font)


class Columns(_Chart):
    """세로 기둥. 만기분석처럼 칸이 많고 순서가 뜻을 갖는 자리."""

    def __init__(self, parent: tk.Misc, *, height: int = 240, title: str = "",
                 fmt: Callable[[float], str] = theme.money) -> None:
        super().__init__(parent, height=height, title=title)
        self._fmt = fmt
        self._rows: list[tuple[str, list[float]]] = []
        self._names: list[str] = []

    def show(self, labels: Sequence[str], series: Mapping[str, Sequence[float]]) -> None:
        self._names = list(series)
        self._rows = [(str(label), [float(series[name][i]) for name in self._names])
                      for i, label in enumerate(labels)]
        self.redraw()

    def _paint(self, width: int, height: int) -> None:
        if not self._rows:
            self._empty(width, height, "표시할 값이 없습니다")
            return
        pad = hidpi.px(self, 10)
        axis_w = hidpi.px(self, 74)
        legend_h = hidpi.px(self, 16) if len(self._names) > 1 else 0
        floor = height - pad - hidpi.px(self, 16)
        top = pad + legend_h
        peak = max((max(values) for _l, values in self._rows), default=0.0)
        ceiling = nice_ceiling(peak or 1.0)
        plot_w = width - axis_w - pad
        step = plot_w / len(self._rows)
        group = len(self._names) or 1
        bar_w = max(1.0, (step * 0.66) / group)

        for tick in range(5):
            value = ceiling * tick / 4
            y = floor - (floor - top) * tick / 4
            self.canvas.create_line(axis_w, y, width - pad, y, fill="#EEF1F7")
            self.canvas.create_text(axis_w - hidpi.px(self, 4), y,
                                    text=self._fmt(value), anchor="e",
                                    fill=theme.MUTED, font=self._font)

        for index, (label, values) in enumerate(self._rows):
            centre = axis_w + step * index + step / 2
            for slot, value in enumerate(values):
                bar_top = floor - (floor - top) * min(1.0, value / ceiling)
                x0 = centre - (group * bar_w) / 2 + slot * bar_w
                self.canvas.create_rectangle(
                    x0, bar_top, x0 + bar_w * 0.9, floor,
                    fill=SERIES_COLORS[slot % len(SERIES_COLORS)], outline="")
            if len(self._rows) <= 24 or index % 2 == 0:
                self.canvas.create_text(centre, floor + hidpi.px(self, 8),
                                        text=label, fill=theme.MUTED,
                                        font=self._font)

        for slot, name in enumerate((self._names[1:] and self._names) or []):
            x = axis_w + slot * hidpi.px(self, 88)
            self.canvas.create_rectangle(x, pad, x + hidpi.px(self, 10),
                                         pad + hidpi.px(self, 10),
                                         fill=SERIES_COLORS[slot % len(SERIES_COLORS)],
                                         outline="")
            self.canvas.create_text(x + hidpi.px(self, 14), pad + hidpi.px(self, 5),
                                    text=name, anchor="w", fill=theme.TEXT,
                                    font=self._font)


class Lines(_Chart):
    """꺾은선 여러 벌. 연령별 퇴직률·승급률·사망률처럼 x 가 숫자인 곡선."""

    def __init__(self, parent: tk.Misc, *, height: int = 240, title: str = "",
                 fmt: Callable[[float], str] = lambda v: f"{v:.2%}",
                 x_label: str = "") -> None:
        super().__init__(parent, height=height, title=title)
        self._fmt = fmt
        self._x_label = x_label
        self._series: dict[str, dict[float, float]] = {}

    def show(self, series: Mapping[str, Mapping[Any, Any]], *,
             x_label: str = "") -> None:
        cleaned: dict[str, dict[float, float]] = {}
        for name, points in series.items():
            got = {}
            for key, value in points.items():
                try:
                    got[float(key)] = float(value)
                except (TypeError, ValueError):
                    continue
            if got:
                cleaned[str(name)] = dict(sorted(got.items()))
        self._series = cleaned
        if x_label:
            self._x_label = x_label
        self.redraw()

    def _paint(self, width: int, height: int) -> None:
        if not self._series:
            self._empty(width, height, "등록된 곡선이 없습니다")
            return
        pad = hidpi.px(self, 10)
        axis_w = hidpi.px(self, 62)
        legend_h = hidpi.px(self, 16)
        floor = height - pad - hidpi.px(self, 14)
        top = pad + legend_h

        xs = [x for points in self._series.values() for x in points]
        ys = [y for points in self._series.values() for y in points.values()]
        x_lo, x_hi = min(xs), max(xs)
        if x_hi <= x_lo:
            x_hi = x_lo + 1
        ceiling = nice_ceiling(max(ys) or 1e-6)
        plot_w = width - axis_w - pad

        for tick in range(5):
            value = ceiling * tick / 4
            y = floor - (floor - top) * tick / 4
            self.canvas.create_line(axis_w, y, width - pad, y, fill="#EEF1F7")
            self.canvas.create_text(axis_w - hidpi.px(self, 4), y, text=self._fmt(value),
                                    anchor="e", fill=theme.MUTED, font=self._font)

        for tick in range(5):
            value = x_lo + (x_hi - x_lo) * tick / 4
            x = axis_w + plot_w * tick / 4
            self.canvas.create_text(x, floor + hidpi.px(self, 7),
                                    text=f"{value:g}", fill=theme.MUTED,
                                    font=self._font)
        if self._x_label:
            self.canvas.create_text(width - pad, floor + hidpi.px(self, 7),
                                    text=self._x_label, anchor="e",
                                    fill=theme.MUTED, font=self._font)

        for index, (name, points) in enumerate(self._series.items()):
            colour = SERIES_COLORS[index % len(SERIES_COLORS)]
            spots: list[float] = []
            for x, y in points.items():
                px = axis_w + plot_w * (x - x_lo) / (x_hi - x_lo)
                py = floor - (floor - top) * min(1.0, y / ceiling)
                spots += [px, py]
            if len(spots) >= 4:
                self.canvas.create_line(*spots, fill=colour, width=2, smooth=False)
            elif spots:
                self.canvas.create_oval(spots[0] - 2, spots[1] - 2,
                                        spots[0] + 2, spots[1] + 2,
                                        fill=colour, outline="")
            x = axis_w + index * hidpi.px(self, 88)
            self.canvas.create_line(x, pad + hidpi.px(self, 5),
                                    x + hidpi.px(self, 12), pad + hidpi.px(self, 5),
                                    fill=colour, width=2)
            self.canvas.create_text(x + hidpi.px(self, 16), pad + hidpi.px(self, 5),
                                    text=name, anchor="w", fill=theme.TEXT,
                                    font=self._font)
