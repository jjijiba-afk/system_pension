"""화면 색·글꼴·숫자 서식.

이 프로그램의 숫자는 대부분 원 단위 금액이다. 억 단위로 줄여 쓰면 보기에는
편하지만 조서로 옮길 때 다시 원 단위를 찾아야 하므로, 화면에서도 **원 단위
그대로** 세 자리마다 끊어 보여 준다. 차감 항목은 계리 조서가 그렇듯 괄호로
싼다.
"""

from __future__ import annotations

import contextlib
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Any

from .. import hidpi

__all__ = [
    "ACCENT", "BAD", "BG", "CARD", "GOOD", "LINE", "MUTED", "OK_BG", "TEXT",
    "WARN", "apply_styles", "money", "num", "pct", "pick_font", "signed",
]

BG = "#F4F6FA"
CARD = "#FFFFFF"
ACCENT = "#1F3864"
TEXT = "#1B2430"
MUTED = "#5B6478"
LINE = "#D6DCE8"
GOOD = "#1E7B34"
WARN = "#B26B00"
BAD = "#B42318"
OK_BG = "#EAF3EC"

#: 한글이 깨지지 않는 순서. 앞에서부터 실제로 깔린 것을 쓴다.
_FACES = (
    "Malgun Gothic", "맑은 고딕", "NanumGothic", "Noto Sans CJK KR",
    "Noto Sans KR", "AppleGothic", "DejaVu Sans",
)
_MONO = ("Consolas", "D2Coding", "DejaVu Sans Mono", "Courier New")

_chosen: dict[str, str] = {}


def pick_font(widget: Any, mono: bool = False) -> str:
    """이 컴퓨터에 실제로 깔려 있는 글꼴 이름.

    없는 글꼴을 지정하면 Tk 가 조용히 기본 글꼴로 떨어지는데, 그 기본이
    한글을 못 그리는 환경이 있다. 그래서 목록에서 **깔린 것** 을 고른다.
    """
    key = "mono" if mono else "text"
    if key in _chosen:
        return _chosen[key]
    try:
        have = {name.lower() for name in tkfont.families(widget)}
    except tk.TclError:  # pragma: no cover - 화면이 없을 때
        have = set()
    for face in (_MONO if mono else _FACES):
        if face.lower() in have:
            _chosen[key] = face
            return face
    _chosen[key] = "TkFixedFont" if mono else "TkDefaultFont"
    return _chosen[key]


def apply_styles(root: tk.Misc) -> ttk.Style:
    """창 하나에 이 프로그램의 모양을 입힌다."""
    face = pick_font(root)
    style = ttk.Style(root)
    with contextlib.suppress(tk.TclError):
        style.theme_use("clam")

    base = (face, 9)
    style.configure(".", font=base)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("TLabel", background=BG, foreground=TEXT, font=base)
    style.configure("Card.TLabel", background=CARD, foreground=TEXT, font=base)
    style.configure("Hint.TLabel", background=BG, foreground=MUTED, font=(face, 8))
    style.configure("CardHint.TLabel", background=CARD, foreground=MUTED, font=(face, 8))
    style.configure("Title.TLabel", background=BG, foreground=ACCENT, font=(face, 15, "bold"))
    style.configure("Head.TLabel", background=BG, foreground=ACCENT, font=(face, 11, "bold"))
    style.configure("Good.TLabel", background=BG, foreground=GOOD, font=base)
    style.configure("Warn.TLabel", background=BG, foreground=WARN, font=base)
    style.configure("Bad.TLabel", background=BG, foreground=BAD, font=base)
    style.configure("Metric.TLabel", background=CARD, foreground=ACCENT,
                    font=(face, 14, "bold"))
    style.configure("MetricCap.TLabel", background=CARD, foreground=MUTED, font=(face, 8))

    style.configure("TLabelframe", background=BG, borderwidth=1, relief="solid",
                    bordercolor=LINE)
    style.configure("TLabelframe.Label", background=BG, foreground=ACCENT,
                    font=(face, 10, "bold"))
    style.configure("TCheckbutton", background=BG, font=base)
    style.configure("TRadiobutton", background=BG, font=base)
    style.configure("TButton", font=base, padding=(10, 4))
    style.configure("Run.TButton", font=(face, 11, "bold"), padding=(20, 9),
                    background=ACCENT, foreground="#FFFFFF", borderwidth=0)
    style.map("Run.TButton",
              background=[("active", "#2E4E7E"), ("disabled", "#A9B2C4")],
              foreground=[("disabled", "#EDEFF4")])
    style.configure("Link.TButton", font=(face, 8), padding=(4, 1))
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", font=(face, 10), padding=(14, 7))
    style.map("TNotebook.Tab",
              background=[("selected", CARD)], foreground=[("selected", ACCENT)])
    style.configure("TProgressbar", troughcolor="#DDE3EE", background="#4472C4",
                    borderwidth=0, thickness=hidpi.px(root, 16))
    style.configure("TEntry", padding=3)
    style.configure("Treeview", font=base, rowheight=hidpi.px(root, 22),
                    background=CARD, fieldbackground=CARD)
    style.configure("Treeview.Heading", font=(face, 9, "bold"), padding=(4, 3))
    return style


# ── 숫자 서식 ────────────────────────────────────────────────────

def money(value: Any, blank: str = "-") -> str:
    """원 단위 금액. 음수는 괄호로 싼다."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return blank
    if number == 0:
        return "0"
    if number < 0:
        return f"({abs(number):,.0f})"
    return f"{number:,.0f}"


def signed(value: Any, blank: str = "-") -> str:
    """증감처럼 부호가 뜻을 갖는 자리. 늘어난 쪽에 ``+`` 를 붙인다."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return blank
    if number == 0:
        return "0"
    return f"{number:+,.0f}"


def pct(value: Any, digits: int = 3, blank: str = "-") -> str:
    try:
        return f"{float(value):.{digits}%}"
    except (TypeError, ValueError):
        return blank


def num(value: Any, digits: int = 1, blank: str = "-") -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return blank
