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
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .actuarial import (
    FRACTION_HALF,
    FRACTION_KEEP,
    FRACTION_MODES,
    SERVICE_BASES,
    SERVICE_DAILY,
)
from .assumptions import (
    BENEFIT_MODES,
    BENEFIT_RULE_SHEET,
    BENEFIT_SHEET,
    CUMULATIVE,
    DISCOUNT_SHEET,
    FORMULA,
    LONGTERM_RULE_SHEET,
    LONGTERM_SHEET,
    LONGTERM_TYPES,
    LT_IN_KIND,
    LT_VACATION,
    MORTALITY_SHEET,
    PROMOTION_SHEET,
    SALARY_SHEET,
    WITHDRAWAL_SHEET,
    write_assumptions,
)
from .formula import FUNCTIONS, VARIABLES, Formula, FormulaError
from .normalize import text

__all__ = ["AssumptionsEditor", "open_editor"]

_PAD = 8
_DEFAULT_ROWS = 12

#: 지급액 반올림 단위 선택지. 화면에 보이는 글자 → 원 단위 값.
_ROUNDING_UNITS = ("없음", "1원", "10원", "100원", "1,000원")
_ROUNDING_VALUES = {"없음": 0, "1원": 1, "10원": 10, "100원": 100, "1,000원": 1000}
_UNIT_LABELS = {v: k for k, v in _ROUNDING_VALUES.items()}

#: 지급규정을 담는 시트. ``Input`` 시트와 열 배치가 같아 그대로 읽힌다.
PAYOUT_SHEET = "지급규정"
_PAYOUT_HEADERS = (
    "명부직군", "변환직군명", "퇴직급여 정년연령", "장기급여 정년연령",
    "정년초과 가산연령", "퇴직급여 지급률 규정", "장기급여 지급률 규정",
    "퇴직급여 퇴직률 규정", "퇴직급여 승급률 규정", "장기급여 퇴직률 규정",
    "장기급여 승급률 규정", "퇴직자 퇴직급여 퇴직률 규정", "퇴직자 장기급여 퇴직률 규정",
    "가입자격(최소근속)", "임원 정년연령", "임원 정년초과 가산연령", "산출 제외",
    "근속 산정방법", "단수 처리", "지급액 반올림 단위", "반올림 방식",
)


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

    @property
    def per_job_group(self) -> bool:
        return not self.fixed_headers


SPECS: tuple[SheetSpec, ...] = (
    SheetSpec(
        DISCOUNT_SHEET, "할인율", "연차", ("할인율",),
        "한 줄만 넣으면 전 기간 단일 할인율입니다. 여러 줄이면 각 연차의 현물이자율(spot)로 봅니다.",
    ),
    SheetSpec(
        SALARY_SHEET, "Base-up", "연차", ("Base-up 상승률",),
        "승진·승급을 제외한 공통 임금인상률입니다. 마지막 줄의 값이 그 이후 전 기간에 적용됩니다.",
    ),
    SheetSpec(
        PROMOTION_SHEET, "승급률", "연령", (),
        "승진·호봉 승급에 따른 인상률입니다. Base-up 과 더해져 총 임금상승률이 됩니다.",
        key_choices=("연령", "근속"),
    ),
    SheetSpec(
        WITHDRAWAL_SHEET, "퇴직률", "연령", (),
        "사망을 제외한 연간 중도퇴직률입니다.",
        key_choices=("연령", "근속"),
    ),
    SheetSpec(
        MORTALITY_SHEET, "사망률", "연령", ("남자", "여자"),
        "연간 사망률 qx 입니다. 사용한 경험생명표의 출처를 비고에 남겨 두세요.",
    ),
    SheetSpec(
        BENEFIT_SHEET, "지급률", "근속연수", (),
        "30일 평균임금 대비 지급배수입니다. 값의 의미는 '지급률 규정' 탭의 방식에 따라 달라집니다.",
    ),
    SheetSpec(
        LONGTERM_SHEET, "장기급여", "근속연수", (),
        "근속 포상·장기근속휴가의 지급일수입니다(일 기본급 × 일수).",
    ),
)


class _Grid(ttk.Frame):
    """스크롤되는 표 입력 위젯.

    엑셀처럼 셀을 직접 치되, 열 구성은 :class:`SheetSpec` 과 직군 목록이 정한다.
    """

    def __init__(self, parent, spec: SheetSpec, job_groups: list[str]) -> None:
        super().__init__(parent, padding=_PAD)
        self.spec = spec
        self.job_groups = job_groups
        self._entries: list[list[ttk.Entry]] = []
        self.key_var = tk.StringVar(value=spec.key_header)

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

        if self.spec.note:
            ttk.Label(
                self, text=f"· {self.spec.note}", style="Hint.TLabel", wraplength=760,
                justify="left",
            ).pack(fill="x", pady=(6, 4))

    def _build_body(self) -> None:
        wrapper = ttk.Frame(self)
        wrapper.pack(fill="both", expand=True)

        self._canvas = tk.Canvas(wrapper, highlightthickness=0, height=260)
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
    def headers(self) -> list[str]:
        if self.spec.per_job_group:
            return [self.key_var.get(), *self.job_groups]
        return [self.key_var.get(), *self.spec.fixed_headers]

    def _relabel_key(self) -> None:
        self._header_labels[0].configure(text=self.key_var.get())

    def rebuild_columns(self, job_groups: list[str]) -> None:
        """직군이 바뀌면 값을 지키면서 열을 다시 만든다."""
        if not self.spec.per_job_group:
            return
        previous = self.get_rows()
        old_groups = list(self.job_groups)
        self.job_groups = list(job_groups)

        # 직군명을 키로 값을 옮긴다. 이름이 사라진 직군의 값은 버린다.
        remapped: list[list[str]] = []
        for row in previous:
            by_group = dict(zip(old_groups, row[1:], strict=False))
            remapped.append([row[0], *[by_group.get(g, "") for g in self.job_groups]])
        self.set_rows(remapped)

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

    def __init__(self, parent, job_groups: list[str]) -> None:
        super().__init__(parent, padding=_PAD)
        self.job_groups = list(job_groups)
        self._rows: dict[str, dict[str, Any]] = {}

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
            justify="left", style="Hint.TLabel", wraplength=780,
        ).pack(anchor="w")
        ttk.Label(
            box,
            text="함수  " + " ".join(sorted(FUNCTIONS)),
            justify="left", style="Hint.TLabel", wraplength=780,
        ).pack(anchor="w")
        ttk.Label(
            box,
            text='예시  =IF(t<10, t*1.0, 10 + (t-10)*2.0)     '
                 '=IF(제도="DB", t*1.5, t)     =MIN(t, 30)',
            justify="left", style="Hint.TLabel",
        ).pack(anchor="w", pady=(2, 0))

    def rebuild(self, job_groups: list[str]) -> None:
        """직군 목록이 바뀌면 행을 다시 만든다. 기존 입력은 이름으로 이어받는다."""
        previous = self.get_values()
        for child in self._body.winfo_children():
            child.destroy()
        self._rows.clear()
        self.job_groups = list(job_groups)

        headers = ("규정명(직군)", "방식", "수식", "")
        for col, title in enumerate(headers):
            ttk.Label(self._body, text=title, style="Col.TLabel").grid(
                row=0, column=col, sticky="w", padx=2, pady=(0, 4)
            )
        self._body.columnconfigure(2, weight=1)

        for index, group in enumerate(self.job_groups, start=1):
            saved = previous.get(group, {})
            mode_var = tk.StringVar(value=saved.get("mode", CUMULATIVE))
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

            combo.bind("<<ComboboxSelected>>", lambda _e, g=group: self._sync_row(g))
            formula_var.trace_add("write", lambda *_a, g=group: self._validate_row(g))
            self._sync_row(group)

        bar = ttk.Frame(self._body)
        bar.grid(row=len(self.job_groups) + 1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Button(bar, text="수식 미리보기", command=self.preview).pack(side="left")

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
        window.geometry("560x420")

        box = ttk.Frame(window, padding=_PAD)
        box.pack(fill="both", expand=True)
        ttk.Label(
            box, text="연령 40세 · 정년 60세 · 제도 DB 기준으로 계산한 근속연수별 배수입니다.",
            style="Hint.TLabel", wraplength=520,
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
        return {
            group: {"mode": row["mode"].get(), "formula": row["formula"].get().strip()}
            for group, row in self._rows.items()
        }

    def set_values(self, values: dict[str, dict[str, str]]) -> None:
        for group, item in values.items():
            row = self._rows.get(group)
            if row is None:
                continue
            row["mode"].set(item.get("mode") or CUMULATIVE)
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
            style="Hint.TLabel", wraplength=840, justify="left",
        ).pack(anchor="w", pady=(0, 8))

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(
            bar, text="명부 일반사항에서 규정 읽어오기", command=self._load_from_general_info
        ).pack(side="left")
        self.evidence = ttk.Label(bar, text="", style="Hint.TLabel", wraplength=560,
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
            "지급액 반올림",
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

            self._rows[group] = row

        note = ttk.Frame(self._body)
        note.grid(row=len(self.job_groups) + 1, column=0, columnspan=9, sticky="w", pady=(10, 0))
        ttk.Label(
            note,
            text="근속 산정방법  일할=근속일수÷365(근로기준법)  ·  월할=완성 개월÷12  ·  "
                 "분기할=완성 분기÷4  ·  반기할=완성 반기÷2  ·  연할=완성 햇수\n"
                 "단수 처리  가산연수를 더한 뒤 적용합니다. '절사'는 "
                 "'1년이 되지 않는 단수개월은 버림' 규정에 해당합니다.\n"
                 "정년(임원)  규정에 '없음'으로 적혀 오면 직원 정년과 같게 두세요. "
                 "이미 정년을 넘긴 사람은 현재연령 + 가산연령으로 처리됩니다.",
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
    '휴가 10일', '현물 포상 + 기념품', '평균임금의 500%', '100만원' 이다.
    같은 표에 적힌 숫자가 유형에 따라 일수·배수·금액으로 달라지므로, 유형을
    먼저 고르게 한다.

    금·물품은 평가시점 시세가 매일 바뀌어 프로그램이 정할 수 없다. 담당자가
    환산한 금액을 넣고, 미래분은 **현물 상승률** 로 올린다.
    """

    def __init__(self, parent, job_groups: list[str]) -> None:
        super().__init__(parent, padding=_PAD)
        self.job_groups = list(job_groups)
        self._rows: dict[str, dict[str, Any]] = {}

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

        for col, title in enumerate(("규정명(직군)", "지급유형", "현물 상승률", "환산 근거")):
            ttk.Label(self._body, text=title, style="Col.TLabel").grid(
                row=0, column=col, sticky="w", padx=3, pady=(0, 6)
            )
        self._body.columnconfigure(3, weight=1)

        for index, group in enumerate(self.job_groups, start=1):
            saved = previous.get(group, {})
            row = {
                "kind": tk.StringVar(value=saved.get("kind", LT_VACATION)),
                "escalation": tk.StringVar(value=saved.get("escalation", "")),
                "note": tk.StringVar(value=saved.get("note", "")),
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

            ttk.Entry(self._body, textvariable=row["note"]).grid(
                row=index, column=3, sticky="ew", padx=3, pady=1
            )

            self._rows[group] = row
            combo.bind("<<ComboboxSelected>>", lambda _e, g=group: self._sync(g))
            self._sync(group)

        ttk.Label(
            self._body,
            text="현물 상승률은 '현물' 유형에만 씁니다. 환산 근거에는 "
                 "'현물 포상 @ 2025-12-31 시세' 처럼 남겨 두세요.",
            style="Hint.TLabel", wraplength=780, justify="left",
        ).grid(row=len(self.job_groups) + 1, column=0, columnspan=4, sticky="w", pady=(10, 0))

    def _sync(self, group: str) -> None:
        """현물이 아니면 상승률 칸을 잠근다."""
        row = self._rows[group]
        is_in_kind = row["kind"].get() == LT_IN_KIND
        row["entry"].configure(state="normal" if is_in_kind else "disabled")
        if not is_in_kind:
            row["escalation"].set("")

    def get_values(self) -> dict[str, dict[str, str]]:
        return {
            group: {
                "kind": row["kind"].get(),
                "escalation": row["escalation"].get().strip(),
                "note": row["note"].get().strip(),
            }
            for group, row in self._rows.items()
        }

    def set_values(self, values: dict[str, dict[str, str]]) -> None:
        for group, item in values.items():
            row = self._rows.get(group)
            if row is None:
                continue
            for key in ("kind", "escalation", "note"):
                if key in item:
                    row[key].set(item[key])
            self._sync(group)


class AssumptionsEditor(tk.Toplevel):
    """산출 가정 입력 창."""

    def __init__(
        self,
        parent=None,
        *,
        job_groups: list[str] | None = None,
        on_close: Callable[[AssumptionsEditor], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("산출 가정 입력")
        self.geometry("900x720")
        self.minsize(760, 600)

        self.job_groups = list(job_groups or ["정규직", "임원"])
        self.path: Path | None = None
        """마지막으로 저장하거나 불러온 파일. 호출한 쪽이 이어받을 수 있다."""

        self._on_close = on_close
        self.protocol("WM_DELETE_WINDOW", self.close)

        self._build_styles()
        self._build_job_group_bar()
        self._build_tabs()
        self._build_actions()

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        style.configure("Hint.TLabel", foreground="#4B5563")
        style.configure("Col.TLabel", font=("", 9, "bold"))

    # ── 직군 관리 ────────────────────────────────────────────────
    def _build_job_group_bar(self) -> None:
        box = ttk.LabelFrame(self, text="직군별 규정", padding=_PAD)
        box.pack(fill="x", padx=_PAD, pady=(_PAD, 4))

        ttk.Label(
            box,
            text="여기 적은 직군 이름이 각 가정 표의 열 머리글이 됩니다. "
                 "명부의 Input 시트 '변환 직군명' 과 글자까지 같아야 합니다.",
            style="Hint.TLabel", wraplength=820, justify="left",
        ).pack(anchor="w", pady=(0, 6))

        row = ttk.Frame(box)
        row.pack(fill="x")

        self.job_group_var = tk.StringVar(value=", ".join(self.job_groups))
        entry = ttk.Entry(row, textvariable=self.job_group_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _e: self._apply_job_groups())

        ttk.Button(row, text="적용", command=self._apply_job_groups).pack(side="left", padx=(6, 0))
        ttk.Button(row, text="명부에서 불러오기", command=self._load_from_roster).pack(
            side="left", padx=(4, 0)
        )

    def _apply_job_groups(self) -> None:
        names = [text(n) for n in self.job_group_var.get().split(",")]
        names = [n for n in names if n]
        if not names:
            messagebox.showwarning("직군", "직군을 하나 이상 입력하세요.", parent=self)
            return
        if len(set(names)) != len(names):
            messagebox.showwarning("직군", "같은 직군 이름이 두 번 들어갔습니다.", parent=self)
            return

        self.job_groups = names
        self.job_group_var.set(", ".join(names))
        for grid in self._grids.values():
            grid.rebuild_columns(names)
        self._payout_tab.rebuild(names)
        self._rule_tab.rebuild(names)
        self._longterm_tab.rebuild(names)

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

        self._grids: dict[str, _Grid] = {}
        for spec in SPECS:
            grid = _Grid(book, spec, self.job_groups)
            book.add(grid, text=spec.tab)
            self._grids[spec.sheet] = grid

        self._payout_tab = _PayoutRuleTab(book, self.job_groups)
        book.add(self._payout_tab, text="지급규정")

        self._rule_tab = _BenefitRuleTab(book, self.job_groups)
        book.add(self._rule_tab, text="지급률 규정")

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
        self._grids[BENEFIT_SHEET].set_rows(
            [["1", *["1.0"] * count], ["10", *["10.0"] * count], ["20", *["20.0"] * count]]
        )
        self._grids[LONGTERM_SHEET].set_rows(
            [["10", *["10"] * count], ["20", *["20"] * count], ["30", *["30"] * count]]
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
        from .workbook import open_workbook

        wb = open_workbook(path)
        try:
            # 직군은 지급률 시트의 열 머리글에서 가져온다.
            groups: list[str] = []
            for spec in SPECS:
                if not spec.per_job_group or spec.sheet not in wb.sheetnames:
                    continue
                ws = wb[spec.sheet]
                groups = [
                    text(ws.cell(1, c).value)
                    for c in range(2, ws.max_column + 1)
                    if text(ws.cell(1, c).value)
                ]
                if groups:
                    break
            if groups:
                self.job_group_var.set(", ".join(groups))
                self._apply_job_groups()

            for spec in SPECS:
                grid = self._grids[spec.sheet]
                if spec.sheet not in wb.sheetnames:
                    grid.set_rows([])
                    continue
                ws = wb[spec.sheet]
                if spec.key_choices:
                    header = text(ws.cell(1, 1).value)
                    grid.key_var.set("근속" if "근속" in header else "연령")

                width = len(grid.headers)
                rows = []
                for row in range(2, ws.max_row + 1):
                    values = [_cell_text(ws.cell(row, c).value) for c in range(1, width + 1)]
                    if any(values):
                        rows.append(values)
                grid.set_rows(rows)

            if PAYOUT_SHEET in wb.sheetnames:
                ws = wb[PAYOUT_SHEET]
                payout: dict[str, dict[str, Any]] = {}
                for row in range(2, ws.max_row + 1):
                    name = text(ws.cell(row, 1).value)
                    if not name:
                        continue
                    unit = _as_int(_cell_text(ws.cell(row, 20).value), 0)
                    payout[name] = {
                        "nra": _cell_text(ws.cell(row, 3).value) or "60",
                        "add_age": _cell_text(ws.cell(row, 5).value) or "2",
                        "min_service": _cell_text(ws.cell(row, 14).value) or "0",
                        "executive_nra": _cell_text(ws.cell(row, 15).value) or "60",
                        "excluded": text(ws.cell(row, 17).value).upper() in ("Y", "제외"),
                        "basis": text(ws.cell(row, 18).value) or SERVICE_DAILY,
                        "fraction": text(ws.cell(row, 19).value) or FRACTION_KEEP,
                        "unit": _UNIT_LABELS.get(unit, "없음"),
                    }
                self._payout_tab.set_values(payout)

            if LONGTERM_RULE_SHEET in wb.sheetnames:
                ws = wb[LONGTERM_RULE_SHEET]
                longterm: dict[str, dict[str, str]] = {}
                for row in range(2, ws.max_row + 1):
                    name = text(ws.cell(row, 1).value)
                    if not name:
                        continue
                    raw = ws.cell(row, 3).value
                    longterm[name] = {
                        "kind": text(ws.cell(row, 2).value) or LT_VACATION,
                        "escalation": f"{float(raw) * 100:g}%" if isinstance(raw, (int, float)) and raw else "",
                        "note": text(ws.cell(row, 4).value),
                    }
                self._longterm_tab.set_values(longterm)

            if BENEFIT_RULE_SHEET in wb.sheetnames:
                ws = wb[BENEFIT_RULE_SHEET]
                values: dict[str, dict[str, str]] = {}
                for row in range(2, ws.max_row + 1):
                    name = text(ws.cell(row, 1).value)
                    if not name:
                        continue
                    values[name] = {
                        "mode": text(ws.cell(row, 2).value) or CUMULATIVE,
                        "formula": text(ws.cell(row, 3).value),
                    }
                self._rule_tab.set_values(values)
        finally:
            wb.close()

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
        messagebox.showinfo("저장", f"기초율을 저장했습니다.\n\n{path}", parent=self)

    def collect(self) -> tuple[
        dict[str, tuple[list[str], list[list[Any]]]],
        dict[str, tuple[str, str]],
        dict[str, tuple[str, float, str]],
    ]:
        """화면의 입력을 파일로 쓸 수 있는 평범한 자료구조로 모은다."""
        sheets = {
            spec.sheet: (
                self._grids[spec.sheet].headers,
                [[_parse_cell(v) for v in row] for row in self._grids[spec.sheet].get_rows()],
            )
            for spec in SPECS
        }
        rules = {
            group: (item["mode"], item["formula"])
            for group, item in self._rule_tab.get_values().items()
        }

        # 지급규정 탭 → 'Input' 시트와 같은 배치로 한 장 더 만든다. 산출 때
        # 그대로 읽히도록 열 순서를 맞춘다.
        payout = self._payout_tab.get_values()
        rows: list[list[Any]] = []
        for group, item in payout.items():
            rows.append([
                group, group,
                _as_int(item["nra"], 60), _as_int(item["nra"], 60),
                _as_int(item["add_age"], 2),
                "", "", "", "", "", "", "", "",
                _as_float(item["min_service"], 0.0),
                _as_int(item["executive_nra"], 0),
                _as_int(item["add_age"], 2),
                "Y" if item["excluded"] else "",
                item["basis"], item["fraction"],
                _ROUNDING_VALUES.get(item["unit"], 0), FRACTION_HALF,
            ])
        sheets[PAYOUT_SHEET] = (list(_PAYOUT_HEADERS), rows)

        longterm = {
            group: (
                item["kind"],
                _as_float(item["escalation"].rstrip("%"), 0.0) / 100.0
                if item["escalation"].endswith("%")
                else _as_float(item["escalation"], 0.0),
                item["note"],
            )
            for group, item in self._longterm_tab.get_values().items()
        }
        return sheets, rules, longterm

    def write(self, path: Path) -> Path:
        """현재 입력을 기초율 워크북으로 쓴다."""
        sheets, rules, longterm = self.collect()
        return write_assumptions(path, sheets, rules, longterm)


    def close(self) -> None:
        """창을 닫는다. 호출한 쪽에 결과를 알린 뒤 정리한다."""
        if self._on_close is not None:
            self._on_close(self)
        self.destroy()


def _as_int(token: str, default: int) -> int:
    try:
        return int(float(str(token).strip()))
    except (TypeError, ValueError):
        return default


def _as_float(token: str, default: float) -> float:
    try:
        return float(str(token).strip())
    except (TypeError, ValueError):
        return default


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value).strip()


def _parse_cell(token: str) -> Any:
    """입력 문자열을 엑셀에 쓸 값으로. ``4.5%`` 는 0.045 로 저장한다."""
    token = token.strip()
    if not token:
        return None
    percent = token.endswith("%")
    body = token[:-1] if percent else token
    try:
        number = float(body.replace(",", ""))
    except ValueError:
        return token
    return number / 100.0 if percent else number


def open_editor(
    parent=None,
    *,
    job_groups: list[str] | None = None,
    on_close: Callable[[AssumptionsEditor], None] | None = None,
) -> AssumptionsEditor:
    """가정 입력 창을 띄운다."""
    editor = AssumptionsEditor(parent, job_groups=job_groups, on_close=on_close)
    if parent is not None:
        editor.transient(parent)
    editor.grab_set()
    return editor


def main() -> int:
    """가정 입력기만 단독 실행."""
    root = tk.Tk()
    root.withdraw()
    editor = AssumptionsEditor(root)
    editor.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    return 0
