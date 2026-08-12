"""회사에 보내는 명부 양식.

받는 사람은 계리를 모르는 인사·회계 담당자다. 그래서 이 파일 하나로 **무엇을
어디에 적는지** 가 끝나야 한다.

* 색이 진한 앞쪽 열만 채우면 산출된다. 나머지는 해당자만.
* 열은 다섯 묶음으로 갈라 두었다. 해당 없는 묶음은 통째로 지우고 보내도 된다.
* 머리글 이름으로 열을 찾으므로 순서를 바꿔도 되고, 옛 이름으로 적어 보내도
  읽힌다(:mod:`pension.layout` 의 별칭).

명부만으로는 산출이 되지 않으므로 같은 파일에 ``기본정보``·``퇴직급여규정``·
``사외적립자산`` 을 함께 둔다. 따로 보내면 셋 중 하나가 빠진 채로 온다.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

FACE = "맑은 고딕"

# 블록별 색 — 필수인지 아닌지가 열 색만 보고 판단되게 한다.
BLOCKS = {
    "필수": ("1F3864", "FFFFFF", "이 열이 비면 그 사람은 산출되지 않습니다."),
    "제도·근속": ("2E6F6A", "FFFFFF", "해당자만 채우세요. 비우면 아래 '비우면' 대로 봅니다."),
    "장기급여": ("7A5470", "FFFFFF",
              "근속포상·장기근속휴가가 있는 회사만. 없으면 블록째 지워도 됩니다."),
    "개인 예외": ("8F6318", "FFFFFF",
              "이 사람만 직군 규칙과 달라야 할 때. 대부분 비웁니다 — 블록째 지워도 됩니다."),
    "기타": ("5B6478", "FFFFFF", "드물게 쓰는 항목과 관리용. 블록째 지워도 됩니다."),
}

# (블록, 열이름, 뜻, 예시, 비우면)
ACTIVE = [
    ("필수", "순번", "1부터 이어지는 번호", 1, "무시합니다"),
    ("필수", "사번", "회사 사번. 전기와 맞대어 볼 때 이것으로 짝지웁니다",
     "A0001", "주민번호+성명으로 임시 사번을 만듭니다"),
    ("필수", "성명", "동명이인이 있어도 그대로", "홍길동", "사번만으로 봅니다"),
    ("필수", "주민등록번호 앞7자리",
     "생년월일과 성별을 여기서 읽습니다. **뒷 여섯 자리는 적지 마세요** — "
     "받는 순간 개인정보 등급이 올라갑니다", "850501-1",
     "생년월일·성별 칸을 따로 채우세요"),
    ("필수", "생년월일", "주민번호를 적었으면 비워도 됩니다", "", "주민번호에서 읽습니다"),
    ("필수", "입사일자", "근속 기산일", "2010-03-02", "근속을 못 정해 산출 제외"),
    ("필수", "중간정산일", "중간정산을 했으면 그 날. 근속이 이 날부터 다시 셉니다",
     "", "중간정산 없음 — 입사일부터 셉니다"),
    ("필수", "직군", "지급률·정년이 갈리는 묶음. **임원도 여기에 적습니다** "
     "('임원'·'상무'·'등기이사'). [기본정보] 의 직군 규칙에서 묶어 줍니다",
     "정규직", "첫 직군으로 봅니다"),
    ("필수", "규정명", "이 사람에게 걸 지급률 규정. 경험률을 규정별로 볼 때도 씁니다",
     "규정A", "직군에 걸린 규정"),
    ("필수", "30일 평균임금", "근로기준법상 평균임금 30일분(원)", 5_000_000,
     "임금이 0이라 채무도 0"),
    ("필수", "추계액", "회사가 계산해 둔 퇴직급여추계액(원). 우리 값과 맞대어 봅니다",
     45_000_000, "검산을 건너뜁니다"),

    ("제도·근속", "퇴직급여 제도구분", "DB / DC / 퇴직금제도. DC 는 산출에서 빠집니다",
     "DB", "DB 로 봅니다"),
    ("제도·근속", "DB비율", "혼합형이면 DB 비중. `DC 1% / DB 99%` 면 0.99 (99 로 적어도 됩니다)",
     "", "1 (전액 DB)"),
    ("제도·근속", "중간정산 지급금액", "중간정산으로 이미 지급한 금액(원)", "", "0"),
    ("제도·근속", "휴직차감일수",
     "근속에서 빼는 휴직 **일수**. 기산일을 그만큼 뒤로 밉니다 — 연수로 환산하지 "
     "마세요", "", "0"),
    ("제도·근속", "잔여계약기간",
     "정년이 아니라 **계약 만료** 로 나가는 사람. 몇 년 남았는지 적으면 "
     "그때 퇴직하는 것으로 봅니다", "", "직군 규칙의 정년까지 근무"),
    ("제도·근속", "원가코드", "제조원가 / 판관비 등 배분 코드", "판관비", "배분표를 안 만듭니다"),

    ("장기급여", "장기급여 대상", "Y / N", "N", "N (대상 아님)"),
    ("장기급여", "1일 통상임금", "장기근속휴가를 금액으로 환산할 때 씁니다(원)", "", "0"),
    ("장기급여", "장기급여 기지급액", "이미 지급한 근속포상 금액(원)", "", "0"),

    ("개인 예외", "정년연령", "이 사람만 정년이 다를 때", "", "직군 규칙의 정년"),
    ("개인 예외", "임금피크 연령", "임금피크가 시작되는 연령", "", "적용 안 함"),
    ("개인 예외", "임원지급배수",
     "임원 2·3배수처럼 이 사람만 배수가 다를 때. 지급률 규정이 내는 배수에 "
     "이 값을 곱합니다 — 규정에 이미 배수가 들어 있으면 비우세요(두 번 곱해집니다)",
     "", "1 로 봅니다"),
    ("개인 예외", "퇴직률 규정", "중도·사망퇴직률을 다르게 걸 때", "", "직군에 걸린 규정"),
    ("개인 예외", "승급률 규정", "승급률을 다르게 걸 때", "", "직군에 걸린 규정"),
    ("개인 예외", "장기급여 지급률 규정", "장기급여만 다른 지급률을 걸 때", "",
     "직군에 걸린 규정"),
    ("개인 예외", "장기급여 퇴직률 규정", "장기급여만 다른 퇴직률을 걸 때", "",
     "직군에 걸린 규정"),
    ("개인 예외", "장기급여 승급률 규정", "장기급여만 다른 승급률을 걸 때", "",
     "직군에 걸린 규정"),

    ("기타", "명예퇴직 기준임금", "명예퇴직 급여를 다른 임금으로 계산할 때(원)",
     "", "30일 평균임금을 씁니다"),
    ("기타", "전입일", "계열사에서 옮겨 온 날", "", "해당 없음"),
    ("기타", "전입 인수액", "함께 넘겨받은 채무액(원)", "", "0"),
    ("기타", "추가지급 기준일", "이 날 이후 근속분에 지급률이 달라질 때 그 기준일",
     "", "구간을 나누지 않습니다"),
    ("기타", "추가지급 기본급", "추가지급 구간에 쓸 기본급(원)", "", "30일 평균임금을 씁니다"),
    ("기타", "비고", "자유 기재. 산출에는 쓰지 않습니다", "", ""),
]

RETIRED = [
    ("필수", "순번", "1부터 이어지는 번호", 1, "무시합니다"),
    ("필수", "사번", "회사 사번", "T0001", "주민번호+성명으로 임시 사번을 만듭니다"),
    ("필수", "성명", "", "이퇴직", "사번만으로 봅니다"),
    ("필수", "주민등록번호 앞7자리", "생년월일과 성별. 뒷 여섯 자리는 적지 마세요",
     "800210-1", "생년월일 칸을 채우세요"),
    ("필수", "생년월일", "주민번호를 적었으면 비워도 됩니다", "", "주민번호에서 읽습니다"),
    ("필수", "입사일자", "근속 기산일", "2012-04-01", "근속을 못 정합니다"),
    ("필수", "퇴사일", "실제 퇴직일. DC 전환일·전출일도 여기에 적습니다",
     "2025-06-30", "퇴직자로 세지 않습니다"),
    ("필수", "퇴직사유",
     "중도 / 사망 / DC전환 / 정년 / 전출 / 사업처분 (숫자 1~6 으로 적어도 됩니다)",
     "중도", "사유 미상으로 봅니다"),
    ("필수", "직군", "재직자명부와 같은 표기로. 임원도 여기에", "정규직", "첫 직군으로 봅니다"),

    ("제도·근속", "퇴직급여 제도구분", "DB / DC / 퇴직금제도", "DB", "DB 로 봅니다"),
    ("제도·근속", "퇴직급여 총지급액", "실제로 지급한 퇴직급여 전액(원)", 45_000_000,
     "지급액 0 으로 봅니다"),
    ("제도·근속", "사외자산 지급액", "그중 사외적립자산에서 나간 금액(원)", 40_000_000, "0"),
    ("제도·근속", "사외자산 지급일", "자산에서 실제로 나간 날", "", "퇴사일로 봅니다"),
    ("제도·근속", "국민연금 전환금", "국민연금 전환금 지급액(원)", "", "0"),
    ("제도·근속", "원가코드", "제조원가 / 판관비 등 배분 코드", "제조원가", "배분표를 안 만듭니다"),

    ("장기급여", "장기급여 대상", "Y / N", "N", "N (대상 아님)"),
    ("장기급여", "장기급여 지급액", "퇴직하며 지급한 장기급여(원)", "", "0"),

    ("개인 예외", "퇴직률 규정", "이 사람만 다른 퇴직률을 걸 때", "", "직군에 걸린 규정"),
    ("개인 예외", "장기급여 퇴직률 규정", "장기급여만 다른 퇴직률을 걸 때", "",
     "직군에 걸린 규정"),

    ("기타", "퇴직위로금 등", "퇴직급여 외에 지급한 금액(원)", "", "0"),
    ("기타", "전출 지급액", "계열사 전출·사업처분으로 넘긴 금액(원)", "", "0"),
    ("기타", "비고", "자유 기재. 산출에는 쓰지 않습니다", "", ""),
]

THIN = Side(style="thin", color="B8C0D0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

#: 퇴직사유 코드 → 말. 기존 명부는 숫자로 오므로 제안 서식에서는 말로 보여 준다.
REASON_WORD = {
    "1": "중도", "2": "사망", "3": "DC전환",
    "4": "정년", "5": "전출", "6": "사업처분",
}


#: 두 번째 작성 예시. 한 줄만 보이면 "이 줄만 고치면 되나" 로 읽혀, 사람이
#: 늘어날 때 어디에 이어 적어야 할지 알기 어렵다. 성격이 다른 사람으로 둔다.
SECOND_ACTIVE = {
    "순번": 2, "사번": "A0002", "성명": "김임원",
    "주민등록번호 앞7자리": "720815-2", "입사일자": "2005-01-02",
    "직군": "임원", "규정명": "임원규정", "30일 평균임금": 9_000_000,
    "추계액": 210_000_000, "퇴직급여 제도구분": "DB", "임원지급배수": 2,
    "원가코드": "판관비", "장기급여 대상": "N",
}
SECOND_RETIRED = {
    "순번": 2, "사번": "T0002", "성명": "박정년",
    "주민등록번호 앞7자리": "651120-2",
    "입사일자": "1998-03-02", "퇴사일": "2025-11-20",
    "퇴직사유": "정년", "직군": "정규직",
    "퇴직급여 제도구분": "DB", "퇴직급여 총지급액": 120_000_000,
    "사외자산 지급액": 118_000_000, "원가코드": "제조원가", "장기급여 대상": "N",
}


def _sheet(wb, name: str, columns: list, first_row: int = 4, second: dict | None = None,
           rows: list[dict] | None = None, extras: tuple[str, ...] = ()):
    """명부 시트 한 장.

    :param rows: 채워 넣을 자료. 주면 작성 예시 두 줄 대신 이것을 적는다.
        열쇠는 **열 이름** 이다(:data:`ACTIVE` 의 두 번째 항목).
    :param extras: 고정 서식에 없는 열. 오른쪽에 덧붙인다 — 회사가 누진 보전·
        지급구간처럼 자기네 열을 더해 보내는 모양 그대로다.
    """
    columns = list(columns) + [("기타", label, "", "", "") for label in extras]

    ws = wb.create_sheet(name)
    ws.cell(1, 1, f"{name} — 색이 진한 앞쪽 열이 필수입니다."
                  + ("" if rows else " 노란 줄은 작성 예시이니 지우고 쓰세요."))
    ws.cell(1, 1).font = Font(name=FACE, size=9, italic=True, color="5B6478")

    # 2행: 블록 이름을 병합해 얹는다. 어디까지가 필수인지 한눈에 보이게.
    start = 1
    for index in range(1, len(columns) + 1):
        block = columns[index - 1][0]
        last = index == len(columns)
        if last or columns[index][0] != block:
            fill, ink, _note = BLOCKS[block]
            ws.merge_cells(start_row=2, start_column=start, end_row=2, end_column=index)
            cell = ws.cell(2, start, block)
            cell.font = Font(name=FACE, size=9, bold=True, color=ink)
            cell.fill = PatternFill("solid", fgColor=fill)
            cell.alignment = Alignment(horizontal="center")
            start = index + 1

    for index, (block, label, _mean, sample, _blank) in enumerate(columns, start=1):
        fill, ink, _note = BLOCKS[block]
        cell = ws.cell(3, index, label)
        cell.font = Font(name=FACE, size=9, bold=True, color=ink)
        cell.fill = PatternFill("solid", fgColor=fill)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER
        ws.column_dimensions[get_column_letter(index)].width = max(11, min(18, len(label) + 5))

        if rows is None:
            sample_cell = ws.cell(first_row, index, sample)
            sample_cell.font = Font(name=FACE, size=9, color="9C6500")
            sample_cell.fill = PatternFill("solid", fgColor="FFF2CC")
            sample_cell.border = BORDER

    if rows is None:
        for index, (block, label, _mean, _sample, _blank) in enumerate(columns, start=1):
            value = (second or {}).get(label)
            cell = ws.cell(first_row + 1, index, value)
            cell.font = Font(name=FACE, size=9, color="9C6500")
            cell.fill = PatternFill("solid", fgColor="FFF2CC")
            cell.border = BORDER
    else:
        where = {label: index for index, (_b, label, *_r) in enumerate(columns, start=1)}
        body = Font(name=FACE, size=9)
        for offset, record in enumerate(rows):
            row = first_row + offset
            for label, value in record.items():
                index = where.get(label)
                if index is None or value == "":
                    continue
                ws.cell(row, index, value).font = body

    ws.row_dimensions[3].height = 34
    return ws


def _guide(wb) -> None:
    ws = wb.create_sheet("작성요령", 0)
    ws.column_dimensions["A"].width = 13
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 56
    ws.column_dimensions["D"].width = 16
    ws.column_dimensions["E"].width = 34

    ws["A1"] = "명부 작성요령"
    ws["A1"].font = Font(name=FACE, size=14, bold=True, color="1F3864")
    ws["A2"] = ("필수(남색) 열만 채우면 산출됩니다. 나머지는 해당자만 채우세요 — "
                "비워 두면 아래 '비우면' 대로 처리합니다.")
    ws["A2"].font = Font(name=FACE, size=9, color="5B6478")
    ws["A3"] = ("우리 회사에 해당 없는 블록은 열째 지우고 보내도 됩니다. "
                "머리글 이름으로 열을 찾으므로 순서를 바꿔도 됩니다.")
    ws["A3"].font = Font(name=FACE, size=9, color="5B6478")

    row = 5
    ws.cell(row, 1, "시트").font = Font(name=FACE, size=11, bold=True, color="1F3864")
    row += 1
    for name, what in (
        ("기본정보", "단체명·산출기준일·상시근로자 수·신용등급, 그리고 직군 규칙"),
        ("퇴직급여규정", "지급규정 열 항목과 특이사항. 산출가정의 근거가 됩니다"),
        ("사외적립자산", "신탁 명세서의 증감표·세부내역. 순확정급여부채가 여기서 나옵니다"),
        ("재직자명부", "기준일 현재 재직 중인 사람"),
        ("퇴직자명부", "기중에 퇴직·전출·DC전환한 사람"),
    ):
        ws.cell(row, 1, name).font = Font(name=FACE, size=9, bold=True)
        ws.cell(row, 2, what).font = Font(name=FACE, size=9, color="5B6478")
        row += 1
    row += 1

    ws.cell(row, 1, "명부 열 블록").font = Font(name=FACE, size=11, bold=True, color="1F3864")
    row += 1
    for block, (fill, ink, note) in BLOCKS.items():
        cell = ws.cell(row, 1, block)
        cell.font = Font(name=FACE, size=9, bold=True, color=ink)
        cell.fill = PatternFill("solid", fgColor=fill)
        cell.border = BORDER
        ws.cell(row, 2, note).font = Font(name=FACE, size=9, color="5B6478")
        row += 1
    row += 1

    for title, columns in (("재직자명부", ACTIVE), ("퇴직자명부", RETIRED)):
        ws.cell(row, 1, f"{title} ({len(columns)}열)").font = Font(
            name=FACE, size=11, bold=True, color="1F3864")
        row += 1
        for index, head in enumerate(("구분", "열 이름", "뜻", "예시", "비우면")):
            cell = ws.cell(row, index + 1, head)
            cell.font = Font(name=FACE, size=9, bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="44546A")
            cell.border = BORDER
        row += 1
        for block, label, meaning, sample, blank in columns:
            ws.cell(row, 1, block).font = Font(name=FACE, size=9, color=BLOCKS[block][0])
            ws.cell(row, 2, label).font = Font(name=FACE, size=9, bold=block == "필수")
            ws.cell(row, 3, meaning).font = Font(name=FACE, size=9)
            ws.cell(row, 4, sample).font = Font(name=FACE, size=9, color="9C6500")
            ws.cell(row, 5, blank).font = Font(name=FACE, size=9, color="5B6478")
            for column in range(1, 6):
                ws.cell(row, column).border = BORDER
                ws.cell(row, column).alignment = Alignment(vertical="top", wrap_text=True)
            row += 1
        row += 2


def _basics(wb, *, values: dict | None = None, groups: list | None = None,
            note: str = "") -> None:
    """예전 ``Input`` 시트. 'C3 에 넣으세요' 대신 이름을 붙인다.

    :param values: 항목 이름 → 값. 주면 예시 대신 이 값을 적는다.
    :param groups: 직군 규칙 표의 줄들. ``(명부 직군, 산출 직군, 정년, 장기급여
        정년, 가산연수)``.
    """
    filled = values is not None
    ws = wb.create_sheet("기본정보", 1)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 56

    ws["A1"] = "기본정보"
    ws["A1"].font = Font(name=FACE, size=14, bold=True, color="1F3864")
    ws["A2"] = note or "노란 칸만 채우면 됩니다."
    ws["A2"].font = Font(name=FACE, size=9, color="5B6478")

    rows = [
        ("단체명", "○○주식회사", "보고서 표지에 올라갑니다"),
        ("산출기준일", "2025-12-31", "결산일. 비우면 프로그램 화면에서 넣어도 됩니다"),
        ("산출 시작일", "2025-01-01", "직전 결산일 다음 날. 이자원가를 이 기간으로 환산합니다"),
        ("상시근로자 수", 275, "표준률의 300인 미만/이상을 고르는 데 씁니다"),
        ("회사채 신용등급", "AA-", "할인율로 쓸 회사채 등급. AAA·AA+·AA0·AA-·A+·A0·A- 중"),
        ("평균임금 하한 점검액", 0, "이보다 낮은 평균임금을 오류로 봅니다. 0 이면 점검 안 함"),
    ]
    if filled:
        rows = [(label, values.get(label, value), note_)
                for label, value, note_ in rows]
    for offset, (label, value, note) in enumerate(rows, start=3):
        ws.cell(offset, 1, label).font = Font(name=FACE, size=9, bold=True)
        cell = ws.cell(offset, 2, value)
        cell.font = Font(name=FACE, size=9, color="000000" if filled else "0000FF")
        if not filled:
            cell.fill = PatternFill("solid", fgColor="FFFF00")
        cell.border = BORDER
        ws.cell(offset, 3, note).font = Font(name=FACE, size=9, color="5B6478")

    ws["A10"] = "직군 규칙"
    ws["A10"].font = Font(name=FACE, size=11, bold=True, color="1F3864")
    ws["A11"] = "명부에 적은 직군을 산출에 쓸 묶음으로 배정합니다. 정년이 다르면 여기서 나눕니다."
    ws["A11"].font = Font(name=FACE, size=9, color="5B6478")

    heads = ("명부 직군", "산출 직군", "정년연령", "장기급여 정년", "정년초과 가산연수")
    for index, head in enumerate(heads, start=1):
        cell = ws.cell(12, index, head)
        cell.font = Font(name=FACE, size=9, bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="44546A")
        cell.border = BORDER
        ws.column_dimensions[get_column_letter(index)].width = max(14, len(head) + 6)
    table = groups or [(group, group, 60, 60, 2) for group in ("정규직", "계약직", "임원")]
    for offset, line in enumerate(table, start=13):
        for index, value in enumerate(line, start=1):
            cell = ws.cell(offset, index, value)
            cell.font = Font(name=FACE, size=9, color="000000" if filled else "9C6500")
            if not filled:
                cell.fill = PatternFill("solid", fgColor="FFF2CC")
            cell.border = BORDER


# ── 퇴직급여규정 · 특이사항 ──────────────────────────────────────

#: 지급규정 항목. (라벨, 예시, 설명)
RULE_ROWS = [
    ("가입자격", "전 임직원", "근속 1년 이상 등 제한이 있으면 그대로 적으세요"),
    ("근속기간 산정", "월할 계산, 1년 미만 단수개월 절사",
     "일할 / 월할 / 분기할 / 반기할 / 연할 중 어느 것인지"),
    ("계산구조", "ROUND(평균임금 × 근속연수 × 지급률, -1)",
     "수식 그대로 적어 주시면 됩니다"),
    ("기준임금", "퇴직 직전 3개월 평균임금", "무엇을 급여 기준으로 삼는지"),
    ("정년 (직원)", "만 60세", ""),
    ("정년 (임원)", "없음", "정년이 없으면 '없음' 이라고 적으세요"),
    ("중도퇴직 지급률", "근속연수 × 1.0", "사유별로 배수가 다르면 각각 적으세요"),
    ("사망퇴직 지급률", "근속연수 × 1.0", ""),
    ("정년퇴직 지급률", "근속연수 × 1.0", ""),
    ("지급방법", "일시금", "일시금 / 연금 / 선택"),
]

#: 특이사항. (구분, 예시)
SPECIAL_ROWS = [
    ("제도 변경", "2023-01-01 부터 누진제 폐지, 그 전 근속분은 종전 지급률 유지"),
    ("명예퇴직", "만 55세 이상·근속 20년 이상 대상, 잔여 정년 개월수 × 기본급"),
    ("임금피크", "만 57세부터 매년 10% 감액"),
    ("중간정산", "2016년 전원 중간정산, 근속을 그 날부터 다시 셈"),
    ("임원", "임원 퇴직금 지급규정 별도. 세법 한도 초과분 프로즌"),
    ("DC 전환", "2024년 신입부터 DC. 전환자는 전환일 기준으로 정산 완료"),
    ("장기급여", "근속 10·20·30년에 포상금, 20년에 장기근속휴가 10일"),
    ("그 밖에", "산출에 영향을 줄 만한 것은 모두 적어 주세요"),
]


def _rules(wb, *, filled: bool = False, specials: dict[str, str] | None = None) -> None:
    """퇴직급여 지급규정과 특이사항. 예전 ``일반사항`` 의 지급규정 칸.

    :param specials: 특이사항 구분 → 내용. 주면 그 줄을 채운 채로 낸다.
    """
    ws = wb.create_sheet("퇴직급여규정", 2)
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 52
    ws.column_dimensions["D"].width = 46

    ws["A1"] = "퇴직급여 지급규정 및 특이사항"
    ws["A1"].font = Font(name=FACE, size=14, bold=True, color="1F3864")
    ws["A2"] = ("규정집을 그대로 옮길 필요는 없습니다. 아래 항목만 한 줄씩 적어 주세요. "
                "여기 적힌 대로 산출가정을 세우고, 보고서에 근거로 남깁니다.")
    ws["A2"].font = Font(name=FACE, size=9, color="5B6478")

    def _band(row: int, title: str, heads: tuple[str, ...]) -> int:
        ws.cell(row, 2, title).font = Font(name=FACE, size=11, bold=True, color="1F3864")
        row += 1
        for column, head in zip((2, 3, 4), heads):
            cell = ws.cell(row, column, head)
            cell.font = Font(name=FACE, size=9, bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="44546A")
            cell.border = BORDER
        return row + 1

    row = _band(4, "퇴직급여 지급규정", ("항목", "내용", "적는 법"))
    for label, sample, note in RULE_ROWS:
        ws.cell(row, 2, label).font = Font(name=FACE, size=9, bold=True)
        cell = ws.cell(row, 3, sample)
        cell.font = Font(name=FACE, size=9,
                         color="000000" if filled else "9C6500")
        cell.fill = PatternFill("solid", fgColor="FFFFFF" if filled else "FFF2CC")
        ws.cell(row, 4, note).font = Font(name=FACE, size=9, color="5B6478")
        for column in (2, 3, 4):
            ws.cell(row, column).border = BORDER
            ws.cell(row, column).alignment = Alignment(vertical="top", wrap_text=True)
        row += 1

    row += 1
    row = _band(row, "특이사항", ("구분", "내용", "적는 법"))
    for label, sample in SPECIAL_ROWS:
        ws.cell(row, 2, label).font = Font(name=FACE, size=9, bold=True)
        if specials is not None:
            written = specials.get(label, "")
        else:
            written = sample if filled else ""
        cell = ws.cell(row, 3, written)
        cell.font = Font(name=FACE, size=9)
        cell.fill = PatternFill("solid", fgColor="FFFFFF")
        ws.cell(row, 4, sample).font = Font(name=FACE, size=9, color="9C6500")
        for column in (2, 3, 4):
            ws.cell(row, column).border = BORDER
            ws.cell(row, column).alignment = Alignment(vertical="top", wrap_text=True)
        ws.row_dimensions[row].height = 22
        row += 1
    ws.cell(row, 2, "해당 없으면 비워 두세요. 오른쪽은 예시일 뿐 회사 사실이 아닙니다.").font = (
        Font(name=FACE, size=9, italic=True, color="5B6478"))


# ── 사외적립자산 ────────────────────────────────────────────────

#: 퇴직급여추계액 변동내역. (부호, 항목, 예시금액)
OBLIGATION_ROWS = [
    ("(+)", "계열사 전입", 0),
    ("(+)", "합병으로 받은 금액", 0),
    ("(-)", "퇴직금 지급액", 980_000_000),
    ("(-)", "중간정산금", 120_000_000),
    ("(-)", "DC전환 지급액", 0),
    ("(-)", "퇴직위로금 (명예퇴직금 등)", 60_000_000),
    ("(-)", "계열사 전출", 0),
    ("(-)", "사업처분·분할", 0),
]

#: 사외적립자산 변동내역. (부호, 항목, DB, 국민연금전환금)
ASSET_ROWS = [
    ("(+)", "부담금 납입액", 1_500_000_000, 0),
    ("(+)", "이자수익", 420_000_000, 0),
    ("(+)", "계열사 전입", 0, 0),
    ("(+)", "합병으로 받은 금액", 0, 0),
    ("(-)", "퇴직금 지급액", 980_000_000, 0),
    ("(-)", "중간정산금", 120_000_000, 0),
    ("(-)", "DC전환 지급액", 0, 0),
    ("(-)", "계열사 전출", 0, 0),
    ("(-)", "사업처분·분할", 0, 0),
    ("(-)", "운용관리수수료", 18_000_000, 0),
    ("(-)", "자산관리수수료", 9_000_000, 0),
]

ASSET_OPENING = (12_000_000_000, 300_000_000)
#: 기말은 신탁 명세서의 숫자를 그대로 적는 자리다. 검증 줄이 0 이 되는 값.
ASSET_CLOSING = (12_793_000_000, 300_000_000)

#: 자산 분류별 공정가치 (문단 142 공시).
ASSET_BREAKDOWN = [
    ("현금 및 현금등가물", 150_000_000),
    ("정기예금·원리금보장 GIC", 9_800_000_000),
    ("국공채", 1_500_000_000),
    ("특수채·금융채", 700_000_000),
    ("회사채", 400_000_000),
    ("수익증권 (펀드)", 543_000_000),
    ("그 밖의 자산", 0),
]


def _assets(wb, *, filled: bool = False, numbers: dict | None = None) -> None:
    """사외적립자산 증감표와 세부내역.

    표 제목과 항목 이름은 지금 프로그램이 찾는 말 그대로 두었다. 시트 이름만
    ``일반사항`` 에서 바뀐다.

    :param numbers: 채워 넣을 금액. 열쇠는 ``obligation``(항목→금액),
        ``asset``(항목→(DB, 국민연금전환금)), ``opening``·``closing``(둘의 짝),
        ``breakdown``(분류→금액), ``extras``(항목→금액). 주면 예시 대신 이것을
        적는다. 검산줄이 0 이 되도록 **부르는 쪽이** 기말을 역산해 넘겨야 한다.
    """
    filled = filled or numbers is not None
    numbers = numbers or {}
    obligation_of = numbers.get("obligation")
    asset_of = numbers.get("asset")

    ws = wb.create_sheet("사외적립자산", 3)
    for column, width in (("A", 4), ("B", 34), ("C", 18), ("D", 18),
                          ("E", 18), ("F", 52)):
        ws.column_dimensions[column].width = width

    ink = "000000" if filled else "9C6500"
    face = "FFFFFF" if filled else "FFF2CC"

    def money(row: int, column: int, value, *, formula: bool = False):
        cell = ws.cell(row, column, value)
        cell.number_format = "#,##0"
        cell.border = BORDER
        cell.font = Font(name=FACE, size=9,
                         color="000000" if formula else ink, bold=formula)
        if not formula:
            cell.fill = PatternFill("solid", fgColor=face)
        return cell

    def head(row: int, columns: tuple[tuple[int, str], ...]) -> None:
        for column, label in columns:
            cell = ws.cell(row, column, label)
            cell.font = Font(name=FACE, size=9, bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="44546A")
            cell.border = BORDER
            cell.alignment = Alignment(horizontal="center")

    def label(row: int, sign: str, text_: str) -> None:
        ws.cell(row, 1, sign).font = Font(name=FACE, size=9, color="5B6478")
        cell = ws.cell(row, 2, text_)
        cell.font = Font(name=FACE, size=9)
        cell.border = BORDER

    ws["A1"] = "사외적립자산"
    ws["A1"].font = Font(name=FACE, size=14, bold=True, color="1F3864")
    ws["A2"] = ("신탁회사 명세서의 숫자를 그대로 옮겨 주세요. 노란 칸이 입력, "
                "굵은 칸은 자동 계산입니다. 맨 아래 '검증' 이 0 이어야 합니다.")
    ws["A2"].font = Font(name=FACE, size=9, color="5B6478")

    # 채무 변동내역
    ws.cell(4, 2, "퇴직급여추계액 변동내역").font = Font(
        name=FACE, size=11, bold=True, color="1F3864")
    ws.cell(4, 6, "회사가 계산한 추계액(K-GAAP)의 기중 증감입니다.").font = Font(
        name=FACE, size=9, color="5B6478")
    head(5, ((2, "구분"), (3, "금액")))
    row = 6
    for sign, text_, amount in OBLIGATION_ROWS:
        label(row, sign, text_)
        money(row, 3, amount if obligation_of is None else obligation_of.get(text_, 0))
        row += 1

    # 자산 변동내역
    row += 1
    ws.cell(row, 2, "사외적립자산 변동내역").font = Font(
        name=FACE, size=11, bold=True, color="1F3864")
    ws.cell(row, 6, "유출입은 모두 양수로 적으세요. 부호는 왼쪽 (+)(-) 가 정합니다.").font = (
        Font(name=FACE, size=9, color="5B6478"))
    head(row + 1, ((2, "구분"), (3, "DB퇴직연금"), (4, "국민연금전환금"), (5, "합계")))

    opening = numbers.get("opening", ASSET_OPENING)
    closing = numbers.get("closing", ASSET_CLOSING)

    opening_row = row + 2
    label(opening_row, "", "기초 잔액 (전기말)")
    money(opening_row, 3, opening[0])
    money(opening_row, 4, opening[1])
    money(opening_row, 5, f"=C{opening_row}+D{opening_row}", formula=True)

    row = opening_row + 1
    for sign, text_, db, pension in ASSET_ROWS:
        label(row, sign, text_)
        if asset_of is not None:
            db, pension = asset_of.get(text_, (0, 0))
        money(row, 3, db)
        money(row, 4, pension)
        money(row, 5, f"=C{row}+D{row}", formula=True)
        row += 1
    last_move = row - 1

    closing_row = row
    label(closing_row, "", "기말 잔액 (결산일)")
    money(closing_row, 3, closing[0])
    money(closing_row, 4, closing[1])
    money(closing_row, 5, f"=C{closing_row}+D{closing_row}", formula=True)

    verify_row = closing_row + 1
    label(verify_row, "", "검증  (기초 + 유입 − 유출 − 기말)")
    plus_last = opening_row + 4          # (+) 줄 넷: 부담금·이자수익·전입·합병
    for column in "CDE":
        cell = ws.cell(verify_row, ord(column) - 64,
                       f"={column}{opening_row}"
                       f"+SUM({column}{opening_row + 1}:{column}{plus_last})"
                       f"-SUM({column}{plus_last + 1}:{column}{last_move})"
                       f"-{column}{closing_row}")
        cell.number_format = "#,##0;[RED](#,##0)"
        cell.font = Font(name=FACE, size=9, bold=True)
        cell.border = BORDER
    ws.cell(verify_row, 6, "0 이 아니면 회사가 보낸 표 자체가 맞지 않는다는 뜻입니다.").font = (
        Font(name=FACE, size=9, color="5B6478"))

    # 세부내역
    row = verify_row + 2
    ws.cell(row, 2, "사외적립자산 세부내역").font = Font(
        name=FACE, size=11, bold=True, color="1F3864")
    ws.cell(row, 6, "기말 공정가치를 자산 종류별로. 공시(문단 142)에 그대로 실립니다.").font = (
        Font(name=FACE, size=9, color="5B6478"))
    head(row + 1, ((2, "자산 분류"), (3, "공정가치")))
    row += 2
    first_detail = row
    for name, amount in numbers.get("breakdown", ASSET_BREAKDOWN):
        cell = ws.cell(row, 2, name)
        cell.font = Font(name=FACE, size=9)
        cell.border = BORDER
        money(row, 3, amount)
        row += 1
    total = ws.cell(row, 2, "합계")
    total.font = Font(name=FACE, size=9, bold=True)
    total.border = BORDER
    money(row, 3, f"=SUM(C{first_detail}:C{row - 1})", formula=True)
    ws.cell(row, 4, f"기말 잔액(C{closing_row}+D{closing_row}) 과 같아야 합니다.").font = (
        Font(name=FACE, size=9, color="5B6478"))

    # 그 밖에 담당자가 정해 주어야 하는 숫자
    row += 2
    ws.cell(row, 2, "그 밖의 입력").font = Font(
        name=FACE, size=11, bold=True, color="1F3864")
    head(row + 1, ((2, "항목"), (3, "금액")))
    row += 2
    extras = [
        ("자산인식상한 (문단 64)", "",
         "초과적립일 때만. 환급·미래부담금 절감으로 얻을 효익의 현가. 비우면 상한 미적용"),
        ("기준일 현재 미지급 퇴직급여", 0,
         "이미 퇴직했는데 결산일까지 못 준 금액. 순확정급여부채에 더합니다"),
        ("기중 장기근속 지급액", 0, "장기급여(근속포상·휴가)로 기중에 나간 금액"),
        # '받은금액' 을 붙여 쓴다 — 프로그램이 찾는 말이 그것이라, 띄우면 못 읽는다.
        ("기중 장기근속 받은금액", 0, "전입 등으로 기중에 들어온 장기급여"),
    ]
    given = numbers.get("extras", {})
    for name, amount, note in extras:
        label(row, "", name)
        money(row, 3, given.get(name, amount))
        ws.cell(row, 4, note).font = Font(name=FACE, size=9, color="5B6478")
        row += 1




def build_workbook(
    *,
    basics: dict | None = None,
    groups: list | None = None,
    note: str = "",
    rules_filled: bool = False,
    specials: dict[str, str] | None = None,
    numbers: dict | None = None,
    actives: list[dict] | None = None,
    retirees: list[dict] | None = None,
    active_extras: tuple[str, ...] = (),
    retired_extras: tuple[str, ...] = (),
):
    """이 양식대로 된 통합문서 하나.

    빈 양식과 시험용 명부가 **같은 서식** 이어야 한다. 따로 만들면 회사에 보낸
    양식과 우리가 시험하는 명부가 서서히 갈라져, 정작 받아 본 파일에서 처음
    어긋난다. 그래서 시트를 만드는 곳은 여기 하나다.
    """
    import openpyxl

    wb = openpyxl.Workbook()
    del wb["Sheet"]
    _guide(wb)
    _basics(wb, values=basics, groups=groups, note=note)
    _rules(wb, filled=rules_filled, specials=specials)
    _assets(wb, numbers=numbers)
    _sheet(wb, "재직자명부", ACTIVE, second=SECOND_ACTIVE,
           rows=actives, extras=active_extras)
    _sheet(wb, "퇴직자명부", RETIRED, second=SECOND_RETIRED,
           rows=retirees, extras=retired_extras)
    return wb


def label_for(columns: list, aliases: dict[str, tuple[str, ...]]) -> dict[str, str]:
    """필드명 → 이 양식의 열 이름.

    :mod:`pension.layout` 의 별칭표와 맞대어 짓는다. 이름을 손으로 한 벌 더
    적어 두면 한쪽만 고쳐졌을 때 조용히 어긋난다.
    """
    from .layout import normalize_header

    known: dict[str, str] = {}
    for key, names in aliases.items():
        for name in names:
            known.setdefault(normalize_header(name), key)

    found: dict[str, str] = {}
    for _block, label, *_rest in columns:
        key = known.get(normalize_header(label))
        if key:
            found.setdefault(key, label)
    return found


def write_roster_template(path: str | Path) -> Path:
    """빈 명부 양식을 만든다."""
    path = Path(path)
    build_workbook().save(path)
    _embed_values(path)
    return path


def _embed_values(path: Path) -> None:
    """수식 칸에 계산 결과를 심는다.

    openpyxl 은 수식을 글자로만 적고 값을 남기지 않는다. 그러면 엑셀이 '제한된
    보기' 로 열었을 때 그 칸이 통째로 비어 보여, 받는 사람 눈에는 표가 깨진
    것처럼 보인다. 수식은 그대로 두므로 입력을 고치면 다시 계산된다.
    """
    import re
    import shutil
    import zipfile

    import openpyxl
    from openpyxl.utils import range_boundaries

    def value_of(ws, ref, seen):
        raw = ws[ref].value
        if isinstance(raw, str) and raw.startswith("="):
            if ref in seen:
                raise ValueError(f"순환 참조: {ref}")
            return evaluate(ws, raw[1:], seen | {ref})
        return float(raw) if isinstance(raw, (int, float)) else 0.0

    def span(ws, text, seen):
        left, right = text.split(":")
        c1, r1, c2, r2 = range_boundaries(f"{left}:{right}")
        return [value_of(ws, ws.cell(r, c).coordinate, seen)
                for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)]

    def evaluate(ws, expr, seen):
        expr = re.sub(r"SUM\(([A-Z]+\d+:[A-Z]+\d+)\)",
                      lambda m: "(" + repr(sum(span(ws, m.group(1), seen))) + ")",
                      expr)
        expr = re.sub(r"\$?([A-Z]{1,2})\$?([0-9]+)",
                      lambda m: repr(value_of(ws, m.group(1) + m.group(2), seen)),
                      expr)
        if not re.fullmatch(r"[0-9eE+\-*/.() ]+", expr):
            raise ValueError(f"풀 수 없는 수식: {expr}")
        return eval(expr)  # noqa: S307 — 숫자와 연산자만 남은 것을 확인했다

    book = openpyxl.load_workbook(path)
    wanted = {}
    for index, name in enumerate(book.sheetnames, start=1):
        ws = book[name]
        found = {}
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    number = evaluate(ws, cell.value[1:], frozenset({cell.coordinate}))
                    found[cell.coordinate] = repr(round(number, 6))
        if found:
            wanted[index] = found
    if not wanted:
        return

    backup = path.with_suffix(".orig")
    shutil.copy2(path, backup)
    try:
        with zipfile.ZipFile(backup) as source, \
                zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                data = source.read(item.filename)
                match = re.fullmatch(r"xl/worksheets/sheet(\d+)\.xml", item.filename)
                if match and int(match.group(1)) in wanted:
                    text = data.decode("utf-8")
                    for ref, value in wanted[int(match.group(1))].items():
                        pattern = (r'(<c r="%s"[^>]*>)(<f[^>]*>.*?</f>)'
                                   r'(?:<v\s*/>|<v>.*?</v>)?(</c>)' % ref)
                        text, hits = re.subn(pattern,
                                             r"\g<1>\g<2><v>" + value + r"</v>\g<3>",
                                             text, count=1)
                        if hits != 1:
                            raise ValueError(f"{item.filename} 의 {ref} 를 찾지 못했다")
                    data = text.encode("utf-8")
                target.writestr(item, data)
    finally:
        backup.unlink(missing_ok=True)
