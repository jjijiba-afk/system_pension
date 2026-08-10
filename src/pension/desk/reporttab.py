"""계리평가 보고서 탭.

본문은 :mod:`pension.webreport` 한 곳에서 나온다. 화면에서 읽는 것도, 파일로
저장해 인쇄하는 것도 같은 한 벌이다. 화면용을 따로 짜면 회의에서 본 숫자와
제출한 PDF 가 달라질 수 있다.
"""

from __future__ import annotations

import datetime as _dt
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .files import open_with_default, print_file
from .htmlview import Reader
from .hub import Hub

__all__ = ["ReportTab"]

_KINDS = (("퇴직급여", "severance"), ("기타장기종업원급여", "longterm"))


class ReportTab(ttk.Frame):
    """보고서 화면."""

    def __init__(self, parent: tk.Misc, hub: Hub) -> None:
        super().__init__(parent)
        self.hub = hub
        self.kind = tk.StringVar(value="severance")
        self.client = tk.StringVar()
        self.period_start = tk.StringVar()
        self._html = ""

        bar = ttk.Frame(self, padding=(14, 10, 14, 6))
        bar.pack(fill="x")
        ttk.Label(bar, text="종류").pack(side="left")
        for label, value in _KINDS:
            ttk.Radiobutton(bar, text=label, value=value, variable=self.kind,
                            command=self.refresh).pack(side="left", padx=(6, 8))
        ttk.Label(bar, text="표지 단체명").pack(side="left", padx=(14, 4))
        ttk.Entry(bar, textvariable=self.client, width=20).pack(side="left")
        ttk.Label(bar, text="기간 시작").pack(side="left", padx=(14, 4))
        ttk.Entry(bar, textvariable=self.period_start, width=13).pack(side="left")
        ttk.Button(bar, text="다시 만들기", command=self.refresh).pack(side="left", padx=(10, 0))
        ttk.Button(bar, text="파일로 저장", command=self.save).pack(side="right")
        ttk.Button(bar, text="인쇄", command=self.print_out).pack(side="right", padx=(0, 6))

        self.status = ttk.Label(self, text="", style="Hint.TLabel", padding=(14, 0, 14, 6))
        self.status.pack(fill="x")

        self.reader = Reader(self)
        self.reader.pack(fill="both", expand=True, padx=14, pady=(0, 12))

        hub.watch("run", self.refresh)
        hub.watch("client", self._fill_client)
        self._fill_client()
        self._say("아직 산출 결과가 없습니다. [산출] 탭에서 실행하면 여기 보고서가 만들어집니다.")

    def _fill_client(self) -> None:
        if not self.client.get():
            self.client.set(self.hub.client())

    def _say(self, message: str, bad: bool = False) -> None:
        self.status.configure(text=message, style="Bad.TLabel" if bad else "Hint.TLabel")

    def refresh(self) -> None:
        from ..webreport import render_html

        run = self.hub.run
        if run is None:
            self.reader.clear()
            self._html = ""
            self._say("아직 산출 결과가 없습니다. [산출] 탭에서 실행하면 여기 보고서가 만들어집니다.")
            return

        start = None
        token = self.period_start.get().strip()
        if token:
            from .calc import parse_date

            start = parse_date(token)
            if start is None:
                self._say(f"기간 시작일을 읽지 못했습니다: {token} (예: 2024-01-01)", bad=True)
                return

        try:
            self._html = render_html(run, kind=self.kind.get(),
                                     client=self.client.get().strip(), period_start=start)
        except ValueError as exc:
            self.reader.clear()
            self._html = ""
            self._say(str(exc), bad=True)
            return

        self.reader.show(self._html)
        self._say("이 화면과 [파일로 저장] 한 보고서는 같은 한 벌입니다. "
                  "인쇄 대화상자에서 프린터 대신 'PDF로 저장' 을 고르면 PDF 가 나옵니다.")

    # ── 내보내기 ────────────────────────────────────────────────

    def _default_name(self) -> str:
        run = self.hub.run
        base = getattr(getattr(run, "config", None), "base_date", _dt.date.today())
        word = "퇴직급여" if self.kind.get() == "severance" else "장기종업원급여"
        client = self.client.get().strip() or "평가"
        return f"{client}_{word}_평가보고서_{base}.html"

    def save(self) -> Path | None:
        if not self._html:
            messagebox.showinfo("보고서", "먼저 보고서를 만드세요.", parent=self)
            return None
        path = filedialog.asksaveasfilename(
            title="보고서 저장", defaultextension=".html",
            initialfile=self._default_name(),
            filetypes=[("웹 문서", "*.html"), ("모든 파일", "*.*")], parent=self)
        if not path:
            return None
        target = Path(path)
        target.write_text(self._html, encoding="utf-8")
        self._say(f"저장했습니다: {target}")
        return target

    def print_out(self) -> None:
        """인쇄로 넘긴다. 종이에 그리는 것은 운영체제의 일이다."""
        if not self._html:
            messagebox.showinfo("보고서", "먼저 보고서를 만드세요.", parent=self)
            return
        from ..library import library_dir

        folder = library_dir() / "인쇄"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / self._default_name()
        target.write_text(self._html, encoding="utf-8")

        if print_file(target):
            self._say(f"인쇄로 넘겼습니다: {target}")
        elif open_with_default(target):
            self._say("인쇄 화면을 띄우지 못해 기본 프로그램으로 열었습니다. "
                      "그 화면에서 인쇄(Ctrl+P) → 'PDF로 저장' 을 고르세요.")
        else:
            self._say(f"인쇄로 넘길 프로그램을 찾지 못했습니다. 이 파일을 직접 여세요: {target}",
                      bad=True)
