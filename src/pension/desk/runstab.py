"""산출 내역 탭 — 이 컴퓨터에 남는 산출.

산출을 마치면 이름을 붙여 이 컴퓨터에 남긴다. 다음 결산에 [산출] 탭에서 전기로
끌어오면 확정급여채무·할인율·근무원가가 그대로 들어오고, 전기 기초율까지
연결되어 증감분석이 경험조정과 가정변경효과를 나눠 계산한다.

단체를 먼저 고르는 이유는 하나다 — 전기 산출을 남의 회사 것으로 집으면
증감분석이 통째로 틀리는데, 목록에 뜬 이름만 보고는 알아채기 어렵다.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .files import open_with_default, reveal
from .hub import Hub, Loaded
from .widgets import Table, labeled, section

__all__ = ["RunsTab"]


class RunsTab(ttk.Frame):
    """산출 내역 화면."""

    def __init__(self, parent: tk.Misc, hub: Hub) -> None:
        super().__init__(parent)
        self.hub = hub
        self.run_name = tk.StringVar()

        body = ttk.Frame(self, padding=(16, 12, 16, 12))
        body.pack(fill="both", expand=True)

        keep = section(body, "이 산출을 이 컴퓨터에 저장")
        line = ttk.Frame(keep)
        line.pack(fill="x")
        ttk.Label(line, text="산출명").pack(side="left")
        ttk.Entry(line, textvariable=self.run_name, width=28).pack(side="left", padx=(8, 6))
        ttk.Button(line, text="저장", command=self.save).pack(side="left")
        self.save_status = ttk.Label(line, text="", style="Hint.TLabel")
        self.save_status.pack(side="left", padx=(12, 0))
        labeled(keep, "명부·기초율·결과 파일이 통째로 남습니다. 화면 맨 위에서 고른 단체 "
                      "아래에 들어가며, 같은 이름으로 저장하면 덮어씁니다. 예: 2412 1번단체")

        listing = section(body, "저장된 산출", pady=(0, 8))
        self.table = Table(
            listing, ["산출명", "저장 시각", "기준일", "인원", "확정급여채무", "명부"],
            widths=[180, 130, 100, 70, 180, 160],
            aligns=["w", "w", "w", "e", "e", "w"], height=12)
        self.table.pack(fill="both", expand=True)

        bar = ttk.Frame(listing)
        bar.pack(fill="x", pady=(8, 0))
        for text_, command in (
            ("이 산출 불러오기", self.load),
            ("결과 엑셀 열기", self.open_result),
            ("폴더 열기", self.open_folder),
            ("삭제", self.delete),
            ("새로 고침", self.refresh),
        ):
            ttk.Button(bar, text=text_, command=command).pack(side="left", padx=(0, 6))
        self.status = ttk.Label(listing, text="", style="Hint.TLabel", justify="left")
        self.status.pack(anchor="w", pady=(6, 0))
        labeled(listing,
                "[불러오기] 는 저장해 둔 명부·기초율로 **다시 산출** 해서 분석·보고서·"
                "사번 조회를 그때 그대로 되살립니다. 결과 엑셀만 볼 것이면 [결과 엑셀 열기] "
                "가 빠릅니다.")

        hub.watch("client", self.refresh)
        hub.watch("run", self._suggest_name)
        self.refresh()

    # ── 목록 ────────────────────────────────────────────────────

    def refresh(self) -> None:
        from .. import runs

        found = runs.summaries()
        self.table.fill(
            [(run["name"], run["saved"], run["base_date"], run["headcount"],
              run["dbo"], run["roster_name"]) for run in found],
            keys=lambda _i, row: str(row[0]))
        self._say(f"단체 '{self.hub.client()}' 에 저장된 산출 {len(found)}건.")

    def _say(self, message: str, bad: bool = False) -> None:
        self.status.configure(text=message, style="Bad.TLabel" if bad else "Hint.TLabel")

    def _suggest_name(self) -> None:
        run = self.hub.run
        if run is None or self.run_name.get():
            return
        base = getattr(getattr(run, "config", None), "base_date", None)
        if base is not None:
            self.run_name.set(f"{base:%y%m}")

    def _picked(self) -> str:
        name = self.table.selected()
        if not name:
            self._say("목록에서 산출을 하나 고르세요.", bad=True)
        return name

    # ── 저장 ────────────────────────────────────────────────────

    def save(self) -> None:
        from .. import runs

        loaded = self.hub.loaded
        options = getattr(loaded, "options", None) if loaded else None
        if options is None:
            messagebox.showinfo("산출 내역",
                                "먼저 [산출] 탭에서 산출을 실행하세요.\n"
                                "불러온 산출은 이미 저장돼 있으므로 다시 저장할 것이 없습니다.",
                                parent=self)
            return
        name = self.run_name.get().strip()
        if not name:
            self.save_status.configure(text="산출명을 입력하세요.", style="Bad.TLabel")
            return

        results = {}
        output = Path(options.output_path)
        if output.exists():
            results["산출결과.xlsx"] = output
        members = output.with_name(f"{output.stem}_개인별{output.suffix}")
        if members.exists():
            results["개인별결과.xlsx"] = members

        try:
            runs.save(name, options.roster_path, options.assumptions_path,
                      results=results, report=_report_of(loaded.run),
                      options=_options_of(options),
                      roster_name=Path(options.roster_path).name)
        except (OSError, ValueError) as exc:
            self.save_status.configure(text=f"저장하지 못했습니다: {exc}", style="Bad.TLabel")
            return

        self.save_status.configure(
            text=f"'{name}' 을(를) 단체 '{self.hub.client()}' 에 저장했습니다.",
            style="Good.TLabel")
        self.refresh()
        self.hub.announce("runs")

    # ── 목록 단추 ───────────────────────────────────────────────

    def load(self) -> None:
        """저장된 산출을 그때 쓴 명부·기초율로 다시 산출한다."""
        from .. import runs
        from ..pipeline import RunOptions, run_valuation

        name = self._picked()
        if not name:
            return
        try:
            restored = runs.restore(name)
        except (OSError, ValueError) as exc:
            self._say(str(exc), bad=True)
            return

        meta = restored["meta"]
        base = (meta.get("options") or {}).get("base_date") or ""
        from .calc import parse_date

        self._say(f"'{name}' 을(를) 다시 산출하는 중… (잠시 걸립니다)")
        self.update_idletasks()
        options = RunOptions(
            roster_path=Path(restored["roster"]),
            assumptions_path=Path(restored["assumptions"]),
            output_path=Path(restored["folder"]) / "산출결과.xlsx",
            base_date=parse_date(base),
        )
        try:
            run = run_valuation(options)
        except Exception as exc:
            self._say(f"다시 산출하지 못했습니다: {exc}", bad=True)
            return

        self.hub.publish(Loaded(run=run, options=options,
                                output=options.output_path, name=name))
        self._say(f"'{name}' 을(를) 불러왔습니다. "
                  "[분석] · [계리평가 보고서] · [사번 조회] 탭이 이 산출을 봅니다.")

    def open_result(self) -> None:
        from .. import runs

        name = self._picked()
        if not name:
            return
        try:
            restored = runs.restore(name)
        except (OSError, ValueError) as exc:
            self._say(str(exc), bad=True)
            return
        target = restored["results"].get("산출결과.xlsx")
        if target is None:
            self._say("이 산출에는 저장된 결과 파일이 없습니다. [불러오기] 로 다시 산출하세요.",
                      bad=True)
            return
        open_with_default(target)
        self._say(f"{target}")

    def open_folder(self) -> None:
        from .. import runs

        name = self._picked()
        if not name:
            return
        try:
            reveal(runs.folder_of(name))
        except ValueError as exc:
            self._say(str(exc), bad=True)

    def delete(self) -> None:
        from .. import runs

        name = self._picked()
        if not name:
            return
        if not messagebox.askyesno(
                "삭제", f"저장된 산출 '{name}' 을(를) 지웁니다.\n"
                        "명부·기초율·결과 파일이 함께 사라집니다. 계속할까요?",
                parent=self):
            return
        try:
            runs.delete(name)
        except (OSError, ValueError) as exc:
            self._say(str(exc), bad=True)
            return
        self.refresh()
        self.hub.announce("runs")


def _report_of(run) -> dict:
    """저장본에 남길 요약. 목록과 전기 연결이 이걸 읽는다."""
    valuation = run.valuation
    rate = (run.assumptions.discount.level_rate
            if run.assumptions.discount.flat is not None
            else valuation.single_discount_rate())
    return {
        "summary": [
            ["산출기준일", str(run.config.base_date)],
            ["적용 할인율", f"{rate:.3%}"],
            ["산출대상 인원", f"{valuation.headcount:,}"],
            ["확정급여채무 (DBO)", f"{valuation.dbo:,.0f} 원"],
            ["당기근무원가", f"{valuation.service_cost:,.0f} 원"],
        ],
        "values": {
            "base_date": str(run.config.base_date),
            "dbo": valuation.dbo,
            "service_cost": valuation.service_cost,
            "discount_rate": rate,
            "longterm_dbo": run.longterm.dbo if run.longterm else 0.0,
        },
    }


def _options_of(options) -> dict:
    return {
        "base_date": str(options.base_date) if options.base_date else "",
        "period_start": str(options.period_start) if options.period_start else "",
        "include_sensitivity": options.include_sensitivity,
        "include_longterm": options.include_longterm,
    }
