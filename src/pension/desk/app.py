"""본 화면.

프로그램을 켜면 이 창이 뜬다. 산출부터 보고서까지 **이 창 안에서** 끝난다 —
다른 화면을 따로 띄우지 않는다. 브라우저도 쓰지 않으므로 명부가 이 컴퓨터
밖으로 나갈 길이 없다.

탭 순서는 일하는 순서다. 자료를 걸고(산출) → 가정을 맞추고(산출가정 입력) →
결과를 보고(분석·보고서·사번 조회) → 남긴다(산출 내역). 자료실은 여러 단체를
오갈 때 쓰는 서랍이라 맨 뒤에 둔다.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import hidpi
from . import theme
from .analysis import AnalysisTab
from .calc import CalcTab
from .hub import Hub
from .libtab import LibraryTab
from .membertab import MemberTab
from .reporttab import ReportTab
from .runstab import RunsTab

__all__ = ["DeskApp", "main"]

APP_TITLE = "연금계리 산출 시스템"
APP_VERSION = "1.0"
SUBTITLE = "K-IFRS 1019호 예측단위적립방식(PUC) — 확정급여채무·근무원가·민감도·증감분석"


class DeskApp(tk.Tk):
    """본 창."""

    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_TITLE}  v{APP_VERSION}")
        # 배율은 창을 만든 직후에 먹인다. 크기·최소크기는 픽셀이라 함께 키워야
        # 200% 화면에서 절반 크기로 뜨지 않는다.
        self.scale = hidpi.apply(self)
        self.geometry(hidpi.scale_geometry(self, "1180x900"))
        self.minsize(hidpi.px(self, 960), hidpi.px(self, 700))
        self.configure(bg=theme.BG)
        theme.apply_styles(self)

        self.hub = Hub()
        self.client = tk.StringVar()

        self._header()
        self._tabs()
        self._refresh_clients()
        self.hub.watch("client", self._refresh_clients)
        self.hub.watch("run", self._refresh_title)

    # ── 위쪽 ────────────────────────────────────────────────────

    def _header(self) -> None:
        head = ttk.Frame(self, padding=(18, 12, 18, 6))
        head.pack(fill="x")
        head.columnconfigure(0, weight=1)

        left = ttk.Frame(head)
        left.grid(row=0, column=0, sticky="w")
        ttk.Label(left, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(left, text=SUBTITLE, style="Hint.TLabel").pack(anchor="w", pady=(2, 0))

        right = ttk.Frame(head)
        right.grid(row=0, column=1, sticky="e")
        ttk.Label(right, text="단체").pack(side="left")
        self._client_box = ttk.Combobox(right, textvariable=self.client,
                                        state="readonly", width=22)
        self._client_box.pack(side="left", padx=(6, 6))
        self._client_box.bind("<<ComboboxSelected>>", self._pick_client)
        ttk.Button(right, text="단체 관리",
                   command=lambda: self.show("자료실")).pack(side="left")

    def _refresh_clients(self) -> None:
        from .. import clients

        names = clients.names()
        self._client_box.configure(values=names)
        self.client.set(clients.current())

    def _pick_client(self, _event: object = None) -> None:
        self.hub.select_client(self.client.get())

    def _refresh_title(self) -> None:
        loaded = self.hub.loaded
        if loaded is None:
            self.title(f"{APP_TITLE}  v{APP_VERSION}")
            return
        base = getattr(getattr(loaded.run, "config", None), "base_date", "")
        tail = loaded.name or str(base)
        self.title(f"{APP_TITLE}  v{APP_VERSION}   —   {self.hub.client()} · {tail}")

    # ── 탭 ──────────────────────────────────────────────────────

    def _tabs(self) -> None:
        from ..editor import AssumptionsEditor

        self.book = ttk.Notebook(self, padding=(10, 4, 10, 8))
        self.book.pack(fill="both", expand=True)

        self.calc = CalcTab(self.book, self.hub)
        self.editor = AssumptionsEditor(
            self.book, standalone=False, on_saved=self._editor_saved)
        self.analysis = AnalysisTab(self.book, self.hub)
        self.report = ReportTab(self.book, self.hub)
        self.member = MemberTab(self.book, self.hub)
        self.runs = RunsTab(self.book, self.hub)
        self.library = LibraryTab(self.book, self.hub)

        self._pages = {
            "산출": self.calc,
            "산출가정 입력": self.editor,
            "분석": self.analysis,
            "계리평가 보고서": self.report,
            "사번 조회": self.member,
            "산출 내역": self.runs,
            "자료실": self.library,
        }
        for title, page in self._pages.items():
            self.book.add(page, text=f"  {title}  ")

    def show(self, title: str) -> None:
        page = self._pages.get(title)
        if page is not None:
            self.book.select(page)

    def _editor_saved(self, editor) -> None:
        """가정 입력 탭에서 저장하면 산출 탭이 그 파일을 이어받는다."""
        if editor.path is not None:
            self.calc.adopt_assumptions(editor.path)


def main() -> int:
    """본 화면 진입점."""
    # 창을 만들기 **전** 이어야 한다. Tk 가 뜬 뒤에는 윈도우가 이미 '늘려서
    # 그리기' 로 정해 버려서, 나중에 선언해도 흐린 채로 남는다.
    hidpi.declare_dpi_aware()
    try:
        app = DeskApp()
    except tk.TclError as exc:  # pragma: no cover - 화면이 없는 환경
        print(f"화면을 띄우지 못했습니다: {exc}")
        return 1
    app.mainloop()
    return 0
