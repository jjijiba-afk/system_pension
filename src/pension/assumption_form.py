"""산출 가정 입력값(state) ↔ 기초율 워크북 변환.

가정 입력 화면이 둘이다 — PC 의 tkinter 편집기와 아이패드 웹앱. 두 화면이
각자 워크북을 만들면 서식이 조금씩 어긋나는 순간 **같은 입력이 화면에 따라
다른 채무** 를 낳는다. 그래서 화면과 무관한 자료구조(state)를 하나 정해 두고,
state ↔ 워크북 변환을 이 모듈 한 곳에서만 한다. 화면은 위젯 값을 state 로
모으는 일만 맡는다.

state 는 JSON 으로 그대로 직렬화되는 평범한 dict 다::

    {
      "job_groups": ["정규직", "계약직", "임원"],
      "grids": {시트명: {"key": "연령", "rows": [["20", "0.15", …], …]}, …},
      "payout": {직군: {"nra": "60", "base_up": "반영", …}, …},
      "benefit_rules": {직군: {"mode": "누적", "formula": ""}, …},
      "longterm_rules": {직군: {"kind": "휴가", "escalation": "", "note": ""}, …},
      "mapping": [[명부직군, 임직원구분, 변환직군], …],
    }

칸 값은 전부 **문자열** 이다. ``4.5%`` 는 저장할 때 0.045 로 바뀐다 — 화면에
보이는 그대로를 들고 다니다가 파일로 나갈 때만 숫자로 해석해야, 담당자가 친
값이 화면을 오가며 망가지지 않는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from .actuarial import FRACTION_HALF, FRACTION_KEEP, SERVICE_DAILY
from .assumptions import (
    BENEFIT_RULE_SHEET,
    BENEFIT_SHEET,
    CUMULATIVE,
    DISCOUNT_SHEET,
    FORMULA,
    LONGTERM_RULE_SHEET,
    LONGTERM_SHEET,
    LT_VACATION,
    MORTALITY_SHEET,
    PROMOTION_SHEET,
    SALARY_SHEET,
    WITHDRAWAL_SHEET,
    write_assumptions,
)
from .formula import Formula, FormulaError
from .jobgroup import DEFAULT_GROUPS
from .normalize import text

__all__ = [
    "APPLY_CHOICES",
    "FORM_SHEETS",
    "PAYOUT_HEADERS",
    "PAYOUT_SHEET",
    "ROUNDING_UNITS",
    "ROUNDING_VALUES",
    "UNIT_LABELS",
    "check_formula",
    "default_payout",
    "empty_state",
    "example_state",
    "formula_preview",
    "read_state",
    "state_problems",
    "state_to_sheets",
    "write_state",
]

#: 지급규정을 담는 시트. ``Input`` 시트와 열 배치가 같아 그대로 읽힌다.
PAYOUT_SHEET: Final = "지급규정"
PAYOUT_HEADERS: Final[tuple[str, ...]] = (
    "명부직군", "변환직군명", "퇴직급여 정년연령", "장기급여 정년연령",
    "정년초과 가산연령", "퇴직급여 지급률 규정", "장기급여 지급률 규정",
    "퇴직급여 퇴직률 규정", "퇴직급여 승급률 규정", "장기급여 퇴직률 규정",
    "장기급여 승급률 규정", "퇴직자 퇴직급여 퇴직률 규정", "퇴직자 장기급여 퇴직률 규정",
    "가입자격(최소근속)", "임원 정년연령", "임원 정년초과 가산연령", "산출 제외",
    "근속 산정방법", "단수 처리", "지급액 반올림 단위", "반올림 방식", "임직원구분",
    "Base-up 적용", "승급률 적용", "퇴직률 적용", "사망률 적용",
)

#: 직군별 가정 적용 여부 선택지.
APPLY_CHOICES: Final = ("반영", "미반영")

#: 지급액 반올림 단위 선택지. 화면에 보이는 글자 → 원 단위 값.
ROUNDING_UNITS: Final = ("없음", "1원", "10원", "100원", "1,000원")
ROUNDING_VALUES: Final = {"없음": 0, "1원": 1, "10원": 10, "100원": 100, "1,000원": 1000}
UNIT_LABELS: Final = {v: k for k, v in ROUNDING_VALUES.items()}

#: 표 입력 탭의 서식. 두 화면이 같은 탭 구성을 쓰도록 자료로 둔다.
#: ``fixed`` 가 비면 직군 이름들이 열이 된다.
FORM_SHEETS: Final[tuple[dict[str, Any], ...]] = (
    {
        "sheet": DISCOUNT_SHEET, "tab": "할인율", "key": "연차", "fixed": ("할인율",),
        "note": "한 줄만 넣으면 전 기간 단일 할인율입니다. 여러 줄이면 각 연차의 "
                "현물이자율(spot)로 보고, 수익률곡선기법으로 단일할인율을 함께 구합니다.",
        "key_choices": (),
    },
    {
        "sheet": SALARY_SHEET, "tab": "Base-up", "key": "연차", "fixed": ("Base-up 상승률",),
        "note": "승진·승급을 제외한 공통 임금인상률입니다. 마지막 줄의 값이 그 이후 "
                "전 기간에 적용됩니다.",
        "key_choices": (),
    },
    {
        "sheet": PROMOTION_SHEET, "tab": "승급률", "key": "연령", "fixed": (),
        "note": "승진·호봉 승급에 따른 인상률입니다. Base-up 과 더해져 총 임금상승률이 "
                "됩니다.",
        "key_choices": ("연령", "근속"),
    },
    {
        "sheet": WITHDRAWAL_SHEET, "tab": "퇴직률", "key": "연령", "fixed": (),
        "note": "사망을 제외한 연간 중도퇴직률입니다.",
        "key_choices": ("연령", "근속"),
    },
    {
        "sheet": MORTALITY_SHEET, "tab": "사망률", "key": "연령", "fixed": ("남자", "여자"),
        "note": "연간 사망률 qx 입니다. 사용한 경험생명표의 출처를 비고에 남겨 두세요.",
        "key_choices": (),
    },
    {
        "sheet": BENEFIT_SHEET, "tab": "지급률", "key": "근속연수", "fixed": (),
        "note": "30일 평균임금 대비 지급배수입니다. 값의 의미는 '지급률 규정' 탭의 "
                "방식에 따라 달라집니다.",
        "key_choices": (),
    },
    {
        "sheet": LONGTERM_SHEET, "tab": "장기급여", "key": "근속연수", "fixed": (),
        "note": "근속 포상·장기근속휴가의 지급일수입니다(일 기본급 × 일수).",
        "key_choices": (),
    },
)

#: 주석 줄 표시. 양식 파일이 표 아래에 설명을 적어 두므로 되읽을 때 걸러 낸다.
_NOTE_PREFIXES: Final = ("·", "*", "※", "#")


# ── 기본값 ───────────────────────────────────────────────────────

def default_payout() -> dict[str, Any]:
    """지급규정 한 직군의 기본값. 두 화면의 초기 표시가 같아야 한다."""
    return {
        "excluded": False, "min_service": "1", "nra": "60", "executive_nra": "60",
        "add_age": "2", "basis": SERVICE_DAILY, "fraction": FRACTION_KEEP,
        "unit": "없음", "base_up": "반영", "promotion": "반영",
        "withdrawal": "반영", "mortality": "반영",
    }


def empty_state(job_groups: list[str] | None = None) -> dict[str, Any]:
    groups = [text(g) for g in (job_groups or DEFAULT_GROUPS) if text(g)]
    return {
        "job_groups": groups,
        "grids": {
            spec["sheet"]: {"key": spec["key"], "rows": []} for spec in FORM_SHEETS
        },
        "payout": {group: default_payout() for group in groups},
        "benefit_rules": {
            group: {"mode": CUMULATIVE, "formula": ""} for group in groups
        },
        "longterm_rules": {
            group: {"kind": LT_VACATION, "escalation": "", "note": ""}
            for group in groups
        },
        "mapping": [],
    }


def example_state(job_groups: list[str] | None = None) -> dict[str, Any]:
    """흔한 값 한 벌. 빈 화면에서 시작하기 어려우니 출발점을 준다."""
    state = empty_state(job_groups)
    count = len(state["job_groups"])
    state["grids"][DISCOUNT_SHEET]["rows"] = [["1", "4.5%"]]
    state["grids"][SALARY_SHEET]["rows"] = [["1", "3.0%"], ["6", "2.5%"]]
    state["grids"][PROMOTION_SHEET]["rows"] = [
        ["20", *["2.0%"] * count], ["40", *["1.0%"] * count], ["55", *["0.0%"] * count]
    ]
    state["grids"][WITHDRAWAL_SHEET]["rows"] = [
        ["20", *["15%"] * count], ["35", *["6%"] * count], ["50", *["2%"] * count]
    ]
    state["grids"][MORTALITY_SHEET]["rows"] = [
        ["20", "0.0004", "0.0002"], ["40", "0.0012", "0.0006"], ["60", "0.006", "0.0025"]
    ]
    state["grids"][BENEFIT_SHEET]["rows"] = [
        ["1", *["1.0"] * count], ["10", *["10.0"] * count], ["20", *["20.0"] * count]
    ]
    state["grids"][LONGTERM_SHEET]["rows"] = [
        ["10", *["10"] * count], ["20", *["20"] * count], ["30", *["30"] * count]
    ]
    return state


# ── 검증 ─────────────────────────────────────────────────────────

def check_formula(source: str) -> str:
    """수식 오류 메시지. 문제없으면 빈 문자열."""
    source = text(source)
    if not source:
        return "수식이 비어 있습니다"
    try:
        Formula(source)
    except FormulaError as exc:
        return _short(exc)
    return ""


def state_problems(state: dict[str, Any]) -> list[str]:
    """저장 전에 고쳐야 하는 문제들. 비면 저장해도 된다."""
    found: list[str] = []
    for group, item in state.get("benefit_rules", {}).items():
        if item.get("mode") != FORMULA:
            continue
        source = text(item.get("formula"))
        if not source:
            found.append(f"'{group}' 은 수식 방식인데 수식이 비어 있습니다")
            continue
        error = check_formula(source)
        if error:
            found.append(f"'{group}' 수식: {error}")

    discount = state.get("grids", {}).get(DISCOUNT_SHEET, {}).get("rows", [])
    if not any(any(text(v) for v in row) for row in discount):
        found.append("할인율은 반드시 입력해야 합니다. 없으면 채무를 산출할 수 없습니다")
    return found


def formula_preview(
    formulas: dict[str, str], services: range = range(1, 31)
) -> dict[str, Any]:
    """근속연수별 배수 표. 규정이 의도대로 도는지 저장 전에 눈으로 본다.

    연령 40세 · 정년 60세 · 제도 DB 기준이다.
    """
    compiled: dict[str, Formula | FormulaError] = {}
    for group, source in formulas.items():
        try:
            compiled[group] = Formula(source)
        except FormulaError as exc:
            compiled[group] = exc

    rows = []
    for service in services:
        values: list[str] = [str(service)]
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
        rows.append(values)
    return {"columns": ["근속", *formulas], "rows": rows}


# ── state → 워크북 ───────────────────────────────────────────────

def state_to_sheets(state: dict[str, Any]) -> tuple[
    dict[str, tuple[list[str], list[list[Any]]]],
    dict[str, tuple[str, str]],
    dict[str, tuple[str, float, str]],
]:
    """state 를 :func:`~pension.assumptions.write_assumptions` 입력으로 바꾼다."""
    groups = [text(g) for g in state.get("job_groups", []) if text(g)]

    sheets: dict[str, tuple[list[str], list[list[Any]]]] = {}
    for spec in FORM_SHEETS:
        item = state.get("grids", {}).get(spec["sheet"], {})
        key = text(item.get("key")) or spec["key"]
        headers = [key, *(spec["fixed"] or groups)]
        rows = [
            [_parse_cell(text(value)) for value in row[: len(headers)]]
            for row in item.get("rows", [])
            if any(text(value) for value in row)
        ]
        sheets[spec["sheet"]] = (headers, rows)

    rules = {
        group: (
            text(item.get("mode")) or CUMULATIVE,
            text(item.get("formula")),
        )
        for group, item in state.get("benefit_rules", {}).items()
    }

    # 지급규정 탭 → 'Input' 시트와 같은 배치로 한 장 더 만든다. 산출 때 그대로
    # 읽히도록 열 순서를 맞춘다.
    #
    # 한 행이 곧 하나의 조회 키다. '직군 매핑' 이 채워져 있으면 명부에서 발견된
    # (직군, 임직원구분) 조합마다 한 행씩 쓰고, 규정 값은 배정된 묶음의 것을
    # 그대로 복사한다. 매핑이 비어 있으면 묶음 이름을 그대로 명부 직군으로
    # 본다(직군 열에 이미 정규직/계약직이 적혀 오는 명부).
    payout = {
        text(group): item for group, item in state.get("payout", {}).items()
        if text(group)
    }
    mapping = [
        (text(row[0]), text(row[1]), text(row[2]))
        for row in state.get("mapping", [])
        if len(row) >= 3 and text(row[2]) in payout
    ]
    if not mapping:
        mapping = [(group, "", group) for group in payout]

    rows_out: list[list[Any]] = []
    for source, kind, target in mapping:
        item = payout[target]
        nra = _as_int(item.get("nra"), 60)
        rows_out.append([
            source, target,
            nra, nra,
            _as_int(item.get("add_age"), 2),
            "", "", "", "", "", "", "", "",
            _as_float(item.get("min_service"), 0.0),
            _as_int(item.get("executive_nra"), 0),
            _as_int(item.get("add_age"), 2),
            "Y" if item.get("excluded") else "",
            text(item.get("basis")) or SERVICE_DAILY,
            text(item.get("fraction")) or FRACTION_KEEP,
            ROUNDING_VALUES.get(text(item.get("unit")), 0), FRACTION_HALF,
            kind,
            text(item.get("base_up")) or "반영",
            text(item.get("promotion")) or "반영",
            text(item.get("withdrawal")) or "반영",
            text(item.get("mortality")) or "반영",
        ])
    sheets[PAYOUT_SHEET] = (list(PAYOUT_HEADERS), rows_out)

    longterm = {
        group: (
            text(item.get("kind")) or LT_VACATION,
            _parse_escalation(text(item.get("escalation"))),
            text(item.get("note")),
        )
        for group, item in state.get("longterm_rules", {}).items()
    }
    return sheets, rules, longterm


def write_state(state: dict[str, Any], path: str | Path) -> Path:
    """state 를 기초율 워크북으로 쓴다. 저장할 수 없는 상태면 ``ValueError``."""
    problems = state_problems(state)
    if problems:
        raise ValueError("\n".join(problems))
    sheets, rules, longterm = state_to_sheets(state)
    return write_assumptions(Path(path), sheets, rules, longterm)


# ── 워크북 → state ───────────────────────────────────────────────

def read_state(path: str | Path) -> dict[str, Any]:
    """기초율 워크북을 state 로 되읽는다.

    엑셀에서 고친 파일을 화면으로 다시 불러오는 통로이므로, 시트가 빠져 있어도
    죽지 않고 빈 표로 둔다.
    """
    from .workbook import open_workbook

    wb = open_workbook(path)
    try:
        # 직군은 직군별 열을 쓰는 첫 시트의 머리글에서 가져온다.
        groups: list[str] = []
        for spec in FORM_SHEETS:
            if spec["fixed"] or spec["sheet"] not in wb.sheetnames:
                continue
            ws = wb[spec["sheet"]]
            groups = [
                text(ws.cell(1, c).value)
                for c in range(2, ws.max_column + 1)
                if text(ws.cell(1, c).value)
            ]
            if groups:
                break

        state = empty_state(groups or None)

        for spec in FORM_SHEETS:
            grid = state["grids"][spec["sheet"]]
            if spec["sheet"] not in wb.sheetnames:
                continue
            ws = wb[spec["sheet"]]
            if spec["key_choices"]:
                header = text(ws.cell(1, 1).value)
                grid["key"] = "근속" if "근속" in header else "연령"

            width = 1 + len(spec["fixed"] or state["job_groups"])
            rows = []
            for row in range(2, ws.max_row + 1):
                values = [_cell_text(ws.cell(row, c).value) for c in range(1, width + 1)]
                if not any(values):
                    continue
                # 양식 파일이 표 아래에 적어 두는 설명 줄은 값이 아니다.
                if values[0][:1] in _NOTE_PREFIXES:
                    continue
                rows.append(values)
            grid["rows"] = rows

        if PAYOUT_SHEET in wb.sheetnames:
            ws = wb[PAYOUT_SHEET]
            mapping: list[list[str]] = []
            for row in range(2, ws.max_row + 1):
                name = text(ws.cell(row, 1).value)
                if not name or name[:1] in _NOTE_PREFIXES:
                    continue
                # 규정 값은 변환 직군(2열) 단위로, 매핑은 행 단위로 되읽는다.
                target = text(ws.cell(row, 2).value) or name
                kind = text(ws.cell(row, 22).value)
                mapping.append([name, kind, target])
                unit = _as_int(_cell_text(ws.cell(row, 20).value), 0)
                state["payout"][target] = {
                    "nra": _cell_text(ws.cell(row, 3).value) or "60",
                    "add_age": _cell_text(ws.cell(row, 5).value) or "2",
                    "min_service": _cell_text(ws.cell(row, 14).value) or "0",
                    "executive_nra": _cell_text(ws.cell(row, 15).value) or "60",
                    "excluded": text(ws.cell(row, 17).value).upper() in ("Y", "제외"),
                    "basis": text(ws.cell(row, 18).value) or SERVICE_DAILY,
                    "fraction": text(ws.cell(row, 19).value) or FRACTION_KEEP,
                    "unit": UNIT_LABELS.get(unit, "없음"),
                    # 빈 칸은 '반영'. 이 열이 없던 기존 파일과 동작을 맞춘다.
                    "base_up": text(ws.cell(row, 23).value) or "반영",
                    "promotion": text(ws.cell(row, 24).value) or "반영",
                    "withdrawal": text(ws.cell(row, 25).value) or "반영",
                    "mortality": text(ws.cell(row, 26).value) or "반영",
                }
            # 명부 직군이 변환 직군과 다른 행이 하나라도 있으면 진짜 매핑이다.
            if any(source != target or kind for source, kind, target in mapping):
                state["mapping"] = mapping

        if LONGTERM_RULE_SHEET in wb.sheetnames:
            ws = wb[LONGTERM_RULE_SHEET]
            for row in range(2, ws.max_row + 1):
                name = text(ws.cell(row, 1).value)
                if not name or name[:1] in _NOTE_PREFIXES:
                    continue
                raw = ws.cell(row, 3).value
                state["longterm_rules"][name] = {
                    "kind": text(ws.cell(row, 2).value) or LT_VACATION,
                    "escalation": (
                        f"{float(raw) * 100:g}%"
                        if isinstance(raw, (int, float)) and raw else ""
                    ),
                    "note": text(ws.cell(row, 4).value),
                }

        if BENEFIT_RULE_SHEET in wb.sheetnames:
            ws = wb[BENEFIT_RULE_SHEET]
            for row in range(2, ws.max_row + 1):
                name = text(ws.cell(row, 1).value)
                if not name or name[:1] in _NOTE_PREFIXES:
                    continue
                state["benefit_rules"][name] = {
                    "mode": text(ws.cell(row, 2).value) or CUMULATIVE,
                    "formula": text(ws.cell(row, 3).value),
                }
        return state
    finally:
        wb.close()


# ── 값 해석 ──────────────────────────────────────────────────────

def _short(exc: Exception, limit: int = 60) -> str:
    message = str(exc).split(" — 수식:")[0]
    return message if len(message) <= limit else message[: limit - 1] + "…"


def _as_int(token: object, default: int) -> int:
    try:
        return int(float(str(token).strip()))
    except (TypeError, ValueError):
        return default


def _as_float(token: object, default: float) -> float:
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


def _parse_escalation(token: str) -> float:
    if token.endswith("%"):
        return _as_float(token.rstrip("%"), 0.0) / 100.0
    return _as_float(token, 0.0)
