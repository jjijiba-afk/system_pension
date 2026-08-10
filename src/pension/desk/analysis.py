"""분석 탭 — 산출 한 회차를 그림과 표로.

숫자는 :mod:`pension.dashboard` 가 준 것을 그대로 쓴다. 화면이 따로 계산하면
결과 엑셀·보고서와 어긋나는데, 어긋난 쪽이 어느 쪽인지는 나중에 알 수 없다.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

from . import theme
from .charts import Bars, Columns, Lines
from .hub import Hub
from .widgets import Metrics, ScrollFrame, Table, labeled, section

__all__ = ["AnalysisTab"]


class AnalysisTab(ttk.Frame):
    """분석 화면."""

    def __init__(self, parent: tk.Misc, hub: Hub) -> None:
        super().__init__(parent)
        self.hub = hub
        self._data: dict[str, Any] | None = None
        self.employee = tk.StringVar()

        shell = ScrollFrame(self)
        shell.pack(fill="both", expand=True)
        self._shell = shell
        body = shell.body

        self._empty = ttk.Label(
            body, style="Hint.TLabel", justify="left",
            text="아직 산출 결과가 없습니다. [산출] 탭에서 실행하거나 "
                 "[산출 내역] 탭에서 저장해 둔 산출을 불러오세요.")
        self._empty.pack(anchor="w")

        self._pages = ttk.Frame(body)
        self.metrics = Metrics(self._pages, columns=4)
        self.metrics.pack(fill="x", pady=(0, 4))

        self._build_groups(self._pages)
        self._build_rollforward(self._pages)
        self._build_assets(self._pages)
        self._build_maturity(self._pages)
        self._build_sensitivity(self._pages)
        self._build_projection(self._pages)
        self._build_curves(self._pages)
        self._build_member(self._pages)

        hub.watch("run", self.refresh)

    # ── 조각 ────────────────────────────────────────────────────

    def _build_groups(self, parent: tk.Misc) -> None:
        box = section(parent, "직군별 구성")
        self.group_chart = Bars(box, height=180)
        self.group_chart.pack(fill="x")
        self.group_table = Table(
            box, ["직군", "인원", "확정급여채무", "당기근무원가"],
            widths=[150, 70, 170, 150], aligns=["w", "e", "e", "e"], height=6)
        self.group_table.pack(fill="x", pady=(8, 0))
        self.excluded = ttk.Label(box, text="", style="Hint.TLabel", justify="left")
        self.excluded.pack(anchor="w", pady=(6, 0))

    def _build_rollforward(self, parent: tk.Misc) -> None:
        self.roll_box = section(parent, "확정급여채무 증감내역")
        self.roll_table = Table(self.roll_box, ["구분", "금액"],
                                widths=[320, 190], aligns=["w", "e"], height=12)
        self.roll_table.pack(fill="x")
        self.steps_table = Table(self.roll_box, ["가정변경", "효과"],
                                 widths=[320, 190], aligns=["w", "e"], height=6)
        self.steps_table.pack(fill="x", pady=(8, 0))
        self.steps_table.pack_forget()

    def _build_assets(self, parent: tk.Misc) -> None:
        self.asset_box = section(parent, "사외적립자산 · 순확정급여부채")
        line = ttk.Frame(self.asset_box)
        line.pack(fill="x")
        self.asset_table = Table(line, ["구분", "금액"], widths=[280, 180],
                                 aligns=["w", "e"], height=8)
        self.asset_table.pack(side="left", fill="x", expand=True)
        self.net_table = Table(line, ["구분", "금액"], widths=[280, 180],
                               aligns=["w", "e"], height=8)
        self.net_table.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self.asset_note = ttk.Label(self.asset_box, text="", style="Hint.TLabel",
                                    justify="left")
        self.asset_note.pack(anchor="w", pady=(6, 0))

    def _build_maturity(self, parent: tk.Misc) -> None:
        box = section(parent, "만기분석 (문단 147(c))")
        self.maturity_chart = Columns(box, height=210)
        self.maturity_chart.pack(fill="x")
        labeled(box, "할인 전 급여 지급액을 만기 구간별로 나눈 것입니다. "
                     "파란 기둥이 전체, 주황 기둥이 그중 사외적립자산에서 나갈 몫입니다.")

    def _build_sensitivity(self, parent: tk.Misc) -> None:
        self.sens_box = section(parent, "민감도분석 (문단 145)")
        self.sens_chart = Bars(self.sens_box, height=170, fmt=lambda v: f"{v:+,.2%}")
        self.sens_chart.pack(fill="x")
        self.sens_table = Table(
            self.sens_box, ["가정 변동", "확정급여채무", "증감", "증감률"],
            widths=[220, 170, 150, 90], aligns=["w", "e", "e", "e"], height=8)
        self.sens_table.pack(fill="x", pady=(8, 0))

    def _build_projection(self, parent: tk.Misc) -> None:
        self.proj_box = section(parent, "차년도 예측")
        line = ttk.Frame(self.proj_box)
        line.pack(fill="x")
        self.proj_expense = Table(line, ["차년도 비용", "금액"], widths=[240, 170],
                                  aligns=["w", "e"], height=6)
        self.proj_expense.pack(side="left", fill="x", expand=True)
        self.proj_dbo = Table(line, ["채무 예측", "금액"], widths=[240, 170],
                              aligns=["w", "e"], height=6)
        self.proj_dbo.pack(side="left", fill="x", expand=True, padx=(10, 0))
        labeled(self.proj_box, "가정이 그대로 실현된다고 본 예측입니다 — "
                               "그래서 보험수리적손익은 0 입니다.")

    def _build_curves(self, parent: tk.Misc) -> None:
        box = section(parent, "적용 기초율 곡선")
        self.withdrawal_chart = Lines(box, height=200, title="중도퇴직률")
        self.withdrawal_chart.pack(fill="x")
        self.promotion_chart = Lines(box, height=200, title="승급률")
        self.promotion_chart.pack(fill="x", pady=(10, 0))
        self.mortality_chart = Lines(box, height=200, title="사망률",
                                     fmt=lambda v: f"{v:.4%}", x_label="연령")
        self.mortality_chart.pack(fill="x", pady=(10, 0))

    def _build_member(self, parent: tk.Misc) -> None:
        box = section(parent, "대표 1인 해부")
        line = ttk.Frame(box)
        line.pack(fill="x")
        ttk.Label(line, text="사번").pack(side="left")
        ttk.Entry(line, textvariable=self.employee, width=16).pack(side="left", padx=(6, 6))
        ttk.Button(line, text="이 사람으로 보기", command=self.refresh).pack(side="left")
        ttk.Label(line, text="비우면 근속이 쌓인 중견 직원 중 채무가 가장 큰 사람을 세웁니다.",
                  style="Hint.TLabel").pack(side="left", padx=(10, 0))

        self.profile = ttk.Label(box, text="", style="Hint.TLabel", justify="left")
        self.profile.pack(anchor="w", pady=(8, 4))
        self.scenario_table = Table(
            box, ["시나리오", "확정급여채무", "당기근무원가", "듀레이션", "추계액"],
            widths=[190, 150, 140, 90, 150], aligns=["w", "e", "e", "e", "e"], height=8)
        self.scenario_table.pack(fill="x")
        labeled(box, "같은 사람을 기준 가정과 충격 가정으로 다시 산출한 것입니다. "
                     "엔진의 같은 함수를 부르므로 전체 산출의 그 사람 몫과 어긋나지 않습니다.")

    # ── 갱신 ────────────────────────────────────────────────────

    def refresh(self) -> None:
        from ..dashboard import build

        run = self.hub.run
        if run is None:
            self._pages.pack_forget()
            self._empty.pack(anchor="w")
            return

        try:
            data = build(run, self.employee.get().strip())
        except Exception as exc:
            self._pages.pack_forget()
            self._empty.configure(text=f"분석 화면을 만들지 못했습니다: {exc}")
            self._empty.pack(anchor="w")
            return

        self._data = data
        self._empty.pack_forget()
        self._pages.pack(fill="both", expand=True)
        self._show(data)
        self._shell.to_top()

    def _show(self, data: dict[str, Any]) -> None:
        totals = data["totals"]
        period = " ~ ".join(part for part in data.get("period", []) if part)
        self.metrics.show([
            ("확정급여채무", theme.money(totals["dbo"])),
            ("당기근무원가", theme.money(totals["sc"])),
            ("이자원가 (차기)", theme.money(totals["ic"])),
            ("퇴직급여추계액", theme.money(totals["accrued"])),
            ("산출대상 인원", f"{totals['headcount']:,}명"),
            ("적용 할인율", theme.pct(data["single_rate"])),
            ("듀레이션", theme.num(totals["duration"]) + " 년"),
            ("산출기준일", data["base_date"] + (f"\n{period}" if period else "")),
        ])

        groups = data.get("groups", [])
        self.group_chart.show([(g["name"], g["dbo"]) for g in groups])
        self.group_table.fill([
            (g["name"], f"{g['n']:,}", theme.money(g["dbo"]), theme.money(g["sc"]))
            for g in groups])
        excluded = data.get("excluded") or {}
        self.excluded.configure(
            text=("산출 제외 — " + " · ".join(f"{k} {v:,}명" for k, v in excluded.items()))
            if excluded else "산출에서 뺀 사람은 없습니다.")

        self._fill_rollforward(data)
        self._fill_assets(data)

        maturity = data.get("maturity", [])
        self.maturity_chart.show(
            [row[0] for row in maturity],
            {"전체": [row[1] for row in maturity],
             "사외적립자산": [row[2] for row in maturity]})

        self._fill_sensitivity(data)
        self._fill_projection(data)

        curves = data.get("curves", {})
        axis = data.get("curve_axis", {})
        self.withdrawal_chart.show(_flatten(curves.get("중도퇴직률", {})),
                                   x_label=axis.get("중도퇴직률", ""))
        self.promotion_chart.show(_flatten(curves.get("승급률", {})),
                                  x_label=axis.get("승급률", ""))
        self.mortality_chart.show(curves.get("사망률", {}))

        self._fill_member(data)

    def _fill_rollforward(self, data: dict[str, Any]) -> None:
        rows = data.get("rollforward", [])
        if rows:
            self.roll_box.pack(fill="x", pady=(0, 12))
            self.roll_table.fill([(k, theme.money(v)) for k, v in rows])
        else:
            self.roll_box.pack_forget()
        steps = data.get("assumption_steps", [])
        if steps:
            self.steps_table.pack(fill="x", pady=(8, 0))
            self.steps_table.fill([(k, theme.money(v)) for k, v in steps])
        else:
            self.steps_table.pack_forget()

    def _fill_assets(self, data: dict[str, Any]) -> None:
        assets = data.get("assets", [])
        if not assets:
            self.asset_box.pack_forget()
            return
        self.asset_box.pack(fill="x", pady=(0, 12))
        self.asset_table.fill([(k, theme.money(v)) for k, v in assets])
        self.net_table.fill([(k, theme.money(v)) for k, v in data.get("net", [])])
        note = f"적립비율 {data.get('funded', 0.0):.1%}"
        ceiling = data.get("ceiling")
        if ceiling:
            note += (f"   ·   자산인식상한 {theme.money(ceiling['limit'])} 원 — "
                     f"초과적립 {theme.money(ceiling['surplus'])}, "
                     f"인식제한 효과 {theme.money(ceiling['effect'])}")
        self.asset_note.configure(text=note)

    def _fill_sensitivity(self, data: dict[str, Any]) -> None:
        cases = data.get("sensitivity", [])
        if not cases:
            self.sens_box.pack_forget()
            return
        self.sens_box.pack(fill="x", pady=(0, 12))
        self.sens_chart.show([(row[0], row[3]) for row in cases])
        self.sens_table.fill([
            (row[0], theme.money(row[1]), theme.signed(row[2]), f"{row[3]:+.2%}")
            for row in cases])

    def _fill_projection(self, data: dict[str, Any]) -> None:
        projection = data.get("projection")
        if not projection:
            self.proj_box.pack_forget()
            return
        self.proj_box.pack(fill="x", pady=(0, 12))
        self.proj_expense.fill([(k, theme.money(v)) for k, v in projection["expense"]])
        self.proj_dbo.fill([(k, theme.money(v)) for k, v in projection["dbo"]])

    def _fill_member(self, data: dict[str, Any]) -> None:
        profile = data.get("profile", {})
        parts = [f"{key} {value}" for key, value in profile.items()]
        self.profile.configure(text="   ·   ".join(parts))
        self.scenario_table.fill([
            (case["label"], theme.money(case["dbo"]), theme.money(case["service_cost"]),
             theme.num(case["duration"]), theme.money(case["accrued"]))
            for case in data.get("scenarios", [])])


def _flatten(curves: dict[str, dict]) -> dict[str, dict]:
    """규정별 곡선 묶음을 그대로 쓴다. 이름이 없으면 빈 묶음."""
    return {name: points for name, points in curves.items() if points}
