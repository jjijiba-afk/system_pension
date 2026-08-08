"""산출 결과 엑셀 리포트 작성.

시트 구성::

    산출요약        기준·인원·DBO·원가·듀레이션 한 장 요약
    증감분석        기초 → 기말 확정급여채무 Roll-forward
    민감도분석      가정별 DBO 변동
    개인별산출      1인 1행. 검산과 원가배분에 쓴다
    장기급여        기타장기종업원급여 개인별 산출
    검증리포트      명부 검증 이슈 전체
    UpLoad_Jae      정규화된 재직자 업로드 명부
    UpLoad_Toi      정규화된 퇴직자 업로드 명부
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .errors import Severity
from .pipeline import PensionRun
from .upload import ACTIVE_UPLOAD_HEADERS, RETIRED_UPLOAD_HEADERS

__all__ = ["write_report"]

_MONEY = "#,##0"
_RATE = "0.000%"
_YEARS = "#,##0.00"
_DATE = "yyyy-mm-dd"


def _styles():
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    thin = Side(style="thin", color="BFBFBF")
    return {
        "title": Font(bold=True, size=14, color="1F3864"),
        "header": Font(bold=True, color="FFFFFF"),
        "header_fill": PatternFill("solid", fgColor="44546A"),
        "section": Font(bold=True, size=11, color="1F3864"),
        "section_fill": PatternFill("solid", fgColor="D9E2F3"),
        "total": Font(bold=True),
        "total_fill": PatternFill("solid", fgColor="FFF2CC"),
        "error_fill": PatternFill("solid", fgColor="FCE4E4"),
        "warn_fill": PatternFill("solid", fgColor="FFF6E0"),
        "info_fill": PatternFill("solid", fgColor="EEF3F8"),
        "center": Alignment(horizontal="center", vertical="center", wrap_text=True),
        "border": Border(left=thin, right=thin, top=thin, bottom=thin),
    }


def _write_table(
    ws,
    headers: Iterable[str],
    rows: Iterable[Iterable[Any]],
    *,
    start_row: int = 1,
    formats: dict[int, str] | None = None,
    widths: dict[int, int] | None = None,
) -> int:
    """머리글 + 데이터 표를 쓰고 다음 행 번호를 돌려준다."""
    st = _styles()
    headers = list(headers)
    for col, title in enumerate(headers, start=1):
        cell = ws.cell(start_row, col, title)
        cell.font = st["header"]
        cell.fill = st["header_fill"]
        cell.alignment = st["center"]
        cell.border = st["border"]

    row = start_row
    for row_offset, values in enumerate(rows, start=start_row + 1):
        row = row_offset
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row, col, value)
            cell.border = st["border"]
            if formats and col in formats:
                cell.number_format = formats[col]

    from openpyxl.utils import get_column_letter

    for col, title in enumerate(headers, start=1):
        letter = get_column_letter(col)
        if widths and col in widths:
            ws.column_dimensions[letter].width = widths[col]
        else:
            ws.column_dimensions[letter].width = max(12, min(34, len(str(title)) + 6))

    ws.freeze_panes = ws.cell(start_row + 1, 1).coordinate
    return row + 1


def _summary_sheet(wb, run: PensionRun) -> None:
    st = _styles()
    ws = wb.create_sheet("산출요약")
    ws.column_dimensions["A"].width = 4
    ws.column_dimensions["B"].width = 38
    ws.column_dimensions["C"].width = 22
    ws.column_dimensions["D"].width = 14

    ws["B2"] = "확정급여채무 산출 요약"
    ws["B2"].font = st["title"]

    row = 4

    def section(title: str) -> None:
        nonlocal row
        cell = ws.cell(row, 2, title)
        cell.font = st["section"]
        cell.fill = st["section_fill"]
        ws.cell(row, 3).fill = st["section_fill"]
        ws.cell(row, 4).fill = st["section_fill"]
        row += 1

    def line(label: str, value: Any, fmt: str = "", unit: str = "") -> None:
        nonlocal row
        ws.cell(row, 2, label)
        cell = ws.cell(row, 3, value)
        if fmt:
            cell.number_format = fmt
        if unit:
            ws.cell(row, 4, unit)
        row += 1

    section("0. 산출 기준")
    line("산출기준일", run.config.base_date, _DATE)
    curve = run.assumptions.discount
    if curve.flat is None:
        line("적용 할인율 (단일할인율)", run.valuation.single_discount_rate(), _RATE)
        ws.cell(row - 1, 4, "수익률곡선기법")
        line("  듀레이션 시점 현물이자율", curve.rate(run.valuation.duration), _RATE)
    else:
        line("적용 할인율", curve.level_rate, _RATE)
    line("가중평균 잔존만기(듀레이션)", run.valuation.duration, _YEARS, "년")
    line("기초율 가정", run.assumptions.label)
    row += 1

    section("1. 인원 현황")
    for label, value in run.headcount_summary().items():
        line(label, value, _MONEY, "명")
    row += 1

    section("2. 확정급여채무 (퇴직급여)")
    line("확정급여채무 (DBO)", run.valuation.dbo, _MONEY, "원")
    line("당기근무원가", run.valuation.service_cost, _MONEY, "원")
    line("이자원가 (차기 예상)", run.valuation.interest_cost, _MONEY, "원")
    line("퇴직급여추계액 (즉시퇴직 가정)", run.valuation.accrued_benefit, _MONEY, "원")
    row += 1

    if run.longterm is not None:
        section("3. 기타장기종업원급여")
        line("장기급여채무", run.longterm.dbo, _MONEY, "원")
        line("당기근무원가", run.longterm.service_cost, _MONEY, "원")
        line("이자원가 (차기 예상)", run.longterm.interest_cost, _MONEY, "원")
        line("산출대상 인원", run.longterm.headcount, _MONEY, "명")
        row += 1

    if run.plan_assets is not None:
        assets = run.plan_assets
        section("3-2. 사외적립자산 · 순확정급여부채")
        line("사외적립자산 공정가치", assets.closing_fair_value, _MONEY, "원")
        line("순확정급여부채", assets.net_liability, _MONEY, "원")
        line("적립비율", assets.funded_ratio, "0.0%", "")
        row += 1

    section("4. 당기 지급 실적")
    line("퇴직급여 지급액", run.benefits_paid, _MONEY, "원")
    line("정산 지급액 (중간정산·DC전환·전출)", run.settlements_paid, _MONEY, "원")
    line("사외적립자산 지급액", run.fund_assets_paid, _MONEY, "원")
    line("장기종업원급여 지급액", run.longterm_paid, _MONEY, "원")
    row += 1

    section("5. 직군별 확정급여채무")
    ws.cell(row, 2, "직군").font = st["total"]
    ws.cell(row, 3, "인원").font = st["total"]
    ws.cell(row, 4, "확정급여채무").font = st["total"]
    ws.cell(row, 5, "당기근무원가").font = st["total"]
    ws.column_dimensions["E"].width = 18
    row += 1
    for group, (count, dbo, sc) in run.valuation.by_job_group().items():
        ws.cell(row, 2, group)
        ws.cell(row, 3, count).number_format = _MONEY
        ws.cell(row, 4, dbo).number_format = _MONEY
        ws.cell(row, 5, sc).number_format = _MONEY
        row += 1

    for col in (2, 3, 4, 5):
        cell = ws.cell(row, col)
        cell.fill = st["total_fill"]
        cell.font = st["total"]
    ws.cell(row, 2, "합계")
    ws.cell(row, 3, run.valuation.headcount).number_format = _MONEY
    ws.cell(row, 4, run.valuation.dbo).number_format = _MONEY
    ws.cell(row, 5, run.valuation.service_cost).number_format = _MONEY

    errors = len(run.issues.errors)
    warnings = len(run.issues.warnings)
    row += 2
    verdict = "이상 없음" if errors == 0 else f"오류 {errors}건 — 명부 확인 필요"
    ws.cell(row, 2, "명부 검증 결과").font = st["section"]
    cell = ws.cell(row, 3, verdict)
    cell.fill = st["error_fill"] if errors else st["total_fill"]
    ws.cell(row, 4, f"경고 {warnings}건")


def _rollforward_sheet(wb, run: PensionRun) -> None:
    if run.rollforward is None:
        return
    st = _styles()
    ws = wb.create_sheet("증감분석")
    ws["B2"] = "확정급여채무 증감분석 (Roll-forward)"
    ws["B2"].font = st["title"]
    ws.column_dimensions["B"].width = 34
    ws.column_dimensions["C"].width = 20

    row = 4
    rows = run.rollforward.as_rows()
    for label, amount in rows:
        is_total = label.startswith(("기초", "기말"))
        cell_label = ws.cell(row, 2, label)
        cell_value = ws.cell(row, 3, amount)
        cell_value.number_format = _MONEY
        if is_total:
            cell_label.font = st["total"]
            cell_value.font = st["total"]
            cell_label.fill = st["total_fill"]
            cell_value.fill = st["total_fill"]
        row += 1

    row += 1
    ws.cell(row, 2, "보험수리적손익 합계").font = st["section"]
    ws.cell(row, 3, run.rollforward.actuarial_gain_loss).number_format = _MONEY
    row += 2
    ws.cell(row, 2, "※ 양수는 채무 증가(보험수리적손실), 음수는 채무 감소(이익)를 뜻합니다.")
    row += 1
    ws.cell(
        row, 2,
        "※ 과거근무원가·정산손익은 당기손익, 보험수리적손익은 기타포괄손익입니다.",
    )

    # ── 사외적립자산 ────────────────────────────────────────────
    assets = run.plan_assets
    if assets is None:
        return

    row += 3
    ws.cell(row, 2, "사외적립자산 증감").font = st["title"]
    row += 1
    for label, amount in assets.as_rows():
        is_total = label.startswith(("기초", "기말"))
        cell_label = ws.cell(row, 2, label)
        cell_value = ws.cell(row, 3, amount)
        cell_value.number_format = _MONEY
        if is_total:
            cell_label.font = st["total"]
            cell_value.font = st["total"]
            cell_label.fill = st["total_fill"]
            cell_value.fill = st["total_fill"]
        row += 1

    row += 2
    ws.cell(row, 2, "순확정급여부채").font = st["title"]
    row += 1
    for label, amount in assets.net_rows():
        cell_label = ws.cell(row, 2, label)
        cell_value = ws.cell(row, 3, amount)
        cell_value.number_format = _MONEY
        if label.startswith("순"):
            cell_label.font = st["total"]
            cell_value.font = st["total"]
            cell_label.fill = st["total_fill"]
            cell_value.fill = st["total_fill"]
        row += 1

    ws.cell(row, 2, "적립비율")
    ws.cell(row, 3, assets.funded_ratio).number_format = "0.0%"
    row += 2
    ws.cell(row, 2, "※ 이자수익은 채무와 **같은 할인율** 로 계산합니다(문단 125). "
                    "자산의 실제 수익률이 아닙니다.")
    row += 1
    ws.cell(row, 2, "※ 자산 재측정손익은 기말 공정가치에 맞춘 나머지이며 "
                    "기타포괄손익입니다.")
    if assets.overfunded:
        row += 1
        ws.cell(
            row, 2,
            "※ 자산이 채무를 넘었습니다(초과적립). 자산인식상한(문단 64)을 "
            "따로 검토하십시오 — 이 프로그램은 상한을 적용하지 않습니다.",
        ).font = st["section"]


def _sensitivity_sheet(wb, run: PensionRun) -> None:
    if run.sensitivity is None:
        return
    st = _styles()
    ws = wb.create_sheet("민감도분석")
    ws["B2"] = "확정급여채무 민감도분석"
    ws["B2"].font = st["title"]

    headers = ["구분", "확정급여채무", "증감액", "증감률"]
    rows = [["기준 (당기 가정)", run.sensitivity.base_dbo, 0, 0]]
    rows += [[c.name, c.dbo, c.change, c.change_ratio] for c in run.sensitivity.cases]

    _write_table(
        ws, headers, rows, start_row=4,
        formats={2: _MONEY, 3: _MONEY, 4: "0.00%"},
        widths={1: 26, 2: 20, 3: 18, 4: 12},
    )
    ws.cell(len(rows) + 7, 1,
            "※ 각 항목은 해당 가정만 변동시키고 나머지 가정은 고정한 결과입니다 "
            "(K-IFRS 1019호 문단 145).")


def _member_sheet(wb, run: PensionRun) -> None:
    ws = wb.create_sheet("개인별산출")
    headers = [
        "사번", "성명", "직군", "성별", "원가코드", "제도구분", "연령", "근속연수",
        "정년연령", "투영연수", "30일 평균임금", "퇴직급여추계액", "확정급여채무",
        "당기근무원가", "이자원가", "듀레이션", "지급률 규정", "제외사유",
    ]
    rows = [
        [
            m.employee_id, m.name, m.job_group, m.gender, m.cost_code, m.plan,
            m.age, m.past_service, m.retirement_age, m.projection_years,
            m.monthly_wage, m.accrued_benefit, m.dbo, m.service_cost,
            m.interest_cost, m.duration, m.benefit_rule, m.excluded_reason,
        ]
        for m in run.valuation.members
    ]
    _write_table(
        ws, headers, rows,
        formats={
            8: _YEARS, 11: _MONEY, 12: _MONEY, 13: _MONEY,
            14: _MONEY, 15: _MONEY, 16: _YEARS,
        },
        widths={1: 16, 2: 12, 3: 14, 5: 14, 17: 14, 18: 34},
    )


def _longterm_sheet(wb, run: PensionRun) -> None:
    if run.longterm is None:
        return
    ws = wb.create_sheet("장기급여")
    headers = [
        "사번", "성명", "직군", "연령", "근속연수", "일 기본급",
        "다음 지급 근속", "남은 지급 횟수", "장기급여채무", "당기근무원가",
        "이자원가", "제외사유",
    ]
    rows = [
        [
            m.employee_id, m.name, m.job_group, m.age, m.past_service,
            m.daily_base_pay, m.next_milestone or "", m.milestone_count,
            m.dbo, m.service_cost, m.interest_cost, m.excluded_reason,
        ]
        for m in run.longterm.members
    ]
    _write_table(
        ws, headers, rows,
        formats={5: _YEARS, 6: _MONEY, 9: _MONEY, 10: _MONEY, 11: _MONEY},
        widths={1: 16, 12: 36},
    )


def _issues_sheet(wb, run: PensionRun) -> None:
    st = _styles()
    ws = wb.create_sheet("검증리포트")
    headers = ["심각도", "시트", "행", "열", "순번", "사번", "코드", "내용", "값"]
    rows = [
        [
            issue.severity.value, issue.sheet, issue.row, issue.column,
            issue.seq, issue.employee_id, issue.code, issue.message, issue.value,
        ]
        for issue in run.issues
    ]
    if not rows:
        rows = [["-", "-", None, "-", None, "-", "-", "검증 이슈가 없습니다", "-"]]

    next_row = _write_table(ws, headers, rows, widths={8: 60, 9: 20})

    fills = {
        Severity.ERROR: st["error_fill"],
        Severity.WARNING: st["warn_fill"],
        Severity.INFO: st["info_fill"],
    }
    for offset, issue in enumerate(run.issues, start=2):
        fill = fills[issue.severity]
        for col in range(1, len(headers) + 1):
            ws.cell(offset, col).fill = fill

    ws.cell(
        next_row + 1, 1,
        f"오류 {len(run.issues.errors)}건 / 경고 {len(run.issues.warnings)}건 "
        f"/ 안내 {len(run.issues.notices)}건",
    )
    ws.auto_filter.ref = f"A1:I{max(2, len(rows) + 1)}"


def _upload_sheet(wb, title: str, headers: tuple[str, ...], rows: list[list[Any]]) -> None:
    ws = wb.create_sheet(title)
    if "Jae" in title:
        # 생년월일·입사일자·중간정산일 / 전입일 / 추가지급 기준일
        date_cols = {7, 8, 9, 20, 33}
        # 평균임금·명퇴임금·추계액·일기본급 / 중간정산금액 / 장기급여·전입액 / 추가지급 기본급
        money_cols = {10, 11, 12, 13, 17, 21, 22, 34}
    else:
        # 생년월일·입사일·퇴사일·사외자산 지급일 / 총지급 ~ 전출지급
        date_cols = {7, 8, 9, 10}
        money_cols = {13, 14, 15, 16, 17, 18}

    formats = dict.fromkeys(date_cols, _DATE)
    formats.update(dict.fromkeys(money_cols, _MONEY))
    _write_table(ws, headers, rows or [[""] * len(headers)], formats=formats)


def _cashflow_sheet(wb, run: PensionRun) -> None:
    """기대 급여 현금흐름과 단일할인율·듀레이션의 검산.

    단일할인율은 "곡선으로 할인한 채무와 같은 현재가치를 내는 이자율" 인데,
    그 뒤의 현금흐름이 보이지 않으면 검산할 방법이 없다. 감사인이 이 시트의
    두 열(기대지급액, 현가계수)만으로 채무·단일할인율·듀레이션을 전부 재계산할
    수 있어야 근거자료가 된다.
    """
    flows = run.valuation.cash_flows()
    if not flows:
        return

    st = _styles()
    ws = wb.create_sheet("기대현금흐름")
    ws["B2"] = "기대 급여 현금흐름 (퇴직급여 확정급여채무)"
    ws["B2"].font = st["title"]
    ws.cell(3, 2, "탈퇴는 연 중앙(t-0.5)에, 정년퇴직은 연말에 일어난 것으로 봅니다. "
                  "금액은 탈퇴확률과 근속귀속(PUC)을 반영한 할인 전 기대지급액입니다."
            ).font = st["section"]

    discount = run.assumptions.discount
    single = run.valuation.single_discount_rate()

    headers = ["지급시점(년)", "기대 급여지급액", "현가계수(곡선)", "현재가치",
               f"단일할인율 {single:.4%} 현가"]
    rows = []
    total_pv = 0.0
    total_single = 0.0
    weighted = 0.0
    for timing, amount in flows.items():
        factor = discount.discount_factor(timing)
        pv = amount * factor
        pv_single = amount / (1.0 + single) ** timing if single else pv
        total_pv += pv
        total_single += pv_single
        weighted += pv * timing
        rows.append([timing, amount, factor, pv, pv_single])

    next_row = _write_table(
        ws, headers, rows,
        start_row=5,
        formats={1: _YEARS, 2: _MONEY, 3: "0.000000", 4: _MONEY, 5: _MONEY},
        widths={1: 14, 2: 20, 3: 16, 4: 20, 5: 22},
    )

    def line(label: str, value, fmt: str = _MONEY) -> None:
        nonlocal next_row
        ws.cell(next_row, 2, label).font = st["total"]
        cell = ws.cell(next_row, 4, value)
        cell.number_format = fmt
        cell.font = st["total"]
        cell.fill = st["total_fill"]
        next_row += 1

    next_row += 1
    dbo = run.valuation.dbo
    line("현재가치 합계", total_pv)
    line("확정급여채무 (검산 대상)", dbo)
    line("차이", total_pv - dbo)
    line("단일할인율 재할인 합계", total_single)
    line("단일할인율", single, _RATE)
    line("듀레이션 (Σ현가×시점 ÷ Σ현가)", weighted / total_pv if total_pv else 0.0, _YEARS)


def _assumption_sheet(wb, run: PensionRun) -> None:
    """산출에 실제로 적용한 가정 일체의 사본.

    결과 파일은 담당자 → 회사 → 감사인 사이를 메일로 돌아다니는 동안 기초율
    파일과 분리되기 마련이다. "이 숫자가 어떤 가정에서 나왔나" 에 결과 파일
    혼자 답할 수 있어야 하므로, 적용 시점의 가정을 통째로 남긴다.
    """
    st = _styles()
    ws = wb.create_sheet("적용가정")
    ws["B2"] = "적용 가정 (산출에 실제 사용한 값)"
    ws["B2"].font = st["title"]
    ws.column_dimensions["A"].width = 4

    row = 4

    def section(title: str) -> None:
        nonlocal row
        cell = ws.cell(row, 2, title)
        cell.font = st["section"]
        cell.fill = st["section_fill"]
        row += 1

    def table(headers: list, data: list[list]) -> None:
        nonlocal row
        for col, title in enumerate(headers, start=2):
            cell = ws.cell(row, col, title)
            cell.font = st["header"]
            cell.fill = st["header_fill"]
            cell.border = st["border"]
        row += 1
        for values in data:
            for col, value in enumerate(values, start=2):
                ws.cell(row, col, value).border = st["border"]
            row += 1
        row += 1

    def curve_table(title: str, key_label: str, curves: dict) -> None:
        """규정명별 곡선 묶음을 키(연령/근속) × 규정 열의 한 표로."""
        names = [n for n, c in curves.items() if c]
        if not names:
            return
        keys = sorted({k for n in names for k in curves[n].points})
        section(title)
        table(
            [key_label, *names],
            [[key, *[curves[n].points.get(key, "") for n in names]] for key in keys],
        )

    a = run.assumptions

    section("할인율")
    if a.discount.flat is not None:
        table(["구분", "값"], [["단일 할인율", a.discount.flat]])
    else:
        table(["연차", "현물이자율"], [[k, v] for k, v in sorted(a.discount.spot.points.items())])

    section("임금상승률 (Base-up)")
    table(["연차", "상승률"], [[k, v] for k, v in sorted(a.salary.base_up.points.items())])

    curve_table(f"승급률 (기준: {a.salary.promotion.basis})",
                a.salary.promotion.basis, a.salary.promotion.curves)
    curve_table(f"퇴직률 (기준: {a.withdrawal.basis})", a.withdrawal.basis, a.withdrawal.curves)
    curve_table("사망률 qx", "연령", {"남자": a.mortality.male, "여자": a.mortality.female})
    curve_table("지급률", "근속연수", a.severance_benefit.curves)

    modes = [
        [name, a.severance_benefit.mode(name),
         getattr(a.severance_benefit.formulas.get(name), "source", "")]
        for name in a.severance_benefit.curves
    ]
    if modes:
        section("지급률 방식")
        table(["규정명", "방식", "수식"], modes)

    rules = run.config.job_group_rules
    if rules:
        section("직군별 규정")
        table(
            ["명부직군", "임직원구분", "변환직군", "정년", "임원정년", "가산연령",
             "가입자격(년)", "근속산정", "단수처리", "산출제외",
             "Base-up", "승급률", "퇴직률", "사망률"],
            [
                [r.source_name, r.employee_type_filter, r.mapped_name,
                 r.severance_nra, r.executive_nra or "", r.over_nra_add_age,
                 r.min_service_years, r.service_basis, r.service_fraction,
                 "제외" if r.excluded else "",
                 *["반영" if flag else "미반영" for flag in
                   (r.apply_base_up, r.apply_promotion, r.apply_withdrawal, r.apply_mortality)]]
                for r in rules
            ],
        )

    ws.cell(row, 2, f"기초율 가정: {a.label}").font = st["section"]
    for col in range(2, 16):
        letter = ws.cell(1, col).column_letter
        ws.column_dimensions[letter].width = 13


def write_report(run: PensionRun, path: str | Path) -> Path:
    """산출 결과를 엑셀 한 권으로 저장한다."""
    import openpyxl

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.Workbook()
    del wb["Sheet"]

    _summary_sheet(wb, run)
    _rollforward_sheet(wb, run)
    _sensitivity_sheet(wb, run)
    _cashflow_sheet(wb, run)
    _assumption_sheet(wb, run)
    _member_sheet(wb, run)
    _longterm_sheet(wb, run)
    _issues_sheet(wb, run)
    _upload_sheet(wb, "UpLoad_Jae", ACTIVE_UPLOAD_HEADERS, run.active_upload)
    _upload_sheet(wb, "UpLoad_Toi", RETIRED_UPLOAD_HEADERS, run.retired_upload)

    wb.save(path)
    return path
