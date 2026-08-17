"""산출 결과 엑셀 리포트 작성.

시트 구성::

    산출요약        기준·인원·DBO·원가·듀레이션 한 장 요약
    증감분석        기초 → 기말 확정급여채무 Roll-forward
    민감도분석      가정별 DBO 변동
    개인별산출      1인 1행. 검산과 원가배분에 쓴다
    장기급여        기타장기종업원급여 개인별 산출
    검증리포트      명부 검증 이슈 전체
    재직자명부      산출이 읽은 형태로 정리한 재직자 명부
    퇴직자명부      산출이 읽은 형태로 정리한 퇴직자 명부
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import datetime as _dt
from typing import Any, Final

from .errors import Severity
from .pipeline import PensionRun
from .upload import ACTIVE_UPLOAD_HEADERS, RETIRED_UPLOAD_HEADERS
from .workbook import save_workbook

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
        "wrap": Alignment(vertical="top", wrap_text=True),
        "note": Font(color="5B6478"),
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

    return row + 1



#: 결과 파일에 무엇이 어디 있는지. (시트, 무엇이 있나, 언제 보나)
SHEET_GUIDE: Final = (
    ("검증", "명부에서 잡힌 오류·경고",
     "가장 먼저. 오류가 있으면 아래 숫자를 믿을 수 없습니다"),
    ("요약", "채무·근무원가·순부채 등 핵심 숫자",
     "회계팀에 넘길 숫자는 여기서 가져갑니다"),
    ("채무 증감", "기초에서 기말까지 무엇이 채무를 얼마나 바꿨나",
     "전기 대비 왜 늘었는지 설명할 때"),
    ("민감도", "할인율·임금상승률을 흔들었을 때의 채무",
     "주석 공시(문단 145)에 그대로 실립니다"),
    ("현금흐름", "앞으로 나갈 급여의 시점별 기대액",
     "감사인이 채무를 재계산해 볼 때"),
    ("적용가정", "이 산출에 실제로 쓴 기초율 전부",
     "다른 회차와 비교하거나 근거를 물었을 때"),
    ("개인별 결과", "한 사람 한 줄", "특정 사번의 금액을 확인할 때"),
    ("장기급여 개인별", "장기급여 대상자 한 사람 한 줄",
     "근속포상·장기근속휴가가 있는 회사만"),
    ("재직자명부", "받은 명부를 산출이 읽은 형태로 정리한 것",
     "무엇을 어떻게 읽었는지 되짚을 때"),
    ("퇴직자명부", "위와 같음 (퇴직자)", ""),
)


def _guide_sheet(wb, run: PensionRun) -> None:
    """맨 앞에 두는 안내.

    파일을 열면 곧바로 표 한가운데로 떨어져서, 무엇을 보고 있는 것인지도
    어디를 봐야 하는지도 알 수 없었다. 핵심 숫자와 검증 결과를 먼저 보이고,
    시트마다 언제 쓰는 것인지 한 줄씩 적는다.
    """
    st = _styles()
    ws = wb.create_sheet("이 파일 보는 법")
    for column, width in (("A", 3), ("B", 24), ("C", 28), ("D", 52)):
        ws.column_dimensions[column].width = width

    ws["B2"] = "확정급여채무 산출결과"
    ws["B2"].font = st["title"]
    ws["B3"] = (f"산출기준일 {run.config.base_date}   ·   "
                f"대상 {run.valuation.headcount:,}명   ·   "
                f"작성 {_dt.date.today()}")
    ws["B3"].font = st["note"]

    row = 5
    ws.cell(row, 2, "핵심 숫자").font = st["section"]
    row += 1
    headline: list[tuple[str, float]] = [
        ("확정급여채무", run.valuation.dbo),
        ("당기근무원가", run.valuation.service_cost),
        ("차기 이자원가 (예상)", run.valuation.interest_cost),
        ("퇴직금 추계액", run.valuation.accrued_benefit),
    ]
    if run.plan_assets is not None:
        headline += [("사외적립자산 공정가치", run.plan_assets.closing_fair_value),
                     ("순확정급여부채", run.plan_assets.net_liability)]
    if run.longterm is not None:
        headline.append(("장기급여채무", run.longterm.dbo))
    for label, amount in headline:
        ws.cell(row, 2, label).font = st["total"]
        cell = ws.cell(row, 3, amount)
        cell.number_format = _MONEY
        cell.font = st["total"]
        cell.fill = st["total_fill"]
        ws.cell(row, 4, "원").font = st["note"]
        row += 1

    errors = len(run.issues.errors)
    row += 1
    ws.cell(row, 2, "명부 검증").font = st["total"]
    verdict = ws.cell(row, 3, "이상 없음" if errors == 0 else f"오류 {errors}건")
    verdict.font = st["total"]
    verdict.fill = st["error_fill"] if errors else st["total_fill"]
    ws.cell(row, 4, f"경고 {len(run.issues.warnings)}건 — 자세한 것은 [검증] 시트"
            ).font = st["note"]

    row += 2
    ws.cell(row, 2, "시트 안내").font = st["section"]
    row += 1
    for column, title in ((2, "시트"), (3, "무엇이 있나"), (4, "언제 보나")):
        cell = ws.cell(row, column, title)
        cell.font = st["header"]
        cell.fill = st["header_fill"]
        cell.border = st["border"]
    row += 1
    for name, what, when in SHEET_GUIDE:
        ws.cell(row, 2, name).font = st["total"]
        ws.cell(row, 3, what)
        ws.cell(row, 4, when).font = st["note"]
        for column in (2, 3, 4):
            ws.cell(row, column).border = st["border"]
            ws.cell(row, column).alignment = st["wrap"]
        row += 1


def _summary_sheet(wb, run: PensionRun) -> None:
    st = _styles()
    ws = wb.create_sheet("요약")
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

    section("산출 기준")
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

    section("인원 현황")
    for label, value in run.headcount_summary().items():
        line(label, value, _MONEY, "명")
    row += 1

    section("확정급여채무 (퇴직급여)")
    line("확정급여채무 (DBO)", run.valuation.dbo, _MONEY, "원")
    line("당기근무원가", run.valuation.service_cost, _MONEY, "원")
    line("이자원가 (차기 예상)", run.valuation.interest_cost, _MONEY, "원")
    line("퇴직급여추계액 (즉시퇴직 가정)", run.valuation.accrued_benefit, _MONEY, "원")
    row += 1

    causes = run.valuation.by_cause()
    if causes:
        # 사유마다 지급률이 다른 규정에서는 합계만으로 검산이 안 된다. 어느
        # 사유가 채무를 얼마나 만들었는지 갈라 두어야 지급률 한 칸이 틀린 것을
        # 알아챈다. 합은 위 확정급여채무와 원 단위까지 같다.
        section("퇴직사유별 (급부별) 금액")
        for column, title in ((2, "퇴직사유"), (3, "확정급여채무"),
                              (4, "당기근무원가"), (5, "비중")):
            cell = ws.cell(row, column, title)
            cell.font = st["header"]
            cell.fill = st["header_fill"]
            cell.border = st["border"]
        row += 1
        first_cause = row
        for name, share in causes.items():
            ws.cell(row, 2, name)
            ws.cell(row, 3, share["dbo"]).number_format = _MONEY
            ws.cell(row, 4, share["service_cost"]).number_format = _MONEY
            # 합계는 수식으로 둔다. 박아 넣은 숫자는 감사인이 눌러 봐도 무엇을
            # 더한 것인지 알 수 없다.
            ws.cell(row, 5, f"=C{row}/$C${first_cause + len(causes)}"
                    ).number_format = "0.0%"
            for column in range(2, 6):
                ws.cell(row, column).border = st["border"]
            row += 1
        ws.cell(row, 2, "합계 (= 확정급여채무)").font = st["total"]
        ws.cell(row, 3, f"=SUM(C{first_cause}:C{row - 1})").number_format = _MONEY
        ws.cell(row, 4, f"=SUM(D{first_cause}:D{row - 1})").number_format = _MONEY
        for column in range(2, 6):
            ws.cell(row, column).border = st["border"]
            ws.cell(row, column).font = st["total"]
            ws.cell(row, column).fill = st["total_fill"]
        row += 2

    if run.longterm is not None:
        section("기타장기종업원급여")
        line("장기급여채무", run.longterm.dbo, _MONEY, "원")
        line("당기근무원가", run.longterm.service_cost, _MONEY, "원")
        line("이자원가 (차기 예상)", run.longterm.interest_cost, _MONEY, "원")
        line("산출대상 인원", run.longterm.headcount, _MONEY, "명")
        row += 1

    if run.plan_assets is not None:
        assets = run.plan_assets
        section("사외적립자산 · 순확정급여부채")
        line("사외적립자산 공정가치", assets.closing_fair_value, _MONEY, "원")
        line("순확정급여부채", assets.net_liability, _MONEY, "원")
        line("적립비율", assets.funded_ratio, "0.0%", "")
        row += 1

    section("당기 지급 실적")
    line("퇴직급여 지급액", run.benefits_paid, _MONEY, "원")
    line("정산 지급액 (중간정산·DC전환·전출)", run.settlements_paid, _MONEY, "원")
    line("사외적립자산 지급액", run.fund_assets_paid, _MONEY, "원")
    line("장기종업원급여 지급액", run.longterm_paid, _MONEY, "원")
    row += 1

    section("직군별 확정급여채무")
    ws.cell(row, 2, "직군").font = st["total"]
    ws.cell(row, 3, "인원").font = st["total"]
    ws.cell(row, 4, "확정급여채무").font = st["total"]
    ws.cell(row, 5, "당기근무원가").font = st["total"]
    ws.column_dimensions["E"].width = 18
    row += 1
    first_group = row
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
    for column, letter in ((3, "C"), (4, "D"), (5, "E")):
        ws.cell(row, column,
                f"=SUM({letter}{first_group}:{letter}{row - 1})"
                ).number_format = _MONEY

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
    ws = wb.create_sheet("채무 증감")
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
    ws = wb.create_sheet("민감도")
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
    ws = wb.create_sheet("개인별 결과")
    headers = [
        "사번", "성명", "직군", "성별", "원가코드", "제도구분", "연령", "근속연수",
        "정년연령", "남은 근무연수", "30일 평균임금", "퇴직금 추계액", "확정급여채무",
        "당기근무원가", "차기 이자원가", "잔존만기(년)", "적용 지급률 규정",
        "누진보전 근속연수", "누진보전 지급률", "산출 제외사유",
    ]
    rows = [
        [
            m.employee_id, m.name, m.job_group, m.gender, m.cost_code, m.plan,
            m.age, m.past_service, m.retirement_age, m.projection_years,
            m.monthly_wage, m.accrued_benefit, m.dbo, m.service_cost,
            m.interest_cost, m.duration, m.benefit_rule,
            m.progressive_service or "", m.progressive_rate or "",
            m.excluded_reason,
        ]
        for m in run.valuation.members
    ]
    _write_table(
        ws, headers, rows,
        formats={
            8: _YEARS, 11: _MONEY, 12: _MONEY, 13: _MONEY,
            14: _MONEY, 15: _MONEY, 16: _YEARS, 18: _YEARS,
        },
        widths={1: 16, 2: 12, 3: 14, 5: 14, 17: 14, 20: 34},
    )


def _longterm_sheet(wb, run: PensionRun) -> None:
    if run.longterm is None:
        return
    ws = wb.create_sheet("장기급여 개인별")
    headers = [
        "사번", "성명", "직군", "연령", "근속연수", "1일 통상임금",
        "다음 지급 시점(근속)", "남은 지급 횟수", "장기급여채무", "당기근무원가",
        "차기 이자원가", "산출 제외사유",
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
    ws = wb.create_sheet("검증")
    headers = ["심각도", "어디", "행", "열", "순번", "사번", "내용", "값", "코드"]
    rows = [
        [
            issue.severity.value, issue.sheet, issue.row, issue.column,
            issue.seq, issue.employee_id, issue.message, issue.value, issue.code,
        ]
        for issue in run.issues
    ]
    if not rows:
        rows = [["-", "-", None, "-", None, "-", "검증 이슈가 없습니다", "-", "-"]]

    next_row = _write_table(ws, headers, rows, widths={7: 60, 8: 20})

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


#: 명부 시트에서 날짜로 보일 열.
_UPLOAD_DATES: frozenset = frozenset({
    "생년월일", "입사일자", "중간정산일", "전입일", "추가지급 기준일",
    "퇴사일", "사외자산 지급일",
})

#: 명부 시트에서 세 자리로 끊어 보일 열. ``5000000`` 과 ``50000000`` 은 눈으로
#: 구분되지 않는데, 0 하나 차이가 그 사람의 채무를 열 배로 만든다.
_UPLOAD_MONEY: frozenset = frozenset({
    "30일 평균임금", "명예퇴직 기준임금", "추계액", "1일 통상임금",
    "중간정산 지급금액", "장기급여 기지급액", "전입 인수액", "추가지급 기본급",
    "퇴직급여 총지급액", "사외자산 지급액", "국민연금 전환금",
    "장기급여 지급액", "퇴직위로금 등", "전출 지급액",
})


def _upload_sheet(wb, title: str, headers: tuple[str, ...], rows: list[list[Any]]) -> None:
    """산출이 읽은 형태로 정리한 명부 한 장.

    서식은 **머리글 이름으로** 고른다. 예전에는 열 번호를 적어 두었는데, 열이
    하나 끼어드는 순간 그 오른쪽 서식이 통째로 한 칸씩 밀린다. 실제로 밀려
    있었다 — [임금피크 연령] 57 이 날짜로, [DB비율] 0.99 가 정수로 찍히고,
    [전입일] 은 날짜 대신 숫자로 나왔다. 정작 금액인 [중간정산 지급금액]·
    [전입 인수액] 은 서식 없이 붙어 자릿수를 셀 수 없었다.

    이름으로 고르면 열이 어디로 옮겨 가도 따라간다.
    """
    ws = wb.create_sheet(title)
    formats = {}
    for index, head in enumerate(headers, start=1):
        if head in _UPLOAD_DATES:
            formats[index] = _DATE
        elif head in _UPLOAD_MONEY:
            formats[index] = _MONEY
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
    ws = wb.create_sheet("현금흐름")
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

    if a.longterm_rules:
        section("장기급여 지급 항목")
        table(
            ["규정명", "항목(지급률 열)", "지급유형", "현물 상승률",
             "지급시점", "반복 주기(년)", "누적", "지급일", "환산 근거"],
            [
                [name, entry.item, entry.kind, entry.escalation or "",
                 entry.timing, entry.every_years or "",
                 "Y" if entry.accumulate else "", entry.anniversary, entry.note]
                for name, entries in sorted(a.longterm_rules.items())
                for entry in entries
            ],
        )

    if not a.exit_causes.is_empty():
        section("퇴직사유별 지급 차등")
        table(
            ["지급률 규정", "퇴직사유", "대체 지급률 규정", "가산 규정",
             "가산액(원)", "근속 하한(년)", "가산 귀속", "명부 추가지급 배수"],
            [
                [rule, cause, entry.benefit_rule, entry.extra_rule,
                 entry.extra_amount or "", entry.min_service or "",
                 entry.attribution_basis(cause), entry.roster_extra_multiple or ""]
                for (rule, cause), entry in sorted(a.exit_causes.rules.items())
            ],
        )

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

    # 읽는 순서대로 둔다. 검증이 맨 앞인 것은, 오류가 있으면 그 뒤의 숫자를
    # 쓸 수 없기 때문이다 — 뒤에 두면 다 보고 나서야 알게 된다.
    _guide_sheet(wb, run)
    _issues_sheet(wb, run)
    _summary_sheet(wb, run)
    _rollforward_sheet(wb, run)
    _sensitivity_sheet(wb, run)
    _cashflow_sheet(wb, run)
    _assumption_sheet(wb, run)
    _member_sheet(wb, run)
    _longterm_sheet(wb, run)
    _upload_sheet(wb, "재직자명부", ACTIVE_UPLOAD_HEADERS, run.active_upload)
    _upload_sheet(wb, "퇴직자명부", RETIRED_UPLOAD_HEADERS, run.retired_upload)

    save_workbook(wb, path)
    return path
