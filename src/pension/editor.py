"""산출 가정 입력 화면.

기초율 워크북을 엑셀에서 직접 손대는 대신 이 화면에서 채울 수 있게 한다. 시트
구조를 외우지 않아도 되고, 지급률 규정은 입력하는 즉시 검증·미리보기가 된다.

탭 구성은 기초율 워크북의 시트와 1:1 로 맞췄다. 저장하면 같은 서식의 ``.xlsx``
가 나오므로, 엑셀에서 열어 수정한 뒤 다시 불러와도 된다.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from .actuarial import (
    FRACTION_KEEP,
    FRACTION_MODES,
    SERVICE_BASES,
    SERVICE_DAILY,
)
from .assumption_form import (
    APPLY_CHOICES as _APPLY_CHOICES,
)
from .assumption_form import (
    CAUSE_COLUMN_SEP,
    EXIT_CAUSE_HEADERS,
    FORM_SHEETS,
    cause_split_rules,
    merge_benefit_causes,
    split_benefit_by_cause,
)
from .assumption_form import (
    ROUNDING_UNITS as _ROUNDING_UNITS,
)
from .assumption_form import (
    UNIT_LABELS as _UNIT_LABELS,
)
from .assumption_form import read_state as _read_state
from .assumption_form import state_to_sheets as _state_to_sheets
from .assumptions import (
    ATTRIBUTIONS,
    BENEFIT_MODES,
    BENEFIT_SHEET,
    DISCOUNT_SHEET,
    EXIT_CAUSES,
    FORMULA,
    LONGTERM_SHEET,
    LONGTERM_TIMINGS,
    LONGTERM_TYPES,
    LT_AT_MILESTONE,
    LT_IN_KIND,
    LT_VACATION,
    MORTALITY_SHEET,
    PROMOTION_SHEET,
    SALARY_SHEET,
    STATUTORY_MODE,
    WITHDRAWAL_SHEET,
)
from . import hidpi
from .formula import FUNCTIONS, VARIABLES, Formula, FormulaError
from .jobgroup import DEFAULT_GROUPS
from .normalize import text

__all__ = ["AssumptionsEditor", "open_editor"]

_PAD = 8
_DEFAULT_ROWS = 12


@dataclass(slots=True)
class SheetSpec:
    """탭 하나가 다루는 시트의 서식."""

    sheet: str
    """워크북 시트 이름."""
    tab: str
    """탭에 표시할 이름."""
    key_header: str
    """첫 열 머리글(연차/연령/근속연수)."""
    fixed_headers: tuple[str, ...] = ()
    """직군과 무관하게 고정된 열(할인율·사망률 등). 비면 직군별 열을 쓴다."""
    note: str = ""
    key_choices: tuple[str, ...] = ()
    """첫 열 머리글을 바꿀 수 있는 경우의 선택지(연령/근속)."""
    allow_extra: bool = False
    """직군 말고 이름 붙인 열을 만들 수 있는 표인지(지급률·장기급여)."""
    extra_hint: str = ""
    """그 열을 언제 만드는지. [항목 추가] 를 누를 때 보여 준다."""

    @property
    def per_job_group(self) -> bool:
        return not self.fixed_headers


# 탭 서식은 웹앱과 공유한다 — 두 화면의 시트 구성이 어긋나면 안 된다.
SPECS: tuple[SheetSpec, ...] = tuple(
    SheetSpec(
        sheet=item["sheet"], tab=item["tab"], key_header=item["key"],
        fixed_headers=tuple(item["fixed"]), note=item["note"],
        key_choices=tuple(item["key_choices"]),
        allow_extra=bool(item.get("allow_extra")),
        extra_hint=item.get("extra_hint", ""),
    )
    for item in FORM_SHEETS
)


class _Grid(ttk.Frame):
    """스크롤되는 표 입력 위젯.

    엑셀처럼 셀을 직접 치되, 열 구성은 :class:`SheetSpec` 과 직군 목록이 정한다.
    """

    def __init__(self, parent, spec: SheetSpec, job_groups: list[str]) -> None:
        super().__init__(parent, padding=_PAD)
        self.spec = spec
        self.job_groups = job_groups
        self.extra: list[str] = []
        """직군 말고 따로 이름 붙인 열('정규직·정년', '금').

        지급률·장기급여 표는 직군 아닌 열을 가질 수 있다. 이 창이 그 열을
        들고 있지 못하면, 그렇게 만든 파일을 여기서 열어 저장하는 것만으로
        열과 값이 통째로 사라진다.
        """
        self._entries: list[list[ttk.Entry]] = []
        self.key_var = tk.StringVar(value=spec.key_header)
        self.on_columns_changed: Callable[[], None] | None = None
        """열이 늘거나 줄면 부르는 것. 지급률은 방식·수식 줄이 따라와야 한다."""

        self._build_header()
        self._build_body()
        self.set_rows([])

    # ── 화면 구성 ────────────────────────────────────────────────
    def _build_header(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x")

        if self.spec.key_choices:
            ttk.Label(bar, text="조회 기준").pack(side="left")
            box = ttk.Combobox(
                bar, textvariable=self.key_var, values=list(self.spec.key_choices),
                state="readonly", width=8,
            )
            box.pack(side="left", padx=(4, 12))
            box.bind("<<ComboboxSelected>>", lambda _e: self._relabel_key())

        ttk.Button(bar, text="행 추가", command=self.add_row).pack(side="left")
        ttk.Button(bar, text="빈 행 정리", command=self.compact).pack(side="left", padx=4)
        if self.spec.allow_extra:
            ttk.Button(bar, text="항목 추가", command=self.add_column).pack(side="left")
            ttk.Button(bar, text="항목 삭제", command=self.remove_column).pack(
                side="left", padx=4)

        if self.spec.note:
            ttk.Label(
                self, text=f"· {self.spec.note}", style="Hint.TLabel", wraplength=hidpi.px(self, 760),
                justify="left",
            ).pack(fill="x", pady=(6, 4))

    def _build_body(self) -> None:
        wrapper = ttk.Frame(self)
        wrapper.pack(fill="both", expand=True)

        self._canvas = tk.Canvas(wrapper, highlightthickness=0, height=hidpi.px(self, 260))
        scroll = ttk.Scrollbar(wrapper, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scroll.set)

        self._canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self._table = ttk.Frame(self._canvas)
        self._window = self._canvas.create_window((0, 0), window=self._table, anchor="nw")
        self._table.bind(
            "<Configure>",
            lambda _e: self._canvas.configure(scrollregion=self._canvas.bbox("all")),
        )
        self._canvas.bind(
            "<Configure>", lambda e: self._canvas.itemconfigure(self._window, width=e.width)
        )

    # ── 열 ───────────────────────────────────────────────────────
    @property
    def columns(self) -> list[str]:
        """첫 열을 뺀 열 이름들. 웹앱의 ``grid_columns()`` 와 순서가 같아야 한다."""
        if not self.spec.per_job_group:
            return list(self.spec.fixed_headers)
        extra = [text(n) for n in self.extra]
        extra = [n for n in extra if n and n not in self.job_groups]
        return [*self.job_groups, *dict.fromkeys(extra)]

    @property
    def headers(self) -> list[str]:
        return [self.key_var.get(), *self.columns]

    def _relabel_key(self) -> None:
        self._header_labels[0].configure(text=self.key_var.get())

    def rebuild_columns(
        self, job_groups: list[str], extra: list[str] | None = None
    ) -> None:
        """열 구성이 바뀌면 값을 지키면서 다시 만든다.

        값은 **열 이름을 키로** 옮긴다. 자리로 옮기면 열이 하나만 늘어도 그
        뒤의 배수가 통째로 한 칸씩 밀린다.
        """
        if not self.spec.per_job_group:
            return
        previous = self.get_rows()
        old = list(self.columns)
        self.job_groups = list(job_groups)
        if extra is not None:
            self.extra = [text(n) for n in extra]

        remapped: list[list[str]] = []
        for row in previous:
            by_name = dict(zip(old, row[1:], strict=False))
            remapped.append([row[0], *[by_name.get(c, "") for c in self.columns]])
        self.set_rows(remapped)

    def load(self, rows: list[list[str]], extra: list[str] | None = None) -> None:
        """파일에서 읽은 값을 그대로 앉힌다. 열 구성도 파일 것을 따른다."""
        if self.spec.per_job_group and extra is not None:
            self.extra = [text(n) for n in extra]
        self.set_rows(rows)

    def add_column(self) -> None:
        """직군이 아닌 이름의 열을 하나 만든다."""
        name = text(simpledialog.askstring(
            "항목 추가", self.spec.extra_hint + "\n\n항목 이름", parent=self))
        if not name:
            return
        if name in self.columns:
            messagebox.showwarning("항목 추가", "이미 있는 이름입니다.", parent=self)
            return
        self.rebuild_columns(self.job_groups, [*self.extra, name])
        self._notify()

    def remove_column(self) -> None:
        """따로 만든 열을 지운다. 직군 열은 [직군] 줄에서 다룬다."""
        if not self.extra:
            messagebox.showinfo("항목 삭제", "따로 만든 항목이 없습니다.", parent=self)
            return
        name = text(simpledialog.askstring(
            "항목 삭제",
            "지울 항목 이름\n\n" + " · ".join(self.extra) +
            "\n\n그 열에 적은 값도 함께 사라집니다.",
            parent=self))
        if not name:
            return
        if name not in self.extra:
            messagebox.showwarning("항목 삭제", f"'{name}' 은 없는 항목입니다.", parent=self)
            return
        self.rebuild_columns(self.job_groups, [n for n in self.extra if n != name])
        self._notify()

    def _notify(self) -> None:
        """열이 바뀌었음을 창에 알린다. 방식·수식 줄이 따라와야 한다."""
        if self.on_columns_changed is not None:
            self.on_columns_changed()

    # ── 행 ───────────────────────────────────────────────────────
    def set_rows(self, rows: list[list[str]]) -> None:
        for child in self._table.winfo_children():
            child.destroy()
        self._entries.clear()
        self._header_labels: list[ttk.Label] = []

        for col, title in enumerate(self.headers):
            label = ttk.Label(self._table, text=title, style="Col.TLabel")
            label.grid(row=0, column=col, sticky="ew", padx=1, pady=(0, 4))
            self._table.columnconfigure(col, weight=1, minsize=90)
            self._header_labels.append(label)

        for values in rows:
            self._append(values)
        while len(self._entries) < _DEFAULT_ROWS:
            self._append([])

    def _append(self, values: list[str]) -> None:
        index = len(self._entries)
        widgets: list[ttk.Entry] = []
        for col in range(len(self.headers)):
            entry = ttk.Entry(self._table, width=12, justify="right")
            entry.grid(row=index + 1, column=col, sticky="ew", padx=1, pady=1)
            if col < len(values):
                entry.insert(0, values[col])
            widgets.append(entry)
        self._entries.append(widgets)

    def add_row(self) -> None:
        self._append([])
        self._canvas.yview_moveto(1.0)

    def compact(self) -> None:
        """값이 있는 행만 남기고 빈 줄을 정리한다."""
        self.set_rows(self.get_rows())

    def get_rows(self) -> list[list[str]]:
        """값이 하나라도 있는 행만."""
        rows = []
        for widgets in self._entries:
            values = [w.get().strip() for w in widgets]
            if any(values):
                rows.append(values)
        return rows


class _BenefitRuleTab(ttk.Frame):
    """지급률 규정 방식과 수식을 입력하는 탭.

    수식은 저장 전에 확인할 수 있어야 한다. 잘못된 수식이 산출 단계에서야
    드러나면 담당자는 어디를 고쳐야 할지 알기 어렵다.
    """

    def __init__(
        self, parent, job_groups: list[str],
        on_split: Callable[[str, bool], None] | None = None,
    ) -> None:
        super().__init__(parent, padding=_PAD)
        self.job_groups = list(job_groups)
        self.columns = list(job_groups)
        self.split: list[str] = []
        self._on_split = on_split
        self._rows: dict[str, dict[str, Any]] = {}
        self._passthrough: dict[str, dict[str, Any]] = {}

        self._build_help()
        self._body = ttk.Frame(self)
        self._body.pack(fill="both", expand=True, pady=(6, 0))
        self.rebuild(self.job_groups)

    def _build_help(self) -> None:
        box = ttk.LabelFrame(self, text="작성 방법", padding=_PAD)
        box.pack(fill="x")

        guide = (
            "누적  표의 값이 그 근속연수의 누적 배수입니다. (10년 → 10.0)\n"
            "누진  표의 값이 그 구간에서만 적용할 연 배수입니다. "
            "0년 1.0 / 5년 1.5 / 10년 2.0 이면 근속 12년 = 5×1.0 + 5×1.5 + 2×2.0 = 16.5\n"
            "수식  아래 수식으로 직접 계산합니다. 표로 담기 어려운 규정에만 쓰세요."
        )
        ttk.Label(box, text=guide, justify="left", style="Hint.TLabel").pack(anchor="w")

        ttk.Separator(box, orient="horizontal").pack(fill="x", pady=6)
        ttk.Label(
            box,
            text="변수  " + " · ".join(f"{k}={v}" for k, v in VARIABLES.items()),
            justify="left", style="Hint.TLabel", wraplength=hidpi.px(self, 780),
        ).pack(anchor="w")
        ttk.Label(
            box,
            text="함수  " + " ".join(sorted(FUNCTIONS)),
            justify="left", style="Hint.TLabel", wraplength=hidpi.px(self, 780),
        ).pack(anchor="w")
        ttk.Label(
            box,
            text='예시  =IF(t<10, t*1.0, 10 + (t-10)*2.0)     '
                 '=IF(제도="DB", t*1.5, t)     =MIN(t, 30)',
            justify="left", style="Hint.TLabel",
        ).pack(anchor="w", pady=(2, 0))

    def rebuild(
        self, job_groups: list[str], columns: list[str] | None = None,
        split: list[str] | None = None,
    ) -> None:
        """열 구성이 바뀌면 행을 다시 만든다. 기존 입력은 이름으로 이어받는다.

        :param columns: 지급률 표의 **모든** 열. 직군 아닌 열('정규직·정년',
            '사망가산')도 방식·수식을 따로 가지므로 여기 줄이 있어야 한다.
        :param split: 이미 정년·중도·사망으로 갈라 놓은 직군.
        """
        previous = self.get_values()
        for child in self._body.winfo_children():
            child.destroy()
        self._rows.clear()
        self.job_groups = list(job_groups)
        self.columns = list(columns) if columns is not None else list(job_groups)
        if split is not None:
            self.split = list(split)

        headers = ("규정명(직군)", "방식", "수식", "", "사유별 차등")
        for col, title in enumerate(headers):
            ttk.Label(self._body, text=title, style="Col.TLabel").grid(
                row=0, column=col, sticky="w", padx=2, pady=(0, 4)
            )
        self._body.columnconfigure(2, weight=1)

        for index, group in enumerate(self.columns, start=1):
            saved = previous.get(group, {})
            mode_var = tk.StringVar(value=saved.get("mode", STATUTORY_MODE))
            formula_var = tk.StringVar(value=saved.get("formula", ""))

            ttk.Label(self._body, text=group).grid(row=index, column=0, sticky="w", padx=2)

            combo = ttk.Combobox(
                self._body, textvariable=mode_var, values=list(BENEFIT_MODES),
                state="readonly", width=6,
            )
            combo.grid(row=index, column=1, padx=2, pady=1)

            entry = ttk.Entry(self._body, textvariable=formula_var)
            entry.grid(row=index, column=2, sticky="ew", padx=2, pady=1)

            status = ttk.Label(self._body, text="", width=16)
            status.grid(row=index, column=3, sticky="w", padx=2)

            self._rows[group] = {
                "mode": mode_var, "formula": formula_var,
                "entry": entry, "status": status,
            }
            self._build_split_cell(index, group)

            combo.bind("<<ComboboxSelected>>", lambda _e, g=group: self._sync_row(g))
            formula_var.trace_add("write", lambda *_a, g=group: self._validate_row(g))
            self._sync_row(group)

        bar = ttk.Frame(self._body)
        bar.grid(row=len(self.columns) + 1, column=0, columnspan=5, sticky="w", pady=(8, 0))
        ttk.Button(bar, text="수식 미리보기", command=self.preview).pack(side="left")
        ttk.Label(
            bar,
            text="  사유별 차등을 켜면 그 직군이 " + " · ".join(EXIT_CAUSES) +
                 " 세 열로 갈립니다. 비운 열은 원래 규정을 그대로 씁니다.",
            style="Hint.TLabel",
        ).pack(side="left")

    def _build_split_cell(self, index: int, group: str) -> None:
        """그 줄의 '사유별 차등' 칸.

        갈라서 생긴 열 자신에게는 다시 물을 것이 없으므로 꼬리표만 남긴다.
        """
        if group not in self.job_groups:
            label = "↑ 갈라 놓음" if CAUSE_COLUMN_SEP in group else ""
            ttk.Label(self._body, text=label, style="Hint.TLabel").grid(
                row=index, column=4, sticky="w", padx=2
            )
            return

        var = tk.BooleanVar(value=group in self.split)
        box = ttk.Checkbutton(
            self._body, variable=var,
            command=lambda g=group, v=var: self._toggle_split(g, v),
        )
        box.grid(row=index, column=4, sticky="w", padx=2)
        self._rows[group]["split"] = var

    def _toggle_split(self, group: str, var: tk.BooleanVar) -> None:
        if self._on_split is None:
            var.set(group in self.split)
            return
        self._on_split(group, var.get())

    def _sync_row(self, group: str) -> None:
        """방식에 맞춰 수식 칸을 열고 닫는다."""
        row = self._rows[group]
        is_formula = row["mode"].get() == FORMULA
        row["entry"].configure(state="normal" if is_formula else "disabled")
        if not is_formula:
            row["status"].configure(text="")
        else:
            self._validate_row(group)

    def _validate_row(self, group: str) -> None:
        row = self._rows[group]
        if row["mode"].get() != FORMULA:
            return
        source = row["formula"].get().strip()
        if not source:
            row["status"].configure(text="수식 필요", foreground="#B45309")
            return
        try:
            Formula(source)
        except FormulaError as exc:
            row["status"].configure(text="✕ " + _short(exc), foreground="#B91C1C")
        else:
            row["status"].configure(text="✓ 확인됨", foreground="#15803D")

    def preview(self) -> None:
        """근속 1~30년 배수를 표로 보여 준다. 규정이 의도대로 도는지 눈으로 본다."""
        formulas = {
            group: row["formula"].get().strip()
            for group, row in self._rows.items()
            if row["mode"].get() == FORMULA and row["formula"].get().strip()
        }
        if not formulas:
            messagebox.showinfo("미리보기", "수식 방식으로 지정된 규정이 없습니다.", parent=self)
            return

        window = tk.Toplevel(self)
        window.title("지급률 수식 미리보기")
        window.geometry(hidpi.scale_geometry(self, "560x420"))

        box = ttk.Frame(window, padding=_PAD)
        box.pack(fill="both", expand=True)
        ttk.Label(
            box, text="연령 40세 · 정년 60세 · 제도 DB 기준으로 계산한 근속연수별 배수입니다.",
            style="Hint.TLabel", wraplength=hidpi.px(self, 520),
        ).pack(anchor="w", pady=(0, 6))

        columns = ("근속", *formulas)
        tree = ttk.Treeview(box, columns=columns, show="headings", height=15)
        for column in columns:
            tree.heading(column, text=column)
            tree.column(column, width=90, anchor="e")
        tree.pack(fill="both", expand=True)

        compiled = {}
        for group, source in formulas.items():
            try:
                compiled[group] = Formula(source)
            except FormulaError as exc:
                compiled[group] = exc

        for service in range(1, 31):
            values = [str(service)]
            for group in formulas:
                item = compiled[group]
                if isinstance(item, FormulaError):
                    values.append("오류")
                    continue
                try:
                    result = item.evaluate(t=service, x=40, N=60, 제도="DB", 직군=group)
                    values.append(f"{result:,.3f}")
                except FormulaError:
                    values.append("오류")
            tree.insert("", "end", values=values)

    def get_values(self) -> dict[str, dict[str, str]]:
        # 화면에 없는 열(직군이 아닌 지급률)의 값도 함께 돌려준다. 빠뜨리면
        # 이 창을 한 번 열었다 닫는 것만으로 그 규정이 사라진다.
        values: dict[str, dict[str, str]] = dict(self._passthrough)
        values.update({
            group: {"mode": row["mode"].get(), "formula": row["formula"].get().strip()}
            for group, row in self._rows.items()
        })
        return values

    def set_values(self, values: dict[str, dict[str, str]]) -> None:
        self._passthrough = {
            name: dict(item) for name, item in values.items() if name not in self._rows
        }
        for group, item in values.items():
            row = self._rows.get(group)
            if row is None:
                continue
            row["mode"].set(item.get("mode") or STATUTORY_MODE)
            row["formula"].set(item.get("formula", ""))
            self._sync_row(group)

    def problems(self) -> list[str]:
        """저장 전 확인. 수식 방식인데 수식이 비었거나 틀린 규정을 모은다."""
        found = []
        for group, row in self._rows.items():
            if row["mode"].get() != FORMULA:
                continue
            source = row["formula"].get().strip()
            if not source:
                found.append(f"'{group}' 은 수식 방식인데 수식이 비어 있습니다")
                continue
            try:
                Formula(source)
            except FormulaError as exc:
                found.append(f"'{group}' 수식: {_short(exc)}")
        return found


def _short(exc: Exception, limit: int = 60) -> str:
    message = str(exc).split(" — 수식:")[0]
    return message if len(message) <= limit else message[: limit - 1] + "…"


class _JobGroupMapTab(ttk.Frame):
    """명부 직급·직군을 산출 직군으로 묶는 탭.

    명부의 `직군` 열에 무엇이 들어올지는 회사가 정한다. 실제 파일에서는
    `정규직/계약직` 같은 고용형태가 오기도 하고, `사원/과장/대표이사` 처럼
    **직급** 이 그대로 오기도 하며, 직군은 비고 `임직원구분` 에만
    `정사원/촉탁사원/임원（주재원）` 이 적혀 오기도 한다.

    산출 가정은 이 낱낱의 직급마다 세우지 않는다. 비슷한 것끼리 묶어 그 단위로
    기초율을 만든다. 기본 묶음은 정규직 / 계약직 / 임원이지만, **묶음 이름
    자체를 여기서 바꿀 수 있다.** 생산직과 관리직의 퇴직률이 확연히 다른
    회사라면 정규직을 다시 갈라야 하고, 그때 칸이 셋뿐이면 쓸 수 없기 때문이다.

    배정은 제안만 하고 확정하지 않는다. 같은 '촉탁사원' 이라도 정년 후 재고용은
    계약직, 임원 예우 재고용은 임원인 회사가 있어 규정을 봐야 갈린다.
    """

    def __init__(self, parent, job_groups: list[str]) -> None:
        super().__init__(parent, padding=_PAD)
        self.job_groups = list(job_groups)
        self._rows: list[dict[str, Any]] = []

        ttk.Label(
            self,
            text="명부의 직급·직군을 산출에 쓸 묶음으로 배정합니다. 묶음 이름은 위 "
                 "'직군별 규정' 칸에서 바꾸세요. 저장하면 '지급규정' 시트에 "
                 "명부직군·임직원구분·변환직군명 세 열로 적힙니다.",
            style="Hint.TLabel", wraplength=hidpi.px(self, 840), justify="left",
        ).pack(anchor="w", pady=(0, 8))

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="명부에서 직군 읽어오기", command=self._load_from_roster).pack(
            side="left"
        )
        ttk.Button(bar, text="제안대로 채우기", command=self.apply_suggestions).pack(
            side="left", padx=(4, 0)
        )

        self.summary = ttk.Label(self, text="명부를 읽으면 조합이 나타납니다.",
                                 style="Hint.TLabel")
        self.summary.pack(anchor="w", pady=(0, 4))

        wrapper = ttk.Frame(self)
        wrapper.pack(fill="both", expand=True)
        self._canvas = tk.Canvas(wrapper, highlightthickness=0, height=hidpi.px(self, 260))
        scroll = ttk.Scrollbar(wrapper, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scroll.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self._table = ttk.Frame(self._canvas)
        window = self._canvas.create_window((0, 0), window=self._table, anchor="nw")
        self._table.bind(
            "<Configure>",
            lambda _e: self._canvas.configure(scrollregion=self._canvas.bbox("all")),
        )
        self._canvas.bind(
            "<Configure>", lambda e: self._canvas.itemconfigure(window, width=e.width)
        )
        self._draw()

    def rebuild(self, job_groups: list[str]) -> None:
        """묶음 이름이 바뀌면 배정 칸의 선택지를 갈아 끼운다.

        없어진 이름에 배정돼 있던 조합은 제안값으로 되돌린다. 빈 값으로 두면
        저장 때 소리 없이 빠져나가기 때문이다.
        """
        from .jobgroup import suggest_group

        self.job_groups = list(job_groups)
        for row in self._rows:
            if row["target"].get() not in job_groups:
                row["target"].set(
                    suggest_group(row["source"], row["kind"], job_groups)
                )
        self._draw()

    # ── 명부 읽기 ────────────────────────────────────────────────
    def _load_from_roster(self) -> None:
        # 산출 화면에서 이미 명부를 골랐으면 그것을 쓴다. 같은 파일을 두 번
        # 고르게 하면 서로 다른 명부를 집어 가정과 명부가 어긋날 수 있다.
        path = getattr(self.winfo_toplevel(), "roster_path", None)
        if path is None or not Path(path).exists():
            path = filedialog.askopenfilename(
                title="명부 파일 선택", parent=self,
                filetypes=[("엑셀 파일", "*.xls *.xlsm *.xlsx"), ("모든 파일", "*.*")],
            )
        if not path:
            return
        try:
            from .jobgroup import scan_roster
            from .workbook import open_workbook

            book = open_workbook(path)
            try:
                found = scan_roster(book)
            finally:
                book.close()
        except Exception as exc:
            messagebox.showerror("명부 읽기", f"직군을 읽지 못했습니다.\n\n{exc}", parent=self)
            return

        if not found:
            messagebox.showwarning(
                "명부 읽기", "명부에서 직군·임직원구분을 찾지 못했습니다.", parent=self
            )
            return
        self.set_found(found)

    def set_found(self, found: list) -> None:
        """스캔 결과로 표를 채운다. 이미 배정한 조합은 그대로 둔다."""
        from .jobgroup import suggest_group

        previous = {(r["source"], r["kind"]): r["target"].get() for r in self._rows}
        self._rows = []
        for group in found:
            saved = previous.get(group.key)
            target = saved if saved in self.job_groups else suggest_group(
                group.source_name, group.employee_type, self.job_groups
            )
            self._rows.append({
                "source": group.source_name,
                "kind": group.employee_type,
                "normalized": group.normalized_type,
                "active": group.active,
                "retired": group.retired,
                "target": tk.StringVar(value=target),
            })
        self._draw()

    def apply_suggestions(self) -> None:
        from .jobgroup import suggest_group

        for row in self._rows:
            row["target"].set(suggest_group(row["source"], row["kind"], self.job_groups))

    # ── 표 ───────────────────────────────────────────────────────
    def _draw(self) -> None:
        for child in self._table.winfo_children():
            child.destroy()

        headers = ("명부 직군", "임직원구분", "임원 판정", "재직", "퇴직", "계", "→ 변환 직군")
        for col, title in enumerate(headers):
            ttk.Label(self._table, text=title, style="Col.TLabel").grid(
                row=0, column=col, sticky="w", padx=4, pady=(0, 6)
            )

        for index, row in enumerate(self._rows, start=1):
            total = row["active"] + row["retired"]
            cells = (
                row["source"] or "(빈 값)",
                row["kind"] or "(빈 값)",
                row["normalized"],
                f"{row['active']:,}",
                f"{row['retired']:,}",
                f"{total:,}",
            )
            for col, value in enumerate(cells):
                ttk.Label(self._table, text=value).grid(
                    row=index, column=col, sticky="e" if col >= 3 else "w", padx=4, pady=1
                )
            ttk.Combobox(
                self._table, textvariable=row["target"], values=list(self.job_groups),
                state="readonly", width=12,
            ).grid(row=index, column=6, padx=4, pady=1)

        if self._rows:
            used = {r["target"].get() for r in self._rows}
            people = sum(r["active"] + r["retired"] for r in self._rows)
            self.summary.configure(
                text=f"조합 {len(self._rows)}개 · 인원 {people:,}명 → 묶음 {len(used)}개 "
                     f"({', '.join(g for g in self.job_groups if g in used)})"
            )

    # ── 값 ───────────────────────────────────────────────────────
    def rows(self) -> list[tuple[str, str, str]]:
        """(명부직군, 임직원구분, 변환직군명) 목록. 비어 있으면 매핑을 쓰지 않는다."""
        return [(r["source"], r["kind"], r["target"].get()) for r in self._rows]

    def set_rows(self, rows: list[tuple[str, str, str]]) -> None:
        """저장된 `지급규정` 시트에서 되읽는다."""
        self._rows = [
            {
                "source": source, "kind": kind, "normalized": "",
                "active": 0, "retired": 0,
                "target": tk.StringVar(value=target),
            }
            for source, kind, target in rows
        ]
        self._draw()


class _PayoutRuleTab(ttk.Frame):
    """회사 지급규정 탭 — 직군별 가입자격·정년·근속 산정방법·반올림.

    자료요청서 `1)일반사항` 6번 항목에 자유서술로 적혀 오는 내용을 산출 설정으로
    옮기는 자리다. 문구가 회사마다 달라(‘월할 계산’, ‘근로기준법 일수’,
    ‘단수개월 절사’) 자유입력으로 두면 오타 하나가 채무를 바꾼다. 그래서 고를 수
    있는 것만 버튼으로 두었다.
    """

    def __init__(self, parent, job_groups: list[str]) -> None:
        super().__init__(parent, padding=_PAD)
        self.job_groups = list(job_groups)
        self._rows: dict[str, dict[str, Any]] = {}

        ttk.Label(
            self,
            text="자료요청서 '1)일반사항' 6번(퇴직금 지급규정)을 여기에 옮깁니다. "
                 "규정 문구가 애매하면 담당자에게 확인하세요.",
            style="Hint.TLabel", wraplength=hidpi.px(self, 840), justify="left",
        ).pack(anchor="w", pady=(0, 8))

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(
            bar, text="명부 일반사항에서 규정 읽어오기", command=self._load_from_general_info
        ).pack(side="left")
        self.evidence = ttk.Label(bar, text="", style="Hint.TLabel", wraplength=hidpi.px(self, 560),
                                  justify="left")
        self.evidence.pack(side="left", padx=(10, 0))

        self._body = ttk.Frame(self)
        self._body.pack(fill="both", expand=True)
        self.rebuild(self.job_groups)

    def _load_from_general_info(self) -> None:
        """자료요청서 `1)일반사항` 6번을 읽어 초안을 채운다.

        기계가 확정할 수 없는 자유서술이므로 **초안** 만 채우고 근거 문구를
        보여 준다. 읽지 못한 항목은 손대지 않고 그대로 둔다.
        """
        path = filedialog.askopenfilename(
            title="명부 파일 선택", parent=self,
            filetypes=[("엑셀 파일", "*.xls *.xlsm *.xlsx"), ("모든 파일", "*.*")],
        )
        if not path:
            return
        try:
            from .general_info import read_general_info
            from .workbook import open_workbook

            book = open_workbook(path)
            try:
                info = read_general_info(book)
            finally:
                book.close()
        except Exception as exc:
            messagebox.showerror("일반사항", f"읽지 못했습니다.\n\n{exc}", parent=self)
            return

        draft = info.draft
        applied = {}
        for group in self.job_groups:
            item: dict[str, Any] = {}
            if draft.min_service_years is not None:
                item["min_service"] = f"{draft.min_service_years:g}"
            if draft.staff_nra is not None:
                item["nra"] = str(draft.staff_nra)
            if draft.executive_nra is not None:
                item["executive_nra"] = str(draft.executive_nra)
            if draft.service_basis is not None:
                item["basis"] = draft.service_basis
            if draft.service_fraction is not None:
                item["fraction"] = draft.service_fraction
            if draft.rounding_unit is not None:
                item["unit"] = _UNIT_LABELS.get(draft.rounding_unit, "없음")
            applied[group] = item
        self.set_values(applied)

        lines = [f"{k}: {v}" for k, v in draft.evidence.items()]
        if draft.unread:
            lines.append("확인 필요: " + ", ".join(draft.unread))
        self.evidence.configure(text="  |  ".join(lines) or "읽어낸 항목이 없습니다.")

        if draft.unread:
            messagebox.showwarning(
                "확인 필요",
                "규정에서 읽지 못한 항목이 있습니다. 직접 채워 주세요.\n\n· "
                + "\n· ".join(draft.unread),
                parent=self,
            )

    def rebuild(self, job_groups: list[str]) -> None:
        previous = self.get_values()
        for child in self._body.winfo_children():
            child.destroy()
        self._rows.clear()
        self.job_groups = list(job_groups)

        headers = (
            "직군", "산출\n제외", "가입자격\n(최소근속·년)", "정년\n(직원)",
            "정년\n(임원)", "정년초과\n가산연령", "근속 산정방법", "단수 처리",
            "지급액 반올림", "Base-up", "승급률", "퇴직률", "사망률",
        )
        for col, title in enumerate(headers):
            ttk.Label(self._body, text=title, style="Col.TLabel", justify="center").grid(
                row=0, column=col, padx=3, pady=(0, 6)
            )

        for index, group in enumerate(self.job_groups, start=1):
            saved = previous.get(group, {})
            row: dict[str, Any] = {
                "excluded": tk.BooleanVar(value=saved.get("excluded", False)),
                "min_service": tk.StringVar(value=saved.get("min_service", "1")),
                "nra": tk.StringVar(value=saved.get("nra", "60")),
                "executive_nra": tk.StringVar(value=saved.get("executive_nra", "60")),
                "add_age": tk.StringVar(value=saved.get("add_age", "2")),
                "basis": tk.StringVar(value=saved.get("basis", SERVICE_DAILY)),
                "fraction": tk.StringVar(value=saved.get("fraction", FRACTION_KEEP)),
                "unit": tk.StringVar(value=saved.get("unit", "없음")),
                "base_up": tk.StringVar(value=saved.get("base_up", "반영")),
                "promotion": tk.StringVar(value=saved.get("promotion", "반영")),
                "withdrawal": tk.StringVar(value=saved.get("withdrawal", "반영")),
                "mortality": tk.StringVar(value=saved.get("mortality", "반영")),
            }

            ttk.Label(self._body, text=group).grid(row=index, column=0, sticky="w", padx=3)
            ttk.Checkbutton(self._body, variable=row["excluded"]).grid(row=index, column=1)
            for col, key in enumerate(
                ("min_service", "nra", "executive_nra", "add_age"), start=2
            ):
                ttk.Entry(self._body, textvariable=row[key], width=8, justify="center").grid(
                    row=index, column=col, padx=3, pady=1
                )

            ttk.Combobox(
                self._body, textvariable=row["basis"], values=list(SERVICE_BASES),
                state="readonly", width=8,
            ).grid(row=index, column=6, padx=3)
            ttk.Combobox(
                self._body, textvariable=row["fraction"], values=list(FRACTION_MODES),
                state="readonly", width=8,
            ).grid(row=index, column=7, padx=3)
            ttk.Combobox(
                self._body, textvariable=row["unit"], values=list(_ROUNDING_UNITS),
                state="readonly", width=8,
            ).grid(row=index, column=8, padx=3)

            # 직군마다 쓰지 않는 가정이 있다. 임원은 정년까지 근무한다고 보아
            # 퇴직률을 빼거나, 호봉표가 없는 계약직에 승급률을 주지 않는 식이다.
            for col, key in enumerate(
                ("base_up", "promotion", "withdrawal", "mortality"), start=9
            ):
                ttk.Combobox(
                    self._body, textvariable=row[key], values=list(_APPLY_CHOICES),
                    state="readonly", width=7,
                ).grid(row=index, column=col, padx=2)

            self._rows[group] = row

        note = ttk.Frame(self._body)
        note.grid(row=len(self.job_groups) + 1, column=0, columnspan=13, sticky="w", pady=(10, 0))
        ttk.Label(
            note,
            text="근속 산정방법  일할=근속일수÷365(근로기준법)  ·  월할=완성 개월÷12  ·  "
                 "분기할=완성 분기÷4  ·  반기할=완성 반기÷2  ·  연할=완성 햇수\n"
                 "단수 처리  가산연수를 더한 뒤 적용합니다. '절사'는 "
                 "'1년이 되지 않는 단수개월은 버림' 규정에 해당합니다.\n"
                 "정년(임원)  규정에 '없음'으로 적혀 오면 직원 정년과 같게 두세요. "
                 "이미 정년을 넘긴 사람은 현재연령 + 가산연령으로 처리됩니다.\n"
                 "Base-up·승급률·퇴직률·사망률  '미반영'으로 두면 그 가정의 요율을 0 으로 봅니다. "
                 "임원을 정년까지 근무한다고 보아 퇴직률을 빼는 경우 등에 씁니다.",
            style="Hint.TLabel", justify="left",
        ).pack(anchor="w")

    def get_values(self) -> dict[str, dict[str, Any]]:
        return {
            group: {
                "excluded": row["excluded"].get(),
                "min_service": row["min_service"].get(),
                "nra": row["nra"].get(),
                "executive_nra": row["executive_nra"].get(),
                "add_age": row["add_age"].get(),
                "basis": row["basis"].get(),
                "fraction": row["fraction"].get(),
                "unit": row["unit"].get(),
                "base_up": row["base_up"].get(),
                "promotion": row["promotion"].get(),
                "withdrawal": row["withdrawal"].get(),
                "mortality": row["mortality"].get(),
            }
            for group, row in self._rows.items()
        }

    def set_values(self, values: dict[str, dict[str, Any]]) -> None:
        for group, item in values.items():
            row = self._rows.get(group)
            if row is None:
                continue
            for key, var in row.items():
                if key in item:
                    var.set(item[key])


class _LongTermRuleTab(ttk.Frame):
    """장기급여 지급유형 탭.

    근속 포상은 회사마다 주는 것이 다르다. 실제 규정에서 본 것만 해도
    '휴가 10일', '순금 30돈 + 재직기념패', '평균임금의 500%', '100만원' 이다.
    같은 표에 적힌 숫자가 유형에 따라 일수·배수·금액으로 달라지므로, 유형을
    먼저 고르게 한다.

    금·물품은 평가시점 시세가 매일 바뀌어 프로그램이 정할 수 없다. 담당자가
    환산한 금액을 넣고, 미래분은 **현물 상승률** 로 올린다.
    """

    def __init__(self, parent, job_groups: list[str]) -> None:
        super().__init__(parent, padding=_PAD)
        self.job_groups = list(job_groups)
        self._rows: dict[str, dict[str, Any]] = {}
        self._passthrough: dict[str, dict[str, Any]] = {}

        box = ttk.LabelFrame(self, text="표 값의 뜻", padding=_PAD)
        box.pack(fill="x")
        ttk.Label(
            box,
            text="휴가        '장기급여' 탭의 값 = 지급일수     → 일 기본급 × 일수 (임금상승률 반영)\n"
                 "평균임금    값 = 배수                          → 30일 평균임금 × 배수 (임금상승률 반영)\n"
                 "현물        값 = 정액(원)                      → 평가시점 시세로 환산해 넣고 현물 상승률로 올림\n"
                 "현금        값 = 정액(원)                      → 규정 금액이 고정이므로 올리지 않음",
            style="Hint.TLabel", justify="left", font=("", 9),
        ).pack(anchor="w")

        self._body = ttk.Frame(self)
        self._body.pack(fill="both", expand=True, pady=(8, 0))
        self.rebuild(self.job_groups)

    def rebuild(self, job_groups: list[str]) -> None:
        previous = self.get_values()
        for child in self._body.winfo_children():
            child.destroy()
        self._rows.clear()
        self.job_groups = list(job_groups)

        titles = ("규정명(직군)", "지급유형", "현물 상승률", "지급시점",
                  "반복 주기(년)", "누적", "지급일(월-일)", "환산 근거")
        for col, title in enumerate(titles):
            ttk.Label(self._body, text=title, style="Col.TLabel").grid(
                row=0, column=col, sticky="w", padx=3, pady=(0, 6)
            )
        self._body.columnconfigure(7, weight=1)

        for index, group in enumerate(self.job_groups, start=1):
            saved = previous.get(group, {})
            row = {
                "kind": tk.StringVar(value=saved.get("kind", LT_VACATION)),
                "escalation": tk.StringVar(value=saved.get("escalation", "")),
                "note": tk.StringVar(value=saved.get("note", "")),
                "timing": tk.StringVar(value=saved.get("timing", LT_AT_MILESTONE)),
                "every": tk.StringVar(value=saved.get("every", "")),
                "accumulate": tk.BooleanVar(value=bool(saved.get("accumulate"))),
                "anniversary": tk.StringVar(value=saved.get("anniversary", "")),
            }
            ttk.Label(self._body, text=group).grid(row=index, column=0, sticky="w", padx=3)

            combo = ttk.Combobox(
                self._body, textvariable=row["kind"], values=list(LONGTERM_TYPES),
                state="readonly", width=9,
            )
            combo.grid(row=index, column=1, padx=3, pady=1)

            entry = ttk.Entry(self._body, textvariable=row["escalation"], width=10,
                              justify="right")
            entry.grid(row=index, column=2, padx=3, pady=1)
            row["entry"] = entry

            timing = ttk.Combobox(
                self._body, textvariable=row["timing"], values=list(LONGTERM_TIMINGS),
                state="readonly", width=9,
            )
            timing.grid(row=index, column=3, padx=3, pady=1)

            ttk.Entry(self._body, textvariable=row["every"], width=8,
                      justify="right").grid(row=index, column=4, padx=3, pady=1)
            ttk.Checkbutton(self._body, variable=row["accumulate"]).grid(
                row=index, column=5, padx=3, pady=1
            )
            payday = ttk.Entry(self._body, textvariable=row["anniversary"], width=9,
                               justify="center")
            payday.grid(row=index, column=6, padx=3, pady=1)
            row["payday"] = payday

            ttk.Entry(self._body, textvariable=row["note"]).grid(
                row=index, column=7, sticky="ew", padx=3, pady=1
            )

            self._rows[group] = row
            combo.bind("<<ComboboxSelected>>", lambda _e, g=group: self._sync(g))
            timing.bind("<<ComboboxSelected>>", lambda _e, g=group: self._sync(g))
            self._sync(group)

        ttk.Label(
            self._body,
            text="현물 상승률은 '현물' 유형에만 씁니다. 환산 근거에는 "
                 "'순금 30돈 @ 2025-12-31 시세' 처럼 남겨 두세요.",
            style="Hint.TLabel", wraplength=hidpi.px(self, 780), justify="left",
        ).grid(row=len(self.job_groups) + 1, column=0, columnspan=4, sticky="w", pady=(10, 0))

    def _sync(self, group: str) -> None:
        """현물이 아니면 상승률 칸을 잠근다."""
        row = self._rows[group]
        is_in_kind = row["kind"].get() == LT_IN_KIND
        row["entry"].configure(state="normal" if is_in_kind else "disabled")
        if not is_in_kind:
            row["escalation"].set("")
        # 창립기념일은 '근속도달' 에만 뜻이 있다.
        at_milestone = row["timing"].get() == LT_AT_MILESTONE
        row["payday"].configure(state="normal" if at_milestone else "disabled")
        if not at_milestone:
            row["anniversary"].set("")

    def get_values(self) -> dict[str, dict[str, Any]]:
        # 직군이 아닌 항목 열('금', '특별상여')은 이 탭에 자리가 없다. 값만
        # 그대로 들고 있다가 돌려준다 — 빠뜨리면 창을 여는 것만으로 사라진다.
        values: dict[str, dict[str, Any]] = dict(self._passthrough)
        values.update({
            group: {
                "rule": group,
                "kind": row["kind"].get(),
                "escalation": row["escalation"].get().strip(),
                "note": row["note"].get().strip(),
                "timing": row["timing"].get(),
                "every": row["every"].get().strip(),
                "accumulate": row["accumulate"].get(),
                "anniversary": row["anniversary"].get().strip(),
            }
            for group, row in self._rows.items()
        })
        return values

    def set_values(self, values: dict[str, dict[str, Any]]) -> None:
        self._passthrough = {
            name: dict(item) for name, item in values.items() if name not in self._rows
        }
        for group, item in values.items():
            row = self._rows.get(group)
            if row is None:
                continue
            if "accumulate" in item:
                row["accumulate"].set(bool(item["accumulate"]))
            for key in ("kind", "escalation", "note", "timing", "every", "anniversary"):
                if key in item:
                    row[key].set(item[key])
            self._sync(group)


class _ExitCauseTab(ttk.Frame):
    """퇴직사유별 지급 차등 탭.

    자료요청서 6번에는 '중도퇴직시 / 사망시 / 정년퇴직시 퇴직금 지급률' 이 따로
    있고, 실제로 다르게 적어 오는 회사가 많다 — '동일, 유족지원금 5,000만원',
    '대표이사 3배, 이사/감사 1.5배', '재직 중 사망은 1년 미만도 1년으로 계산'.

    직군 수와 무관하게 줄을 늘렸다 줄였다 해야 하므로, 다른 탭과 달리 직군마다
    한 줄이 아니라 **빈 줄 여러 개** 를 두고 골라 채우게 한다.
    """

    ROWS = 8

    def __init__(self, parent, job_groups: list[str]) -> None:
        super().__init__(parent, padding=_PAD)
        self.job_groups = list(job_groups)
        self.columns = list(job_groups)
        self._rows: list[dict[str, Any]] = []

        box = ttk.LabelFrame(self, text="언제 쓰나", padding=_PAD)
        box.pack(fill="x")
        ttk.Label(
            box,
            text="중도퇴직·사망·정년퇴직의 지급이 다를 때만 채웁니다. 비우면 사유를 가리지 않습니다.\n"
                 "대체 지급률 규정   그 사유일 때 기본 규정 대신 쓸 '지급률' 탭의 열 이름\n"
                 "가산 규정          기본 급여에 더할 배수를 내는 규정 (예: 사망 시 기본급 3개월분)\n"
                 "가산액(원)         정액 가산 (예: 유족지원금 50,000,000)\n"
                 "근속 하한(년)      '사망 시 1년 미만도 1년으로 계산' 처럼 짧은 근속을 끌어올릴 때\n"
                 "가산 귀속          즉시=근속이 늘어도 안 느는 급여라 지금 전액 귀속 / 근속비례=쌓아 감\n"
                 "                   비우면 사망은 '즉시', 나머지는 '근속비례' 입니다.",
            style="Hint.TLabel", justify="left", font=("", 9),
        ).pack(anchor="w")

        self._body = ttk.Frame(self)
        self._body.pack(fill="both", expand=True, pady=(8, 0))
        self.rebuild(self.job_groups)

    def rebuild(self, job_groups: list[str], columns: list[str] | None = None) -> None:
        """:param columns: 고를 수 있는 지급률 규정. 직군만 주면 '사망가산' 처럼
        따로 만든 열을 지목할 수가 없다.
        """
        previous = self.get_values()
        for child in self._body.winfo_children():
            child.destroy()
        self._rows.clear()
        self.job_groups = list(job_groups)
        self.columns = list(columns) if columns is not None else list(job_groups)

        for col, title in enumerate(EXIT_CAUSE_HEADERS):
            ttk.Label(self._body, text=title, style="Col.TLabel").grid(
                row=0, column=col, sticky="w", padx=3, pady=(0, 6)
            )

        groups = ["", *self.columns]
        for index in range(1, self.ROWS + 1):
            saved = previous[index - 1] if index <= len(previous) else [""] * 7
            row = {key: tk.StringVar(value=saved[position]) for position, key in enumerate(
                ("rule", "cause", "alt", "extra", "amount", "floor", "basis")
            )}
            combos = (
                ("rule", groups, 12), ("cause", ["", *EXIT_CAUSES], 8),
                ("alt", groups, 12), ("extra", groups, 12),
            )
            for column, (key, values, width) in enumerate(combos):
                ttk.Combobox(
                    self._body, textvariable=row[key], values=values,
                    state="readonly", width=width,
                ).grid(row=index, column=column, padx=3, pady=1)
            for column, key in ((4, "amount"), (5, "floor")):
                ttk.Entry(self._body, textvariable=row[key], width=14,
                          justify="right").grid(row=index, column=column, padx=3, pady=1)
            ttk.Combobox(
                self._body, textvariable=row["basis"], values=["", *ATTRIBUTIONS],
                state="readonly", width=10,
            ).grid(row=index, column=6, padx=3, pady=1)
            self._rows.append(row)

    def get_values(self) -> list[list[str]]:
        """값이 든 줄만. 규정과 사유만 고른 빈 줄은 규정이 아니다."""
        result: list[list[str]] = []
        for row in self._rows:
            values = [
                row[key].get().strip()
                for key in ("rule", "cause", "alt", "extra", "amount", "floor", "basis")
            ]
            if values[0] and any(values[2:]):
                result.append(values)
        return result

    def set_values(self, rows: list[list[str]]) -> None:
        keys = ("rule", "cause", "alt", "extra", "amount", "floor", "basis")
        for row in self._rows:
            for key in keys:
                row[key].set("")
        for index, values in enumerate(rows[: len(self._rows)]):
            padded = list(values) + [""] * (len(keys) - len(values))
            for key, value in zip(keys, padded, strict=False):
                self._rows[index][key].set(str(value))


class AssumptionsEditor(tk.Toplevel):
    """산출 가정 입력 창."""

    def __init__(
        self,
        parent=None,
        *,
        job_groups: list[str] | None = None,
        on_close: Callable[[AssumptionsEditor], None] | None = None,
        on_saved: Callable[[AssumptionsEditor], None] | None = None,
        roster_path: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("산출 가정 입력")
        self.scale = hidpi.apply(self)
        self.geometry(hidpi.scale_geometry(self, "980x820"))
        self.minsize(hidpi.px(self, 820), hidpi.px(self, 680))

        self.job_groups = list(job_groups or DEFAULT_GROUPS)
        self.path: Path | None = None
        """마지막으로 저장하거나 불러온 파일. 호출한 쪽이 이어받을 수 있다."""

        self.roster_path = Path(roster_path) if roster_path else None
        """산출 화면에서 고른 명부. 직군을 읽어 올 때 파일 선택을 건너뛴다."""

        self._on_close = on_close
        self._on_saved = on_saved
        """저장 직후 호출된다. 창을 닫아야만 결과가 전달되던 것을 없애기 위한 것."""
        self.protocol("WM_DELETE_WINDOW", self.close)

        self._build_styles()
        self._build_library_bar()
        self._build_job_group_bar()
        self._build_tabs()
        self._build_actions()

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        style.configure("Hint.TLabel", foreground="#4B5563")
        style.configure("Col.TLabel", font=("", 9, "bold"))

    # ── 시스템 등록 자료 ─────────────────────────────────────────
    def _build_library_bar(self) -> None:
        """등록해 둔 표준률·금리표를 골라 오는 줄.

        한 결산기에 여러 단체를 산출할 때 금리표는 모두 같다. 단체마다 파일을
        다시 찾아 지정하면 그중 한 번만 다른 파일을 집어도 그 단체만 할인율이
        달라지는데, 눈에 띄지도 않는다.
        """
        from .library import CURVE_KIND, RATES_KIND

        box = ttk.LabelFrame(self, text="시스템 등록 자료", padding=_PAD)
        box.pack(fill="x", padx=_PAD, pady=(_PAD, 4))

        row = ttk.Frame(box)
        row.pack(fill="x")

        self.rates_var = tk.StringVar()
        self.curve_var = tk.StringVar()

        ttk.Label(row, text="표준률").pack(side="left")
        self._rates_box = ttk.Combobox(
            row, textvariable=self.rates_var, state="readonly", width=22
        )
        self._rates_box.pack(side="left", padx=(4, 2))
        ttk.Button(row, text="불러오기", width=8,
                   command=self._load_registered_rates).pack(side="left")
        ttk.Button(row, text="등록", width=5,
                   command=lambda: self._register_asset(RATES_KIND)).pack(side="left", padx=(2, 12))

        ttk.Label(row, text="금리표").pack(side="left")
        self._curve_box = ttk.Combobox(
            row, textvariable=self.curve_var, state="readonly", width=22
        )
        self._curve_box.pack(side="left", padx=(4, 2))
        self.grade_var = tk.StringVar(value="AA0")
        ttk.Combobox(row, textvariable=self.grade_var, state="readonly", width=6,
                     values=["AAA", "AA+", "AA0", "AA-", "국고채"]).pack(side="left", padx=2)
        ttk.Button(row, text="적용", width=5,
                   command=self._apply_registered_curve).pack(side="left")
        ttk.Button(row, text="등록", width=5,
                   command=lambda: self._register_asset(CURVE_KIND)).pack(side="left", padx=(2, 0))

        ttk.Label(
            box,
            text="한 번 등록해 두면 다른 단체를 산출할 때도 목록에서 골라 쓸 수 있습니다. "
                 "불러온 뒤 값을 고쳐도 됩니다 — 등록된 것은 출발점이지 확정이 아닙니다.",
            style="Hint.TLabel", wraplength=hidpi.px(self, 820), justify="left",
        ).pack(anchor="w", pady=(6, 0))

        self._refresh_library()

    def _refresh_library(self) -> None:
        from .library import CURVE_KIND, RATES_KIND, entries, resolve_default

        for kind, box, var in (
            (RATES_KIND, self._rates_box, self.rates_var),
            (CURVE_KIND, self._curve_box, self.curve_var),
        ):
            names = [e.name for e in entries(kind)]
            box.configure(values=names)
            if var.get() not in names:
                default = resolve_default(kind)
                var.set(default.name if default else "")

    def _register_asset(self, kind: str) -> None:
        """자료를 등록 폴더에 넣는다.

        메서드 이름에 ``_register`` 를 쓰면 안 된다 — ``tkinter.Misc._register`` 를
        가려 버려서, 창을 만들 때 ``protocol()`` 이 콜백을 등록하려다 이 함수를
        부르고 파일 선택 창이 떠 버린다. 창이 뜨기도 전에 멈춘다.
        """
        from .library import register

        path = filedialog.askopenfilename(
            title=f"{kind} 파일 선택", parent=self,
            filetypes=[("엑셀 파일", "*.xlsx *.xlsm"), ("모든 파일", "*.*")],
        )
        if not path:
            return
        name = simpledialog.askstring(
            "등록 이름", "목록에 표시할 이름을 정하세요.", parent=self,
            initialvalue=Path(path).stem,
        )
        if name is None:
            return
        try:
            entry = register(kind, path, name=name)
        except Exception as exc:
            messagebox.showerror("등록", f"등록하지 못했습니다.\n\n{exc}", parent=self)
            return

        self._refresh_library()
        (self.rates_var if kind == "표준률" else self.curve_var).set(entry.name)
        self.status.configure(text=f"{kind} '{entry.name}' 을(를) 등록했습니다.")

    def _load_registered_rates(self) -> None:
        """등록된 표준률 워크북을 화면으로 불러온다."""
        from .library import RATES_KIND, find_entry

        entry = find_entry(RATES_KIND, self.rates_var.get())
        if entry is None:
            messagebox.showinfo(
                "표준률", "등록된 표준률이 없습니다. [등록] 으로 파일을 넣으세요.",
                parent=self,
            )
            return
        try:
            self.load_workbook(entry.path)
        except Exception as exc:
            messagebox.showerror("표준률", f"읽지 못했습니다.\n\n{exc}", parent=self)
            return
        # 표준률은 출발점일 뿐이므로 저장 경로로 이어받지 않는다. 그대로 두면
        # [저장] 이 등록 자료를 덮어써 다른 단체까지 바뀐다.
        self.path = None
        self.status.configure(
            text=f"표준률 '{entry.name}' 을(를) 불러왔습니다. 회사에 맞게 고친 뒤 저장하세요."
        )

    def _apply_registered_curve(self) -> None:
        """등록된 금리표에서 할인율 곡선을 뽑아 할인율 탭에 채운다."""
        from .library import CURVE_KIND, find_entry
        from .yieldcurve import pick_curve, read_yield_curves

        entry = find_entry(CURVE_KIND, self.curve_var.get())
        if entry is None:
            messagebox.showinfo(
                "금리표", "등록된 금리표가 없습니다. [등록] 으로 파일을 넣으세요.",
                parent=self,
            )
            return
        try:
            curve = pick_curve(read_yield_curves(entry.path), self.grade_var.get())
        except Exception as exc:
            messagebox.showerror("금리표", f"읽지 못했습니다.\n\n{exc}", parent=self)
            return
        if curve is None:
            messagebox.showwarning(
                "금리표", f"'{self.grade_var.get()}' 등급을 찾지 못했습니다.", parent=self
            )
            return

        self._grids[DISCOUNT_SHEET].set_rows(
            [[f"{years:g}", f"{rate * 100:.4f}%"] for years, rate in curve.points]
        )
        self.status.configure(
            text=f"금리표 '{entry.name}' 의 {curve.label} 곡선({len(curve.points)}개 만기)을 "
                 "할인율에 넣었습니다."
        )

    # ── 직군 관리 ────────────────────────────────────────────────
    def _build_job_group_bar(self) -> None:
        box = ttk.LabelFrame(self, text="직군별 규정", padding=_PAD)
        box.pack(fill="x", padx=_PAD, pady=(_PAD, 4))

        ttk.Label(
            box,
            text="여기 적은 이름이 모든 가정 표의 열 머리글이 됩니다. 명부의 직급·직군을 "
                 "어느 이름에 넣을지는 '직군 매핑' 탭에서 정합니다.",
            style="Hint.TLabel", wraplength=hidpi.px(self, 820), justify="left",
        ).pack(anchor="w", pady=(0, 6))

        row = ttk.Frame(box)
        row.pack(fill="x")

        self.job_group_var = tk.StringVar(value=", ".join(self.job_groups))
        entry = ttk.Entry(row, textvariable=self.job_group_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _e: self._apply_job_groups())

        ttk.Button(row, text="적용", command=self._apply_job_groups).pack(side="left", padx=(6, 0))
        ttk.Button(row, text="기본값", command=self._reset_job_groups).pack(
            side="left", padx=(4, 0)
        )
        ttk.Button(row, text="명부에서 불러오기", command=self._load_from_roster).pack(
            side="left", padx=(4, 0)
        )

        ttk.Label(
            box,
            text="예) 정규직, 계약직, 임원   ·   생산직, 관리직, 일반직, 계약직, 임원\n"
                 "퇴직률·승급률·지급률이 실제로 다른 단위로만 나누세요. 잘게 나눌수록 "
                 "구간별 인원이 줄어 기초율의 통계적 신뢰도가 떨어집니다.",
            style="Hint.TLabel", justify="left",
        ).pack(anchor="w", pady=(6, 0))

    def _apply_job_groups(self) -> None:
        names = [text(n) for n in self.job_group_var.get().split(",")]
        names = [n for n in names if n]
        if not names:
            messagebox.showwarning("직군", "직군을 하나 이상 입력하세요.", parent=self)
            return
        if len(set(names)) != len(names):
            messagebox.showwarning("직군", "같은 직군 이름이 두 번 들어갔습니다.", parent=self)
            return
        self._set_job_groups(names)

    def _reset_job_groups(self) -> None:
        self._set_job_groups(list(DEFAULT_GROUPS))

    def _set_job_groups(self, names: list[str]) -> None:
        """직군 묶음을 바꾸고 모든 탭의 열을 다시 만든다.

        위쪽 입력줄과 '직군 매핑' 탭 두 곳에서 바꿀 수 있으므로 한 자리로 모은다.
        """
        self.job_groups = list(names)
        self.job_group_var.set(", ".join(names))
        for grid in self._grids.values():
            grid.rebuild_columns(names)
        self._map_tab.rebuild(names)
        self._payout_tab.rebuild(names)
        self._longterm_tab.rebuild(names)
        self._refresh_benefit_columns()

    def _refresh_benefit_columns(self) -> None:
        """지급률 표의 열이 바뀌면 그 열을 참조하는 두 탭을 따라 맞춘다.

        방식·수식은 열마다 있고, 퇴직사유는 열 이름을 지목한다. 표만 고치고
        두 탭을 두면 새 열이 규정 없이 남거나 없는 이름을 가리키게 된다.
        """
        columns = self._grids[BENEFIT_SHEET].columns
        split = cause_split_rules(self.state())
        self._rule_tab.rebuild(self.job_groups, columns, split)
        self._cause_tab.rebuild(self.job_groups, columns)

    def _load_from_roster(self) -> None:
        """명부의 ``Input`` 시트에서 변환 직군명을 가져온다. 이름 불일치를 막는다."""
        path = filedialog.askopenfilename(
            title="명부 파일 선택", parent=self,
            filetypes=[("엑셀 파일", "*.xlsm *.xlsx"), ("모든 파일", "*.*")],
        )
        if not path:
            return
        try:
            from .config import read_config
            from .workbook import open_workbook

            wb = open_workbook(path)
            try:
                config = read_config(wb)
            finally:
                wb.close()
        except Exception as exc:
            messagebox.showerror("명부 읽기", f"직군을 읽지 못했습니다.\n\n{exc}", parent=self)
            return

        names = [r.mapped_name for r in config.job_group_rules if r.mapped_name]
        if not names:
            messagebox.showwarning("명부 읽기", "Input 시트에서 직군을 찾지 못했습니다.", parent=self)
            return
        self.job_group_var.set(", ".join(dict.fromkeys(names)))
        self._apply_job_groups()

    # ── 탭 ───────────────────────────────────────────────────────
    def _build_tabs(self) -> None:
        book = ttk.Notebook(self)
        book.pack(fill="both", expand=True, padx=_PAD, pady=4)

        # 직군 묶음이 다른 모든 탭의 열 머리글을 정하므로 맨 앞에 둔다.
        self._map_tab = _JobGroupMapTab(book, self.job_groups)
        book.add(self._map_tab, text="직군 매핑")

        self._grids: dict[str, _Grid] = {}
        for spec in SPECS:
            grid = _Grid(book, spec, self.job_groups)
            book.add(grid, text=spec.tab)
            self._grids[spec.sheet] = grid
        # 지급률 열이 늘면 방식·수식 줄과 퇴직사유 선택지가 따라와야 한다.
        self._grids[BENEFIT_SHEET].on_columns_changed = self._refresh_benefit_columns

        self._payout_tab = _PayoutRuleTab(book, self.job_groups)
        book.add(self._payout_tab, text="지급규정")

        self._rule_tab = _BenefitRuleTab(book, self.job_groups,
                                        on_split=self._toggle_cause_split)
        book.add(self._rule_tab, text="지급률 규정")

        self._cause_tab = _ExitCauseTab(book, self.job_groups)
        book.add(self._cause_tab, text="퇴직사유")

        self._longterm_tab = _LongTermRuleTab(book, self.job_groups)
        book.add(self._longterm_tab, text="장기급여 유형")

    # ── 저장·불러오기 ────────────────────────────────────────────
    def _build_actions(self) -> None:
        bar = ttk.Frame(self, padding=(_PAD, 4, _PAD, _PAD))
        bar.pack(fill="x")

        self.status = ttk.Label(bar, text="", style="Hint.TLabel")
        self.status.pack(side="left")

        ttk.Button(bar, text="닫기", command=self.close).pack(side="right")
        ttk.Button(bar, text="저장", command=self.save).pack(side="right", padx=6)
        ttk.Button(bar, text="불러오기", command=self.load).pack(side="right")
        ttk.Button(bar, text="예시 채우기", command=self.fill_example).pack(side="right", padx=6)

    def fill_example(self) -> None:
        """빈 화면에서 시작하기 어려우니 흔한 값으로 한 벌 채워 준다."""
        def wide(sheet: str) -> int:
            """그 표의 열 수. 따로 만든 열이 있으면 직군 수보다 많다."""
            return len(self._grids[sheet].columns)

        count = len(self.job_groups)
        self._grids[DISCOUNT_SHEET].set_rows([["1", "4.5%"]])
        self._grids[SALARY_SHEET].set_rows([["1", "3.0%"], ["6", "2.5%"]])
        self._grids[PROMOTION_SHEET].set_rows(
            [["20", *["2.0%"] * count], ["40", *["1.0%"] * count], ["55", *["0.0%"] * count]]
        )
        self._grids[WITHDRAWAL_SHEET].set_rows(
            [["20", *["15%"] * count], ["35", *["6%"] * count], ["50", *["2%"] * count]]
        )
        self._grids[MORTALITY_SHEET].set_rows(
            [["20", "0.0004", "0.0002"], ["40", "0.0012", "0.0006"], ["60", "0.006", "0.0025"]]
        )
        benefit = wide(BENEFIT_SHEET)
        self._grids[BENEFIT_SHEET].set_rows(
            [["1", *["1.0"] * benefit], ["10", *["10.0"] * benefit],
             ["20", *["20.0"] * benefit]]
        )
        longterm = wide(LONGTERM_SHEET)
        self._grids[LONGTERM_SHEET].set_rows(
            [["10", *["10"] * longterm], ["20", *["20"] * longterm],
             ["30", *["30"] * longterm]]
        )
        self.status.configure(text="예시 값을 채웠습니다. 회사 규정에 맞게 고쳐 주세요.")

    def load(self) -> None:
        path = filedialog.askopenfilename(
            title="기초율 파일 열기", parent=self,
            filetypes=[("엑셀 파일", "*.xlsx *.xlsm"), ("모든 파일", "*.*")],
        )
        if not path:
            return
        try:
            self.load_workbook(Path(path))
        except Exception as exc:
            messagebox.showerror("불러오기", f"파일을 읽지 못했습니다.\n\n{exc}", parent=self)
            return
        self.path = Path(path)
        self.status.configure(text=f"불러왔습니다: {Path(path).name}")

    def load_workbook(self, path: Path) -> None:
        self.apply_state(_read_state(path))

    def apply_state(self, state: dict[str, Any]) -> None:
        """state 한 벌을 화면 전체에 앉힌다. 파일에서 읽든 화면에서 만들든 같다."""
        if state["job_groups"]:
            self.job_group_var.set(", ".join(state["job_groups"]))
            self._apply_job_groups()

        for spec in SPECS:
            grid = self._grids[spec.sheet]
            item = state["grids"][spec.sheet]
            if spec.key_choices:
                grid.key_var.set(item["key"])
            grid.load(item["rows"], item.get("extra"))

        self._payout_tab.set_values(state["payout"])
        if state["mapping"]:
            self._map_tab.set_rows([tuple(row) for row in state["mapping"]])
        self._longterm_tab.set_values(state["longterm_rules"])
        # 퇴직사유를 먼저 앉힌다. 어느 직군이 갈려 있는지는 '표의 열' 과
        # '퇴직사유 줄' 을 함께 봐야 알 수 있어, 둘 중 하나만 새것이면
        # 갈라 놓은 것을 못 알아본다.
        self._cause_tab.set_values(state.get("exit_causes", []))
        self._refresh_benefit_columns()
        self._rule_tab.set_values(state["benefit_rules"])

    def _toggle_cause_split(self, rule: str, split: bool) -> None:
        """지급률 열 하나를 정년·중도·사망으로 가르거나 도로 접는다."""
        if not split and not messagebox.askyesno(
            "사유별 차등",
            f"'{rule}' 의 사유별 열 세 개를 지웁니다.\n"
            "그 열에 적은 배수도 함께 사라집니다. 계속할까요?",
            parent=self,
        ):
            self._refresh_benefit_columns()   # 체크를 되돌린다
            return
        try:
            change = split_benefit_by_cause if split else merge_benefit_causes
            self.apply_state(change(self.state(), rule))
        except Exception as exc:
            messagebox.showerror("사유별 차등", str(exc), parent=self)
            self._refresh_benefit_columns()
            return
        self.status.configure(text=(
            f"'{rule}' 을(를) {' · '.join(EXIT_CAUSES)} 세 열로 갈랐습니다. "
            "사유마다 다른 배수만 채우세요 — 비운 열은 원래 규정을 씁니다."
            if split else f"'{rule}' 의 사유별 열을 접었습니다."
        ))

    def save(self) -> None:
        problems = self._rule_tab.problems()
        if problems:
            messagebox.showerror(
                "지급률 규정",
                "저장하기 전에 고쳐 주세요.\n\n· " + "\n· ".join(problems),
                parent=self,
            )
            return

        if not self._grids[DISCOUNT_SHEET].get_rows():
            messagebox.showerror(
                "할인율", "할인율은 반드시 입력해야 합니다. 없으면 채무를 산출할 수 없습니다.",
                parent=self,
            )
            return

        path = filedialog.asksaveasfilename(
            title="기초율 저장", parent=self, defaultextension=".xlsx",
            initialfile=self.path.name if self.path else "기초율.xlsx",
            filetypes=[("엑셀 파일", "*.xlsx")],
        )
        if not path:
            return

        try:
            self.write(Path(path))
        except Exception as exc:
            messagebox.showerror("저장", f"저장하지 못했습니다.\n\n{exc}", parent=self)
            return

        self.path = Path(path)
        self.status.configure(text=f"저장했습니다: {Path(path).name}")
        # 창을 닫아야만 산출 화면에 전달되던 것을 없앤다. 저장한 순간 반영한다.
        if self._on_saved is not None:
            self._on_saved(self)
        messagebox.showinfo(
            "저장",
            f"기초율을 저장했습니다.\n\n{path}\n\n산출 화면의 기초율 파일 칸에 "
            "이 파일을 지정했습니다.",
            parent=self,
        )

    def state(self) -> dict[str, Any]:
        """화면의 입력을 웹앱과 공유하는 state 자료구조로 모은다."""
        return {
            "job_groups": list(self.job_groups),
            "grids": {
                spec.sheet: {
                    "key": self._grids[spec.sheet].key_var.get(),
                    "rows": self._grids[spec.sheet].get_rows(),
                    "extra": list(self._grids[spec.sheet].extra),
                }
                for spec in SPECS
            },
            "payout": self._payout_tab.get_values(),
            "benefit_rules": self._rule_tab.get_values(),
            "longterm_rules": self._longterm_tab.get_values(),
            "exit_causes": self._cause_tab.get_values(),
            "mapping": [list(row) for row in self._map_tab.rows()],
        }

    def collect(self) -> tuple[
        dict[str, tuple[list[str], list[list[Any]]]],
        dict[str, tuple[str, str]],
        dict[str, tuple[str, float, str]],
    ]:
        """화면의 입력을 파일로 쓸 수 있는 평범한 자료구조로 모은다."""
        return _state_to_sheets(self.state())

    def write(self, path: Path) -> Path:
        """현재 입력을 기초율 워크북으로 쓴다."""
        from .assumptions import write_assumptions

        sheets, rules, longterm = self.collect()
        return write_assumptions(path, sheets, rules, longterm)


    def has_input(self) -> bool:
        """저장할 만한 입력이 들어 있는지. 할인율 한 줄이라도 있으면 참."""
        return any(grid.get_rows() for grid in self._grids.values())

    def close(self) -> None:
        """창을 닫는다. 호출한 쪽에 결과를 알린 뒤 정리한다.

        한 번도 저장하지 않은 채 닫으면 입력이 통째로 사라진다. 창이 모달이라
        닫기 전에는 산출 화면으로 갈 수도 없어서, 공들여 채운 표가 조용히
        날아가는 일이 실제로 있었다. 그래서 닫기 전에 한 번 묻는다.
        """
        if self.path is None and self.has_input():
            answer = messagebox.askyesnocancel(
                "저장하지 않고 닫기",
                "입력한 기초율을 아직 저장하지 않았습니다.\n"
                "지금 닫으면 입력한 내용이 사라집니다.\n\n"
                "저장할까요?",
                parent=self,
            )
            if answer is None:          # 취소 — 창을 그대로 둔다
                return
            if answer:
                self.save()
                if self.path is None:   # 저장 대화상자를 취소했다
                    return

        if self._on_close is not None:
            self._on_close(self)
        self.destroy()


def open_editor(
    parent=None,
    *,
    job_groups: list[str] | None = None,
    on_close: Callable[[AssumptionsEditor], None] | None = None,
    on_saved: Callable[[AssumptionsEditor], None] | None = None,
    roster_path: Path | None = None,
) -> AssumptionsEditor:
    """가정 입력 창을 띄운다."""
    editor = AssumptionsEditor(
        parent, job_groups=job_groups, on_close=on_close,
        on_saved=on_saved, roster_path=roster_path,
    )
    if parent is not None:
        editor.transient(parent)
    editor.grab_set()
    return editor


def main() -> int:
    """가정 입력기만 단독 실행."""
    hidpi.declare_dpi_aware()   # 창을 만들기 전이어야 효과가 있다
    root = tk.Tk()
    root.withdraw()
    editor = AssumptionsEditor(root)
    editor.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    return 0
