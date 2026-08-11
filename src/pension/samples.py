"""프로그램과 함께 배포하는 기본 파일들.

실행 파일만 받아서는 아무것도 못 한다. 명부와 기초율 두 가지가 있어야 하는데,
빈 화면 앞에서 첫 칸을 무엇으로 채울지 알아내는 것이 실제로 가장 오래 걸린다.
그래서 **그대로 열어 고치면 되는 한 벌** 을 같이 넣는다.

세 가지를 만든다.

``명부_기본.xlsx``
    난수로 만든 가상 명부(재직 290명·퇴직 28명). 예시 두 줄로는 볼 수 없는
    형태 — DC 혼재·임원의 직군 표기·중간정산 — 가 들어 있다. **실존 인물이
    아니다**: 배포물에 실제 개인정보를 담지 않기 위해서다.

``명부_양식.xlsx``
    ``Input`` · ``재직자명부`` · ``퇴직자명부`` 세 시트. 머리글은 프로그램이
    인식하는 표준 표기 그대로이고, 작성 예시가 두 줄 들어 있다.

``기초율_기본값.xlsx``
    표준률 원표를 그대로 옮긴다. 사망률은 재직자 기준이라 그대로 써도 되고,
    퇴직률·승급률은 표준률이라 회사 경험률이 있으면 그쪽이 우선이다. 이 둘은
    사업장 규모(300인 미만/이상)로 갈리므로 ``size`` 로 고른다
    (:mod:`pension.standard_rates` 에 근거를 적어 두었다).

``기초율_빈양식.xlsx``
    처음부터 회사 값으로 채우고 싶을 때. 시트 구조와 머리글만 있다.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Final

from .assumptions import (
    BENEFIT_RULE_SHEET,
    BENEFIT_SHEET,
    DISCOUNT_SHEET,
    LONGTERM_RULE_SHEET,
    LONGTERM_SHEET,
    LT_VACATION,
    MORTALITY_SHEET,
    PROMOTION_SHEET,
    SALARY_SHEET,
    STATUTORY_MODE,
    WITHDRAWAL_SHEET,
    write_template,
)
from .config import PAYOUT_SHEET
from .jobgroup import DEFAULT_GROUPS
from .readers import (
    ACTIVE_COLUMNS,
    ACTIVE_FIRST_ROW,
    ACTIVE_SHEET,
    RETIRED_COLUMNS,
    RETIRED_FIRST_ROW,
    RETIRED_SHEET,
)
from .standard_rates import (
    DEFAULT_SIZE,
    DISCOUNT_RATE,
    MORTALITY_SOURCE,
    SALARY_BASE_UP,
    STANDARD_YEAR,
    mortality_table,
    normalize_size,
    promotion_table,
    withdrawal_table,
)
from .yieldcurve import INVESTMENT_GRADES

__all__ = [
    "CURVE_TEMPLATE",
    "CURVE_TENORS",
    "RAW_SHEETS",
    "ROSTER_DEFAULT",
    "ROSTER_TEMPLATE",
    "STANDARD_ASSUMPTIONS",
    "STANDARD_TABLE_FILE",
    "TEMPLATE_ASSUMPTIONS",
    "write_curve_template",
    "write_default_roster",
    "write_roster_template",
    "write_sample_pack",
    "write_standard_assumptions",
    "write_standard_table",
]

ROSTER_TEMPLATE: Final = "명부_양식.xlsx"
ROSTER_DEFAULT: Final = "명부_기본.xlsx"
STANDARD_ASSUMPTIONS: Final = "기초율_기본값.xlsx"
TEMPLATE_ASSUMPTIONS: Final = "기초율_빈양식.xlsx"
CURVE_TEMPLATE: Final = "금리표_양식.xlsx"
STANDARD_TABLE_FILE: Final = "표준률_원표.xlsx"



def _style():
    from openpyxl.styles import Alignment, Font, PatternFill

    return {
        "head_font": Font(bold=True, color="FFFFFF", size=9),
        "head_fill": PatternFill("solid", fgColor="44546A"),
        "note_font": Font(italic=True, color="808080"),
        "title_font": Font(bold=True, size=12),
        "sample_font": Font(color="9C6500"),
        "sample_fill": PatternFill("solid", fgColor="FFF2CC"),
        "center": Alignment(horizontal="center", vertical="center", wrap_text=True),
    }


def write_roster_template(path: str | Path) -> Path:
    """명부 양식을 만든다.

    머리글은 :mod:`pension.layout` 이 인식하는 표준 표기를, 열 위치는 기존
    통합문서와 같은 자리를 쓴다. 그래서 이 파일을 그대로 채워 넣으면 열을
    옮기지 않아도 읽힌다. 열을 끼워 넣어도 머리글로 찾으므로 괜찮다.
    """
    import openpyxl

    from .layout import ACTIVE_HEADER_ALIASES, RETIRED_HEADER_ALIASES

    path = Path(path)
    st = _style()
    wb = openpyxl.Workbook()

    def sheet(name: str, columns: dict, aliases: dict, first_row: int, samples: list[dict]):
        ws = wb.create_sheet(name)
        header_row = first_row - 2

        ws.cell(1, 1, f"{name} — 노란 줄은 작성 예시입니다. 지우고 실제 자료를 넣으세요.")
        ws.cell(1, 1).font = st["note_font"]

        ws.cell(header_row, 2, "순번").font = st["head_font"]
        ws.cell(header_row, 2).fill = st["head_fill"]
        for key, col in columns.items():
            # 머리글은 별칭 목록의 첫 번째(표준 표기)를 쓴다.
            label = aliases.get(key, (col.label,))[0]
            cell = ws.cell(header_row, col.index, label)
            cell.font = st["head_font"]
            cell.fill = st["head_fill"]
            cell.alignment = st["center"]
            ws.column_dimensions[cell.column_letter].width = max(10, min(22, len(label) + 4))

        for offset, values in enumerate(samples):
            row = first_row + offset
            ws.cell(row, 2, offset + 1).fill = st["sample_fill"]
            for key, value in values.items():
                cell = ws.cell(row, columns[key].index, value)
                cell.font = st["sample_font"]
                cell.fill = st["sample_fill"]
        ws.freeze_panes = ws.cell(first_row, 3)
        return ws

    sheet(
        ACTIVE_SHEET, ACTIVE_COLUMNS, ACTIVE_HEADER_ALIASES, ACTIVE_FIRST_ROW,
        [
            {
                "employee_id": "A0001", "employee_type": "직원", "job_group": "정규직",
                "name": "홍길동", "gender": "남", "birth_date": "1985-05-01",
                "hire_date": "2010-03-02", "monthly_wage": 5_000_000,
                "daily_base_pay": 150_000, "plan": "DB", "longterm_target": "Y",
            },
            {
                "employee_id": "A0002", "employee_type": "임원", "job_group": "정규직",
                "name": "김임원", "gender": "여", "birth_date": "1972-11-20",
                "hire_date": "1998-01-05", "monthly_wage": 12_000_000,
                "daily_base_pay": 350_000, "plan": "DB", "longterm_target": "N",
            },
        ],
    )
    sheet(
        RETIRED_SHEET, RETIRED_COLUMNS, RETIRED_HEADER_ALIASES, RETIRED_FIRST_ROW,
        [
            {
                "employee_id": "T0001", "employee_type": "직원", "job_group": "정규직",
                "name": "이퇴직", "gender": "남", "birth_date": "1980-02-10",
                "hire_date": "2012-04-01", "exit_date": "2025-06-30",
                "reason": 1, "plan": "DB", "total_payment": 45_000_000,
                "fund_payment": 40_000_000, "longterm_target": "Y",
            },
            {
                "employee_id": "T0002", "employee_type": "직원", "job_group": "계약직",
                "name": "박정년", "gender": "여", "birth_date": "1965-09-15",
                "hire_date": "2001-07-01", "exit_date": "2025-09-30",
                "reason": 4, "plan": "퇴직금제도", "total_payment": 88_000_000,
                "longterm_target": "N",
            },
        ],
    )

    # ── Input 시트 ────────────────────────────────────────────────
    ws = wb.create_sheet("Input", 0)
    ws.cell(1, 2, "산출 기준").font = st["title_font"]
    ws.cell(3, 2, "산출기준일")
    ws.cell(3, 3, "2025-12-31").font = st["sample_font"]
    ws.cell(5, 2, "평균임금 체크금액")
    ws.cell(5, 3, 0).font = st["sample_font"]
    ws.cell(6, 2, "· 이 금액보다 낮은 평균임금은 오류로 봅니다. 쓰지 않으려면 0.").font = st["note_font"]

    ws.cell(9, 2, "직군 규칙").font = st["title_font"]
    ws.cell(10, 2, "· 직군은 '산출 가정 입력' 화면의 [직군 매핑] 탭에서 정하는 편이 쉽습니다.").font = st["note_font"]
    headers = (
        "명부직군", "변환직군명", "퇴직급여 정년연령", "장기급여 정년연령", "정년초과 가산연령",
    )
    for col, title in enumerate(headers, start=2):
        cell = ws.cell(11, col, title)
        cell.font = st["head_font"]
        cell.fill = st["head_fill"]
        cell.alignment = st["center"]
        ws.column_dimensions[cell.column_letter].width = 18
    for offset, group in enumerate(DEFAULT_GROUPS):
        row = 12 + offset
        for col, value in enumerate((group, group, 60, 60, 2), start=2):
            ws.cell(row, col, value).font = st["sample_font"]
            ws.cell(row, col).fill = st["sample_fill"]

    del wb["Sheet"]
    wb.save(path)
    return path


def write_default_roster(path: str | Path, *, seed: int = 20251231) -> Path:
    """기본 명부를 만든다 — 재직 290명 · 퇴직 28명.

    **난수로 만든 가상 명부다.** 실존 인물이 아니고, 같은 씨앗이면 언제나 같은
    명부가 나온다.

    전에는 실제 평가 사례의 명부를 그대로 넣었다. 성명은 없었지만 생년월일·
    입사일·30일 평균임금이 사람마다 한 줄씩이라, 같은 회사 안에서는 특정될 수
    있는 자료였다 — 프로그램을 남에게 건네면 그 자료도 같이 건네진다. 처음 한 번
    돌려 보는 것이 목적이니 실제 값일 이유가 없다.

    형태는 그대로 남겼다. DC 가입자가 섞여 산출대상에서 빠지고, 임원인데 직군
    칸이 '정규직' 인 사람이 있고, 중간정산자가 있다. 자료가 더 험한 명부를 보려면
    :func:`~pension.rostergen.write_case_pack` 의 시험명부 3종을 쓴다.
    """
    from .rostergen import DEFAULT_CASE, write_case_roster

    return write_case_roster(DEFAULT_CASE, path, seed=seed)



def write_standard_assumptions(
    path: str | Path,
    *,
    job_groups: tuple[str, ...] = DEFAULT_GROUPS,
    yield_curve_path: str | Path | None = None,
    grade: str = "",
    size: str = DEFAULT_SIZE,
) -> Path:
    """기본값이 채워진 기초율 워크북을 만든다.

    사망률만 근거 있는 값이고 나머지는 회사가 손봐야 한다. 그 구분이 파일을
    여는 순간 보이도록 시트마다 비고를 적는다 — 파일이 담당자에서 감사인까지
    돌아다니는 동안 근거를 되짚을 수 있는 곳은 파일 안뿐이다.

    :param size: 표준률 원표의 사업장 규모 열(``300인 미만`` / ``300인 이상``).
        승급률·중도퇴직률이 이 값으로 갈린다. 어느 쪽을 썼는지 시트 비고에
        남긴다 — 나중에 파일만 보고는 알 수 없기 때문이다.
    """
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    path = Path(path)
    groups = [g for g in job_groups if g] or list(DEFAULT_GROUPS)
    n = len(groups)
    size = normalize_size(size)

    # 금리표 파일을 주면 결산일 현물이자율 곡선을 그대로 쓴다. 만기가 17개나
    # 되어 손으로 옮기면 자릿수를 틀리기 쉽다.
    discount_rows: list[list[Any]] = []
    discount_note = ""
    if yield_curve_path is not None:
        from .yieldcurve import pick_curve, read_yield_curves

        curve = pick_curve(read_yield_curves(yield_curve_path), grade)
        if curve is not None:
            discount_rows = curve.rows()
            discount_note = (
                f"· 출처: {Path(yield_curve_path).name} — {curve.label}"
                + (f" ({curve.base_date} 기준)" if curve.base_date else "")
                + "\n· 만기별 현물이자율(spot)입니다. 결산일마다 새로 받아 바꾸세요."
            )

    wb = openpyxl.Workbook()
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="44546A")
    note_font = Font(italic=True, color="808080")
    warn_font = Font(bold=True, color="C00000")

    def make(name: str, headers: list[str], rows: list[list[Any]], note: str,
             warn: str = "") -> None:
        ws = wb.create_sheet(name)
        for col, title in enumerate(headers, start=1):
            cell = ws.cell(1, col, title)
            cell.font = head_font
            cell.fill = head_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            ws.column_dimensions[cell.column_letter].width = max(12, len(str(title)) + 4)
        for offset, values in enumerate(rows, start=2):
            for col, value in enumerate(values, start=1):
                ws.cell(offset, col, value)
        tail = len(rows) + 3
        if warn:
            ws.cell(tail, 1, warn).font = warn_font
            tail += 1
        ws.cell(tail, 1, note).font = note_font
        ws.freeze_panes = "A2"

    make(
        DISCOUNT_SHEET, ["연차", "할인율"], discount_rows or [list(r) for r in DISCOUNT_RATE],
        discount_note or
        "· 한 줄이면 전 기간 단일 할인율, 여러 줄이면 각 연차의 현물이자율(spot)입니다.",
        warn="" if discount_rows else
        "[필수 확인] 결산일 현재 우량회사채 수익률로 바꾸세요. 매 결산 달라집니다.",
    )
    make(
        SALARY_SHEET, ["연차", "Base-up 상승률"], [list(r) for r in SALARY_BASE_UP],
        "· 승진·승급을 뺀 공통 인상률입니다. 마지막 줄 값이 그 이후 전 기간에 적용됩니다.",
        warn="[필수 확인] 회사의 임금협상 이력과 중장기 계획으로 바꾸세요.",
    )
    make(
        PROMOTION_SHEET, ["연령", *groups],
        [[age, *[rate] * n] for age, rate in promotion_table(size)],
        f"· 출처: {STANDARD_YEAR} 승급률 — {size} (Base-up 제외 값)\n"
        "· Base-up 과 더해져 총 임금상승률이 됩니다. A1 을 '근속'으로 바꾸면 근속 기준입니다.",
        warn="[필수 확인] 표준률입니다. 회사 경험률(호봉표·승진 이력)이 있으면 그쪽으로 바꾸세요.",
    )
    make(
        WITHDRAWAL_SHEET, ["연령", *groups],
        [[age, *[rate] * n] for age, rate in withdrawal_table(size)],
        f"· 출처: {STANDARD_YEAR} 중도퇴직률 — {size}\n"
        "· 사망을 제외한 연간 중도퇴직률입니다. 계단식으로 읽히므로 모든 연령을 적을 필요는 없습니다.",
        warn="[필수 확인] 표준률입니다. 과거 3~5년 회사 경험률로 반드시 바꾸세요. "
             "채무에 가장 크게 영향을 주는 가정입니다.",
    )
    make(
        MORTALITY_SHEET, ["연령", "남자", "여자"], mortality_table(),
        f"· 출처: {MORTALITY_SOURCE}\n"
        "· 재직자 사망률이라 국민 전체 생명표보다 낮습니다. 회사 경험률로 바꾸려면 "
        "이 두 열만 고치면 됩니다.\n"
        "· 사망률이 채무에 미치는 영향은 퇴직률·임금상승률에 비해 작습니다.",
    )
    # 비워 둔다. '지급률규정' 방식이 법정이라 표가 비면 배수 = 근속연수(법정
    # 퇴직금)가 된다. 예시 숫자를 채워 두면 그 값이 진짜 규정인 줄 알고 그냥
    # 산출해 버리는 일이 생긴다.
    make(
        BENEFIT_SHEET, ["근속연수", *groups], [],
        "· 30일 평균임금 대비 지급배수입니다. **비워 두면 법정 퇴직금**(근속 1년당 30일분)입니다.\n"
        "· 회사 규정이 법정과 다르면 근속연수별 배수를 여기에 적으세요 — 적는 순간 그 값이 쓰입니다.\n"
        "· 누진제라면 구간별 연 배수를 줄마다 적고 '지급률규정' 방식을 '누진' 으로 바꾸세요.",
    )
    make(
        BENEFIT_RULE_SHEET, ["규정명", "방식", "수식", "설명"],
        [[g, STATUTORY_MODE, "", "법정 퇴직금 (근속 1년당 30일분)"] for g in groups],
        "· 방식: 법정(기본 — 지급률 표가 비면 배수=근속연수, 표에 값이 있으면 그 값) /\n"
        "        누적(표 값이 누적 배수) / 누진(표 값이 구간별 연 배수) / 수식\n"
        "· 수식 변수: t=근속연수, x=연령, N=정년연령, S=30일 평균임금, 제도, 직군, 임직원, 배수\n"
        "· 되도록 표를 쓰세요. 수식은 표로 담기 어려운 규정에만 씁니다.",
    )
    make(
        LONGTERM_SHEET, ["근속연수", *groups], [],
        "· 근속 포상·장기근속휴가입니다. 없으면 비워 두세요.\n"
        "· 숫자의 뜻은 '장기급여규정' 시트의 지급유형에 따라 달라집니다 "
        "(휴가=일수, 평균임금=배수, 현물·현금=금액).",
    )
    make(
        LONGTERM_RULE_SHEET, ["규정명", "지급유형", "현물 상승률", "환산 근거"],
        [[g, LT_VACATION, None, ""] for g in groups],
        "· 지급유형: 휴가 / 평균임금 / 현물 / 현금\n"
        "· 현물 포상만 상승률을 따로 받습니다. 시세는 평가시점으로 환산해 넣고 근거를 남기세요.",
    )

    # 지급규정 — 산출 때 명부 Input 보다 우선한다.
    payout_headers = [
        "명부직군", "변환직군명", "퇴직급여 정년연령", "장기급여 정년연령",
        "정년초과 가산연령", "퇴직급여 지급률 규정", "장기급여 지급률 규정",
        "퇴직급여 퇴직률 규정", "퇴직급여 승급률 규정", "장기급여 퇴직률 규정",
        "장기급여 승급률 규정", "퇴직자 퇴직급여 퇴직률 규정", "퇴직자 장기급여 퇴직률 규정",
        "가입자격(최소근속)", "임원 정년연령", "임원 정년초과 가산연령", "산출 제외",
        "근속 산정방법", "단수 처리", "지급액 반올림 단위", "반올림 방식", "임직원구분",
    ]
    make(
        PAYOUT_SHEET, payout_headers,
        [
            [g, g, 60, 60, 2, *[""] * 8, 1, 0, 2, "", "일할", "그대로", 0, "반올림", ""]
            for g in groups
        ],
        "· '산출 가정 입력' 화면의 [직군 매핑]·[지급규정] 탭이 이 시트를 씁니다.\n"
        "· 명부의 Input 시트보다 우선합니다.\n"
        "· 가입자격 1 = 계속근로 1년 미만은 지급 대상 아님(근로자퇴직급여보장법 제4조).",
    )

    del wb["Sheet"]
    wb.save(path)
    return path


#: 금리표 양식의 만기 칸. KIS-Net 금리표가 내려 주는 순서 그대로다.
CURVE_TENORS: Final[tuple[str, ...]] = (
    "3월", "6월", "9월", "1년", "1년6월", "2년", "2년6월", "3년", "4년", "5년",
    "7년", "10년", "15년", "20년", "30년", "40년", "50년",
)


#: 원표 시트 이름. 원본이 ``Sheet_202312_표준중도퇴직률`` 꼴이라 그 결을 따른다.
RAW_SHEETS: Final[tuple[tuple[str, str, tuple[str, str]], ...]] = (
    ("퇴직률", "표준중도퇴직률", ("300인↓", "300인↑")),
    ("승급률", "bu제외_표준승급률", ("300인↓", "300인↑")),
    ("사망률", "표준사망률", ("남자", "여자")),
)


def write_standard_table(path: str | Path) -> Path:
    """표준률을 **원표 서식 그대로** 낸다 — 등록·대조용.

    :func:`write_standard_assumptions` 가 내는 것은 이 프로그램의 기초율 서식이고,
    이 함수가 내는 것은 고시된 원표와 같은 모양이다. 열이 ``No · 연령 ·
    300인↓ · 300인↑`` 로 서 있어 받은 원본과 눈으로 맞대어 볼 수 있고, 규모
    두 열이 한 표에 있어 어느 쪽을 쓸지 나중에 골라도 된다.

    이 파일은 [표준률] 로 등록해 그대로 쓸 수 있다 — 프로그램이 원표 서식을
    읽는다(:func:`pension.standard_rates.read_raw_workbook`).
    """
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    from .standard_rates import raw_rows

    path = Path(path)
    tables = raw_rows()
    wb = openpyxl.Workbook()
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="44546A")
    note_font = Font(italic=True, color="808080")

    for key, title, columns in RAW_SHEETS:
        ws = wb.create_sheet(f"Sheet_{STANDARD_YEAR}_{title}")
        for index, label in enumerate(("No", "연령", *columns), start=1):
            cell = ws.cell(1, index, label)
            cell.font = head_font
            cell.fill = head_fill
            cell.alignment = Alignment(horizontal="center")
            ws.column_dimensions[cell.column_letter].width = 12
        for offset, row in enumerate(tables[key]):
            ws.cell(offset + 2, 1, f"{offset + 1:03d}")
            for index, value in enumerate(row, start=2):
                ws.cell(offset + 2, index, value)

        last = len(tables[key]) + 3
        ws.cell(last, 1, f"· {STANDARD_YEAR} {title}. 값은 원표 표기(소수점 6자리) 그대로입니다.")
        ws.cell(last, 1).font = note_font
        ws.cell(last + 1, 1,
                "· 마지막 줄(70세) 값이 그 이후 전 연령에 적용됩니다 — 표를 계단식으로 "
                "읽으므로 110세까지 적어 둔 원표와 결과가 같습니다.")
        ws.cell(last + 1, 1).font = note_font
        ws.freeze_panes = "C2"

    del wb["Sheet"]
    wb.save(path)
    return path


def write_curve_template(path: str | Path, *,
                         base_date: _dt.date | None = None) -> Path:
    """금리표(채권) 등록 양식을 만든다. **이율 칸은 비워 둔다.**

    할인율은 결산일의 시장 자료다 — 채권평가사가 그날 내려 주는 값이라
    프로그램이 지어낼 수 있는 성질의 것이 아니고, 지어낸 값으로 확정급여채무를
    산출하면 그대로 공시 숫자가 틀린다. 그래서 **칸의 모양만** 만들어 준다.
    받은 금리표에서 해당 등급 줄을 그대로 붙여 넣으면 된다.

    퍼센트(``3.404``)로 넣든 소수(``0.03404``)로 넣든 읽는 쪽이 알아서 본다.

    모양은 명부 양식과 같게 맞췄다 — 작성요령이 앞에 있고, 채울 칸은 노란색,
    블록 머리는 남색이다. 여러 양식을 함께 보내는데 저마다 생김새가 다르면
    받는 사람이 매번 다시 익혀야 한다.
    """
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    face = "맑은 고딕"
    ink, muted, line = "1F3864", "5B6478", "D6DCE8"
    thin = Side(style="thin", color=line)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    fill_head = PatternFill("solid", fgColor=ink)
    fill_input = PatternFill("solid", fgColor="FFF2CC")

    path = Path(path)
    wb = openpyxl.Workbook()

    # ── 작성요령 ────────────────────────────────────────────────
    guide = wb.active
    guide.title = "작성요령"
    guide.column_dimensions["A"].width = 22
    guide.column_dimensions["B"].width = 86
    guide["A1"] = "금리표 작성요령"
    guide["A1"].font = Font(name=face, size=14, bold=True, color=ink)
    guide["A2"] = ("채권평가사에서 받은 결산일 금리표를 [금리표] 시트에 옮겨 "
                   "적으면 됩니다.")
    guide["A2"].font = Font(name=face, size=9, color=muted)

    rows = (
        ("무엇을 넣나", "채권평가사(KIS채권평가·한국자산평가 등)가 결산일에 "
                    "내려 주는 등급별 기간구조입니다."),
        ("왜 비어 있나", "할인율은 그날의 시장 자료라 프로그램이 지어낼 수 "
                     "없습니다. 지어낸 값으로 산출하면 공시 숫자가 그대로 "
                     "틀립니다."),
        ("기준일자", "결산일로 고치세요. 이 날짜가 산출기준일과 다르면 화면에서 "
                  "경고합니다."),
        ("숫자 넣는 법", "3.404 처럼 퍼센트로 넣어도 되고 0.03404 처럼 소수로 "
                     "넣어도 됩니다."),
        ("등급 고르기", "회사가 정한 회계정책을 따릅니다. 국내 실무는 AA- 이상을 "
                    "우량회사채로 보고 그중 AA0 를 가장 많이 씁니다."),
        ("안 쓰는 줄", "지워도 되고 비워 두어도 됩니다 — 빈 줄은 읽지 않습니다."),
    )
    for offset, (label, note) in enumerate(rows, start=4):
        guide.cell(offset, 1, label).font = Font(name=face, size=9, bold=True)
        cell = guide.cell(offset, 2, note)
        cell.font = Font(name=face, size=9, color=muted)
        cell.alignment = Alignment(vertical="top", wrap_text=True)
        guide.row_dimensions[offset].height = 26

    # ── 금리표 ──────────────────────────────────────────────────
    ws = wb.create_sheet("금리표")
    ws.cell(1, 1, "노란 칸에 결산일 금리표를 옮겨 적으세요. 이율 칸은 비어 "
                  "있습니다.").font = Font(name=face, size=9, italic=True, color=muted)

    headers = ["순번", "기준일자", "구분", "등급", *CURVE_TENORS]
    for column, title in enumerate(headers, start=1):
        cell = ws.cell(2, column, title)
        cell.font = Font(name=face, size=9, bold=True, color="FFFFFF")
        cell.fill = fill_head
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)
        cell.border = border
        ws.column_dimensions[cell.column_letter].width = max(9, len(str(title)) + 3)
    ws.row_dimensions[2].height = 26

    stamp = base_date or _dt.date.today()
    for offset, grade in enumerate(INVESTMENT_GRADES + ("국고채",)):
        row = offset + 3
        ws.cell(row, 1, offset + 1).font = Font(name=face, size=9)
        date_cell = ws.cell(row, 2, stamp)
        date_cell.number_format = "yyyy-mm-dd"
        date_cell.font = Font(name=face, size=9, color="9C6500")
        date_cell.fill = fill_input
        ws.cell(row, 3, "국고채권" if grade == "국고채" else "공모 무보증회사채"
                ).font = Font(name=face, size=9)
        ws.cell(row, 4, grade).font = Font(name=face, size=9, bold=True)
        for column in range(1, len(headers) + 1):
            ws.cell(row, column).border = border
            if column >= 5:                      # 이율 칸 — 채우는 자리
                ws.cell(row, column).fill = fill_input
                ws.cell(row, column).number_format = "0.000"

    ws.freeze_panes = ws.cell(3, 5)
    wb.save(path)
    return path


def write_sample_pack(
    directory: str | Path,
    *,
    job_groups: tuple[str, ...] = DEFAULT_GROUPS,
    yield_curve_path: str | Path | None = None,
    grade: str = "",
    size: str = DEFAULT_SIZE,
) -> list[Path]:
    """기본 파일 한 벌을 폴더에 만든다. 만든 파일 경로 목록을 돌려준다."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    return [
        write_default_roster(directory / ROSTER_DEFAULT),
        write_standard_assumptions(
            directory / STANDARD_ASSUMPTIONS, job_groups=job_groups,
            yield_curve_path=yield_curve_path, grade=grade, size=size,
        ),
        write_roster_template(directory / ROSTER_TEMPLATE),
        write_template(directory / TEMPLATE_ASSUMPTIONS, job_groups=job_groups),
        write_curve_template(directory / CURVE_TEMPLATE),
    ]
