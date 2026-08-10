"""사번 조회 탭.

전체 산출은 몇백 명의 합계라 "이 사람 채무가 왜 이 금액인가" 에 답하지 못한다.
사번 하나를 같은 명부·기초율로 **다시 산출** 해 연차별 근거를 한 줄씩 보여
준다. 여기 나온 채무의 합은 전체 산출의 그 사람 몫과 원 단위까지 같다.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any

from . import theme
from .hub import Hub
from .widgets import ScrollFrame, Table, labeled, section

__all__ = ["MemberTab"]

_TRACE_HEADERS = ("연차", "시점", "연령", "근속", "임금", "퇴직사유",
                  "탈퇴확률", "지급액", "귀속액", "할인율", "채무", "근무원가")


class MemberTab(ttk.Frame):
    """사번 조회 화면."""

    def __init__(self, parent: tk.Misc, hub: Hub) -> None:
        super().__init__(parent)
        self.hub = hub
        self.employee = tk.StringVar()
        self._sources: tuple[str, str] | None = None

        shell = ScrollFrame(self)
        shell.pack(fill="both", expand=True)
        self._shell = shell
        body = shell.body

        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="사번 (또는 성명)").pack(side="left")
        entry = ttk.Entry(bar, textvariable=self.employee, width=20)
        entry.pack(side="left", padx=(8, 6))
        entry.bind("<Return>", lambda _e: self.look_up())
        ttk.Button(bar, text="조회", command=self.look_up).pack(side="left")
        self.status = ttk.Label(bar, text="", style="Hint.TLabel")
        self.status.pack(side="left", padx=(12, 0))

        self.summary = section(body, "합계")
        self.total_table = Table(self.summary, ["구분", "금액"], widths=[240, 190],
                                 aligns=["w", "e"], height=5)
        self.total_table.pack(fill="x")
        labeled(self.summary, "같은 사번이 지급 구간으로 여러 줄 나뉘어 있으면 "
                              "구간별 채무를 모두 더한 값입니다.")

        self._rows = ttk.Frame(body)
        self._rows.pack(fill="both", expand=True)

        self.retired_box = section(body, "퇴직자명부에서")
        self.retired_table = Table(
            self.retired_box, ["사번", "성명", "퇴직일자", "퇴직사유", "지급총액",
                               "사외적립 지급", "장기급여 지급"],
            widths=[90, 80, 100, 110, 140, 130, 130],
            aligns=["w", "w", "w", "w", "e", "e", "e"], height=4)
        self.retired_table.pack(fill="x")
        self.retired_box.pack_forget()

        hub.watch("run", self._on_run)
        self._on_run()

    # ── 원본 ────────────────────────────────────────────────────

    def _on_run(self) -> None:
        """어떤 명부·기초율로 조회할지 정한다."""
        loaded = self.hub.loaded
        options = getattr(loaded, "options", None) if loaded else None
        if options is not None:
            self._sources = (str(options.roster_path), str(options.assumptions_path))
            self._say(f"'{Path(options.roster_path).name}' 로 조회합니다.")
        elif loaded is not None and loaded.name:
            try:
                from .. import runs

                restored = runs.restore(loaded.name)
            except (OSError, ValueError):
                self._sources = None
            else:
                self._sources = (str(restored["roster"]), str(restored["assumptions"]))
                self._say(f"저장된 산출 '{loaded.name}' 의 명부로 조회합니다.")
        else:
            self._sources = None
            self._say("먼저 [산출] 탭에서 산출하거나 [산출 내역] 에서 불러오세요.")

    def _say(self, message: str, bad: bool = False) -> None:
        self.status.configure(text=message, style="Bad.TLabel" if bad else "Hint.TLabel")

    # ── 조회 ────────────────────────────────────────────────────

    def look_up(self) -> None:
        from ..memberdetail import lookup

        if self._sources is None:
            self._say("조회할 명부가 없습니다. 먼저 산출하세요.", bad=True)
            return
        needle = self.employee.get().strip()
        if not needle:
            self._say("사번을 입력하세요.", bad=True)
            return

        loaded = self.hub.loaded
        base_date = getattr(getattr(loaded, "options", None), "base_date", None)
        self._say("조회 중…")
        self.update_idletasks()
        try:
            found = lookup(self._sources[0], self._sources[1], needle, base_date=base_date)
        except Exception as exc:
            self._clear()
            self._say(str(exc), bad=True)
            return

        self._show(found)
        self._say(f"기준일 {found['base_date']}   ·   "
                  f"적용 할인율 {theme.pct(found['discount_rate'])}")
        self._shell.to_top()

    def _clear(self) -> None:
        self.total_table.clear()
        for child in self._rows.winfo_children():
            child.destroy()
        self.retired_box.pack_forget()

    def _show(self, found: dict[str, Any]) -> None:
        self._clear()
        self.total_table.fill([(key, theme.money(value))
                               for key, value in found["total"].items()])

        longterm = {index: block for index, block in enumerate(found.get("longterm", []))}
        for index, block in enumerate(found.get("rows", [])):
            self._block(index, block, longterm.get(index))

        retired = found.get("retired", [])
        if retired:
            self.retired_box.pack(fill="x", pady=(0, 12))
            self.retired_table.fill([
                (row["사번"], row["성명"], row["퇴직일자"], row["퇴직사유"],
                 theme.money(row["퇴직급여 지급총액"]),
                 theme.money(row["사외적립 지급액"]),
                 theme.money(row["장기급여 지급액"]))
                for row in retired])
        else:
            self.retired_box.pack_forget()

    def _block(self, index: int, block: dict[str, Any], longterm: dict | None) -> None:
        title = f"재직 {index + 1}"
        구간 = block["profile"].get("지급구간")
        if 구간:
            title += f" — 지급구간 {구간}"
        box = ttk.Labelframe(self._rows, text=f" {title} ", padding=(12, 8, 12, 10))
        box.pack(fill="x", pady=(0, 12))

        if block["excluded"]:
            ttk.Label(box, text=f"산출 제외 — {block['excluded']}",
                      style="Bad.TLabel").pack(anchor="w", pady=(0, 6))

        line = ttk.Frame(box)
        line.pack(fill="x")
        left = Table(line, ["인적사항", "값"], widths=[150, 170],
                     aligns=["w", "w"], height=13)
        left.pack(side="left", fill="x", expand=True)
        left.fill([(key, _plain(value)) for key, value in block["profile"].items()])

        right = Table(line, ["적용 규정", "값"], widths=[170, 170],
                      aligns=["w", "w"], height=13)
        right.pack(side="left", fill="x", expand=True, padx=(10, 0))
        rows = [(key, _plain(value)) for key, value in block["applied"].items()]
        rows += [(key, theme.money(value)) for key, value in block["result"].items()]
        right.fill(rows, tags=lambda i, _r, start=len(block["applied"]):
                   "total" if i >= start else "")

        if longterm is not None:
            ttk.Label(box, text="장기종업원급여", style="Head.TLabel").pack(
                anchor="w", pady=(10, 2))
            lt = Table(box, ["구분", "값"], widths=[220, 190], aligns=["w", "e"], height=6)
            lt.pack(fill="x")
            lt_rows = [(key, _plain(longterm[key])) for key in
                       ("지급유형", "1일 통상임금", "다음 지급 근속", "남은 지급 시점 수")
                       if key in longterm]
            lt_rows += [(key, theme.money(value)) for key, value in longterm["result"].items()]
            lt.fill(lt_rows)

        trace = block.get("trace", [])
        if trace:
            ttk.Label(box, text="연차별 계산 근거", style="Head.TLabel").pack(
                anchor="w", pady=(10, 2))
            table = Table(box, list(_TRACE_HEADERS),
                          widths=[50, 46, 46, 56, 100, 76, 76, 120, 120, 66, 120, 110],
                          aligns=["e", "w", "e", "e", "e", "w", "e", "e", "e", "e", "e", "e"],
                          height=min(16, len(trace)))
            table.pack(fill="x")
            table.fill([_trace_row(row) for row in trace])
            ttk.Label(box, text="여기 '채무' 열의 합이 위 확정급여채무입니다.",
                      style="Hint.TLabel").pack(anchor="w", pady=(4, 0))


def _plain(value: Any) -> str:
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, float):
        return f"{value:,.4g}" if abs(value) < 1000 else f"{value:,.0f}"
    if isinstance(value, int):
        return f"{value:,}"
    return "" if value is None else str(value)


def _trace_row(row: dict[str, Any]) -> tuple[str, ...]:
    return (
        f"{row['t']:g}", str(row.get("timing", "")), f"{row['age']:g}",
        f"{row['service']:.2f}", theme.money(row["wage"]), str(row.get("cause", "")),
        f"{row['exit_probability']:.5f}", theme.money(row["benefit"]),
        theme.money(row["attributed"]), f"{row['discount']:.5f}",
        theme.money(row["dbo"]), theme.money(row["service_cost"]),
    )
