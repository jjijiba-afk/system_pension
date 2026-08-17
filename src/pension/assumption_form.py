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
    ATTRIBUTIONS,
    BENEFIT_RULE_SHEET,
    BENEFIT_SHEET,
    DISCOUNT_SHEET,
    EXIT_CAUSE_SHEET,
    EXIT_CAUSES,
    FORMULA,
    LONGTERM_RULE_SHEET,
    LONGTERM_SHEET,
    LONGTERM_TIMINGS,
    LT_AT_MILESTONE,
    LT_VACATION,
    MORTALITY_SHEET,
    PROMOTION_SHEET,
    SALARY_SHEET,
    STATUTORY_MODE,
    WITHDRAWAL_SHEET,
    write_assumptions,
)
from .formula import Formula, FormulaError
from .jobgroup import DEFAULT_GROUPS
from .normalize import text
from .standard_rates import SALARY_BASE_UP

ALLOCATION_CHOICES: "Final[tuple[str, ...]]" = ("급여식", "근속비례")
"""확정급여채무 할당 방식 선택지."""

NRA_TIMING_CHOICES: "Final[tuple[str, ...]]" = ("연말", "도달 즉시")
"""정년 도달 시점 선택지. :data:`pension.valuation.NRA_TIMINGS` 와 같아야 한다."""

__all__ = [
    "ALLOCATION_CHOICES",
    "APPLY_CHOICES",
    "CAUSE_COLUMN_SEP",
    "EDITOR_GROUPS",
    "EXIT_CAUSE_HEADERS",
    "FORM_SHEETS",
    "NRA_TIMING_CHOICES",
    "PAYOUT_HEADERS",
    "PAYOUT_SHEET",
    "ROUNDING_UNITS",
    "ROUNDING_VALUES",
    "UNIT_LABELS",
    "cause_column",
    "cause_split_rules",
    "check_formula",
    "default_benefit_rule",
    "default_longterm_rule",
    "default_payout",
    "empty_state",
    "example_state",
    "formula_preview",
    "grid_columns",
    "merge_benefit_causes",
    "read_state",
    "split_benefit_by_cause",
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
    "Base-up 적용", "승급률 적용", "퇴직률 적용", "사망률 적용", "할당 방식",
    "정년 도달 시점",
)

#: 퇴직사유 탭의 열. 자유 입력이 아니라 정해진 자리에 넣게 한다.
EXIT_CAUSE_HEADERS: Final[tuple[str, ...]] = (
    "지급률 규정", "퇴직사유", "대체 지급률 규정", "가산 규정",
    "가산액(원)", "근속 하한(년)", "가산 귀속",
)

#: 직군별 가정 적용 여부 선택지.
APPLY_CHOICES: Final = ("반영", "미반영")

#: 지급액 반올림 단위 선택지. 화면에 보이는 글자 → 원 단위 값.
ROUNDING_UNITS: Final = ("없음", "1원", "10원", "100원", "1,000원")
ROUNDING_VALUES: Final = {"없음": 0, "1원": 1, "10원": 10, "100원": 100, "1,000원": 1000}
UNIT_LABELS: Final = {v: k for k, v in ROUNDING_VALUES.items()}

#: 표 입력 탭의 서식. 두 화면이 같은 탭 구성을 쓰도록 자료로 둔다.
#:
#: ``fixed`` 가 비면 직군 이름들이 열이 된다. 급여 표는 거기에 **추가 열**
#: 을 더 만들 수 있다(``extra``) — '사망 시 기본급 3개월분 가산' 처럼 직군이
#: 아닌 이름의 지급률이 필요한 규정이 있다.
#:
#: ``column_panel`` 은 열 머리 아래에 그 열을 **어떻게 읽을지** 를 붙인다.
#: 표만 떼어 놓고 보면 값이 누적 배수인지 구간 배수인지, 휴가 일수인지 금액인지
#: 알 수 없어 늘 다른 탭과 번갈아 봐야 했다.
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
        "note": "30일 평균임금 대비 지급배수입니다. 열마다 그 값을 어떻게 읽을지를 "
                "머리글 아래에서 고릅니다.",
        "key_choices": (), "column_panel": "benefit", "allow_extra": True,
        "extra_hint": "직군 말고 따로 이름 붙인 지급률이 필요할 때 만듭니다 "
                      "(예: 정년배수, 사망가산). [퇴직사유] 에서 그 이름을 고릅니다.",
    },
    {
        "sheet": LONGTERM_SHEET, "tab": "장기급여", "key": "근속연수", "fixed": (),
        "note": "근속 포상·장기근속휴가입니다. 열마다 표 값의 뜻(휴가 일수·배수·금액)과 "
                "언제 주는지를 머리글 아래에서 고릅니다.",
        "key_choices": (), "column_panel": "longterm", "allow_extra": True,
        "extra_hint": "한 근속연수에 성격이 다른 급여가 여럿이면 항목마다 열을 "
                      "만듭니다 (예: 10년 → 휴가 + 금 + 특별상여).",
    },
)

#: 산출가정 화면의 묶음. 탭 하나에 관련된 표를 모두 세로로 쌓는다.
#:
#: 규정이 늘 때마다 탭을 하나씩 붙였더니 열세 개가 되어, 아이패드에서 가로로
#: 밀어야 보였다. 무엇을 어디에 넣었는지도 알기 어려웠다. 화면 구성을 여기
#: 자료로 두는 이유는 탭 이름과 순서를 JS 에 흩어 두지 않기 위해서다.
#:
#: 나누는 기준은 **어디서 오는 값인가** 다. 기초율은 금리표·표준률에서 통째로
#: 불러오는 것들이고, 나머지는 회사 규정을 보고 손으로 정하는 것들이다.
EDITOR_GROUPS: Final[tuple[dict[str, Any], ...]] = (
    {
        "name": "직군",
        "note": "직군을 어떻게 나눌지와, 직군마다 무엇을 적용할지 정합니다. "
                "여기서 켜고 끈 것이 아래 기초율 표들에 그대로 걸립니다.",
        "sections": (
            {"panel": "map", "title": "직군 매핑"},
            {"panel": "payout", "title": "지급규정 (정년·근속·반올림·적용 여부)"},
            {"sheet": SALARY_SHEET, "title": "Base-up (공통 임금인상률)"},
        ),
    },
    {
        "name": "기초율",
        "note": "금리표와 표준률에서 불러오는 표들입니다. 위쪽 [표준률 불러오기] "
                "한 번으로 승급률·퇴직률·사망률이 한꺼번에 채워집니다. "
                "직군별 반영 여부는 [직군] 탭의 지급규정에서 켜고 끕니다.",
        "sections": (
            {"sheet": DISCOUNT_SHEET, "title": "할인율"},
            {"sheet": PROMOTION_SHEET, "title": "승급률"},
            {"sheet": WITHDRAWAL_SHEET, "title": "퇴직률"},
            {"sheet": MORTALITY_SHEET, "title": "사망률"},
        ),
    },
    {
        "name": "퇴직급여",
        "note": "표 한 장에 지급률과 그 값을 읽는 방법이 함께 있습니다. "
                "열 머리 아래에서 누적·누진·수식을 고르십시오.",
        "sections": (
            {"sheet": BENEFIT_SHEET, "title": "지급률"},
            {"panel": "cause", "title": "퇴직사유별 차등 (중도·사망·정년)"},
        ),
    },
    {
        "name": "장기급여",
        "note": "근속 포상·장기근속휴가입니다. 없으면 통째로 비워 두십시오. "
                "항목이 여럿이면 [항목 추가] 로 열을 만드십시오.",
        "sections": (
            {"sheet": LONGTERM_SHEET, "title": "장기급여"},
        ),
    },
)

#: 주석 줄 표시. 양식 파일이 표 아래에 설명을 적어 두므로 되읽을 때 걸러 낸다.
_NOTE_PREFIXES: Final = ("·", "*", "※", "#")


# ── 기본값 ───────────────────────────────────────────────────────

def default_payout() -> dict[str, Any]:
    """지급규정 한 직군의 기본값. 두 화면의 초기 표시가 같아야 한다."""
    return {
        "excluded": False, "min_service": "1", "nra": "60", "executive_nra": "",
        "add_age": "2", "basis": SERVICE_DAILY, "fraction": FRACTION_KEEP,
        "unit": "없음", "base_up": "반영", "promotion": "반영",
        "withdrawal": "반영", "mortality": "반영", "allocation": "급여식",
        "nra_timing": "연말",
    }


def empty_state(job_groups: list[str] | None = None) -> dict[str, Any]:
    """빈 화면의 출발점.

    아무것도 안 채운 상태에서도 **말이 되는 산출** 이 나와야 한다. 그래서
    Base-up 은 전 기간 2%, 지급률은 법정(배수 = 근속연수)으로 두고 시작한다.
    지급률 표에 값을 넣는 순간 그 값이 대신 쓰인다.
    """
    groups = [text(g) for g in (job_groups or DEFAULT_GROUPS) if text(g)]
    grids = {
        spec["sheet"]: {"key": spec["key"], "rows": [], "extra": []}
        for spec in FORM_SHEETS
    }
    grids[SALARY_SHEET]["rows"] = [[f"{years:g}", f"{rate * 100:g}%"]
                                   for years, rate in SALARY_BASE_UP]
    return {
        "job_groups": groups,
        "grids": grids,
        "payout": {group: default_payout() for group in groups},
        "benefit_rules": {group: default_benefit_rule() for group in groups},
        "longterm_rules": {group: default_longterm_rule(group) for group in groups},
        "mapping": [],
        # 중도퇴직·사망·정년퇴직의 지급률이 다를 때만 채운다. 비면 사유를
        # 가리지 않으므로 종전과 같은 산출이 나온다.
        "exit_causes": [],
    }


def default_benefit_rule() -> dict[str, Any]:
    """지급률 열 하나의 기본값."""
    return {"mode": STATUTORY_MODE, "formula": ""}


def default_longterm_rule(rule: str = "") -> dict[str, Any]:
    """장기급여 열 하나의 기본값.

    :param rule: 이 열이 붙는 규정(직군). 직군 열이면 제 이름과 같다.
    """
    return {
        "rule": rule, "kind": LT_VACATION, "escalation": "", "note": "",
        "timing": LT_AT_MILESTONE, "every": "", "accumulate": False,
        "anniversary": "",
    }


def grid_columns(state: dict[str, Any], sheet: str) -> list[str]:
    """그 표의 열 이름들 — 직군 다음에 따로 만든 열.

    급여 표는 직군 말고도 이름 붙인 열을 가질 수 있다('사망가산', '금').
    :func:`state_to_sheets` 와 화면이 같은 순서를 봐야 값이 어긋나지 않는다.
    """
    spec = next((s for s in FORM_SHEETS if s["sheet"] == sheet), None)
    if spec is None or spec["fixed"]:
        return list(spec["fixed"]) if spec else []
    groups = [text(g) for g in state.get("job_groups", []) if text(g)]
    grid = state.get("grids", {}).get(sheet, {})
    raw = [text(name) for name in grid.get("extra", []) if text(name)]
    # 규정 축만 쓰는 표. 명부 전원이 규정명을 달고 있으면 직군 열은 아무에게도
    # 닿지 않는데, 표 앞에 늘어서 있으면 "여기도 채워야 하나" 로 읽힌다.
    # 직군은 퇴직률·승급률·정년의 축이고, 지급률의 축은 규정이다.
    if grid.get("rules_only") and raw:
        return list(dict.fromkeys(raw))
    extra = [name for name in raw if name not in groups]
    return groups + list(dict.fromkeys(extra))


# ── 퇴직사유별 지급률 ────────────────────────────────────────────
#
# 엔진은 처음부터 사유별 차등을 받았지만(``퇴직사유`` 시트), 넣는 길이 2단
# 우회였다. ① [지급률] 표에 '정년배수' 같은 열을 손으로 만들고 ② [퇴직사유]
# 탭에서 그 이름을 지목해야 했다. 자료요청서 6번에 세 줄이 나란히 있는데도
# 처음 쓰는 사람은 이 경로를 찾지 못했다.
#
# 그래서 **한 번에 갈라 주는 조작** 을 둔다. 파일 서식과 엔진은 그대로다 —
# 갈라 놓은 결과가 곧 종전의 '여분 열 + 퇴직사유 줄' 이라, 다른 도구로 연
# 파일도 그대로 읽힌다.

CAUSE_COLUMN_SEP: Final = "·"
"""사유별로 가른 지급률 열 이름의 구분자(``정규직·정년``)."""


def cause_column(rule: object, cause: object) -> str:
    """그 규정·사유에 붙는 지급률 열 이름."""
    return f"{text(rule)}{CAUSE_COLUMN_SEP}{text(cause)}"


def _pad_cause(row: object) -> list[str]:
    values = [text(v) for v in list(row)[: len(EXIT_CAUSE_HEADERS)]]
    return values + [""] * (len(EXIT_CAUSE_HEADERS) - len(values))


def cause_split_rules(state: dict[str, Any]) -> list[str]:
    """지금 사유별로 갈라 놓은 지급률 규정 이름들.

    파일에서 되읽었을 때도 화면이 갈라진 상태로 뜨려면, 저장된 것만 보고
    되짚을 수 있어야 한다. 세 사유의 열이 모두 있고 퇴직사유 줄이 그 열을
    가리키고 있으면 갈라 놓은 것으로 본다.
    """
    columns = set(grid_columns(state, BENEFIT_SHEET))
    linked = {(row[0], row[1]): row[2] for row in map(_pad_cause,
                                                      state.get("exit_causes", []))}
    return [
        rule for rule in grid_columns(state, BENEFIT_SHEET)
        if CAUSE_COLUMN_SEP not in rule
        and all(cause_column(rule, cause) in columns for cause in EXIT_CAUSES)
        and all(linked.get((rule, cause)) == cause_column(rule, cause)
                for cause in EXIT_CAUSES)
    ]


def split_benefit_by_cause(state: dict[str, Any], rule: object) -> dict[str, Any]:
    """지급률 열 하나를 정년·중도·사망 세 열로 가른다.

    세 열의 방식·수식은 원래 열에서 복사해 온다 — 가르는 목적은 '셋이 다르다'
    를 적는 것이지 처음부터 다시 짜는 것이 아니다. 원래 열은 그대로 남는다:
    사유를 안 적은 경우(퇴직자 명부의 이미 확정된 급여 등)에 여전히 쓰인다.

    :raises ValueError: 지급률 표에 없는 열이거나 이미 가른 열일 때.
    """
    from copy import deepcopy

    name = text(rule)
    columns = grid_columns(state, BENEFIT_SHEET)
    if name not in columns:
        raise ValueError(f"지급률 표에 '{name}' 열이 없습니다")
    if CAUSE_COLUMN_SEP in name:
        raise ValueError(f"'{name}' 은 이미 사유별로 가른 열입니다")

    state = deepcopy(state)
    grid = state.setdefault("grids", {}).setdefault(
        BENEFIT_SHEET, {"key": "근속연수", "rows": [], "extra": []})
    extra = [text(n) for n in (grid.get("extra") or [])]
    rules = state.setdefault("benefit_rules", {})
    base = dict(rules.get(name) or default_benefit_rule())

    for cause in EXIT_CAUSES:
        column = cause_column(name, cause)
        if column not in columns and column not in extra:
            extra.append(column)
        rules.setdefault(column, dict(base))
    grid["extra"] = extra

    # 퇴직사유 줄이 이미 있으면 대체 규정만 갈아 끼운다. 가산액·근속 하한을
    # 먼저 적어 둔 회사가 있어, 줄을 새로 만들면 그 값이 사라진다.
    rows = [_pad_cause(row) for row in state.get("exit_causes", [])]
    for cause in EXIT_CAUSES:
        column = cause_column(name, cause)
        found = next((r for r in rows if r[0] == name and r[1] == cause), None)
        if found is None:
            rows.append([name, cause, column, "", "", "", ""])
        else:
            found[2] = column
    state["exit_causes"] = rows
    return state


def merge_benefit_causes(state: dict[str, Any], rule: object) -> dict[str, Any]:
    """가른 세 열을 도로 접는다. 그 열에 적은 값은 함께 사라진다."""
    from copy import deepcopy

    name = text(rule)
    columns = grid_columns(state, BENEFIT_SHEET)
    doomed = [cause_column(name, cause) for cause in EXIT_CAUSES]

    state = deepcopy(state)
    grid = state.setdefault("grids", {}).setdefault(
        BENEFIT_SHEET, {"key": "근속연수", "rows": [], "extra": []})

    # 값 칸도 같이 들어낸다. 이름만 지우면 남은 값이 한 칸씩 옆으로 밀려
    # 엉뚱한 열의 배수가 된다 (열 0 은 근속연수라 +1).
    dead = sorted((columns.index(c) + 1 for c in doomed if c in columns),
                  reverse=True)
    rows = []
    for row in grid.get("rows") or []:
        values = list(row)
        for index in dead:
            if index < len(values):
                del values[index]
        # 근속연수만 남은 줄은 버린다. 사유별 열에 넣으려고 만든 줄이라,
        # 남겨 두면 배수 없는 빈 줄이 표에 쌓인다.
        if any(text(v) for v in values[1:]):
            rows.append(values)
    grid["rows"] = rows
    grid["extra"] = [text(n) for n in (grid.get("extra") or [])
                     if text(n) not in doomed]
    for column in doomed:
        state.get("benefit_rules", {}).pop(column, None)

    # 퇴직사유 줄은 대체 규정만 비운다. 가산액 같은 것을 같이 적었으면 그
    # 규정은 여전히 유효하므로 줄째로 지우면 안 된다.
    kept = []
    for row in map(_pad_cause, state.get("exit_causes", [])):
        if row[0] == name and row[2] in doomed:
            row[2] = ""
            if not any(row[2:]):
                continue
        kept.append(row)
    state["exit_causes"] = kept
    return state


def example_state(job_groups: list[str] | None = None) -> dict[str, Any]:
    """흔한 값 한 벌. 빈 화면에서 시작하기 어려우니 출발점을 준다."""
    state = empty_state(job_groups)
    count = len(state["job_groups"])
    state["grids"][DISCOUNT_SHEET]["rows"] = [["1", "4.5%"]]
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

    groups = [text(g) for g in state.get("job_groups", []) if text(g)]
    for column, item in state.get("longterm_rules", {}).items():
        timing = text(item.get("timing")) or LT_AT_MILESTONE
        if timing not in LONGTERM_TIMINGS:
            found.append(
                f"'{column}' 장기급여의 지급시점 '{timing}' 을(를) 알 수 없습니다 "
                f"({' / '.join(LONGTERM_TIMINGS)} 중 하나)"
            )
        # 따로 만든 열은 어느 직군의 급여인지 적혀 있어야 한다. 비면 그 열이
        # 아무에게도 걸리지 않아 조용히 빠진다.
        rule = text(item.get("rule")) or column
        if column not in groups and rule not in groups:
            found.append(
                f"장기급여 '{column}' 열이 어느 직군의 급여인지 정해지지 않았습니다"
            )

    seen: set[tuple[str, str]] = set()
    for row in _cause_rows(state):
        rule, cause = row[0], row[1]
        if cause not in EXIT_CAUSES:
            found.append(
                f"'{rule}' 의 퇴직사유 '{cause}' 을(를) 알 수 없습니다 "
                f"({' / '.join(EXIT_CAUSES)} 중 하나)"
            )
            continue
        basis = row[6]
        if basis and basis not in ATTRIBUTIONS:
            found.append(
                f"'{rule} / {cause}' 의 가산 귀속 '{basis}' 을(를) 알 수 없습니다 "
                f"({' / '.join(ATTRIBUTIONS)} 중 하나)"
            )
        if (rule, cause) in seen:
            found.append(f"'{rule} / {cause}' 이 두 번 적혀 있습니다")
        seen.add((rule, cause))
    return found


def _cause_rows(state: dict[str, Any]) -> list[list[str]]:
    """퇴직사유 탭에서 값이 든 줄만. 일곱 칸으로 길이를 맞춘다."""
    rows: list[list[str]] = []
    for row in state.get("exit_causes", []):
        values = [text(v) for v in list(row)[: len(EXIT_CAUSE_HEADERS)]]
        values += [""] * (len(EXIT_CAUSE_HEADERS) - len(values))
        if not values[0]:
            continue
        # 규정과 사유만 고르고 아무 값도 안 넣은 줄은 규정이 아니다.
        if not any(values[2:]):
            continue
        rows.append(values)
    return rows


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
    sheets: dict[str, tuple[list[str], list[list[Any]]]] = {}
    for spec in FORM_SHEETS:
        item = state.get("grids", {}).get(spec["sheet"], {})
        key = text(item.get("key")) or spec["key"]
        headers = [key, *grid_columns(state, spec["sheet"])]
        rows = [
            [_parse_cell(text(value)) for value in row[: len(headers)]]
            for row in item.get("rows", [])
            if any(text(value) for value in row)
        ]
        sheets[spec["sheet"]] = (headers, rows)

    # 지급률 방식은 열마다 하나씩. 열 순서를 따라야 화면과 파일이 같은 것을
    # 가리킨다.
    benefit = state.get("benefit_rules", {})
    rules = {
        column: (
            text(benefit.get(column, {}).get("mode")) or STATUTORY_MODE,
            text(benefit.get(column, {}).get("formula")),
        )
        for column in grid_columns(state, BENEFIT_SHEET)
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
            text(item.get("allocation")) or "급여식",
            text(item.get("nra_timing")) or "연말",
        ])
    sheets[PAYOUT_SHEET] = (list(PAYOUT_HEADERS), rows_out)

    sheets[EXIT_CAUSE_SHEET] = (
        list(EXIT_CAUSE_HEADERS),
        [
            [row[0], row[1], row[2], row[3],
             _parse_cell(row[4]), _parse_cell(row[5]), row[6]]
            for row in _cause_rows(state)
        ],
    )

    # 장기급여규정 시트. 표의 열 하나가 곧 지급 항목 하나다. 직군 이름 그대로인
    # 열은 그 직군의 기본 항목이고, 따로 이름 붙인 열은 ``rule`` 이 가리키는
    # 직군에 얹힌다('정규직'에 붙은 '금').
    entries = state.get("longterm_rules", {})
    longterm: list[list[Any]] = []
    for column in grid_columns(state, LONGTERM_SHEET):
        item = entries.get(column, {})
        rule = text(item.get("rule")) or column
        longterm.append([
            rule,
            text(item.get("kind")) or LT_VACATION,
            _parse_escalation(text(item.get("escalation"))) or None,
            text(item.get("note")),
            "" if column == rule else column,
            text(item.get("timing")) or LT_AT_MILESTONE,
            _as_float(item.get("every"), 0.0) or None,
            "Y" if item.get("accumulate") else "",
            text(item.get("anniversary")),
        ])
    return sheets, rules, longterm


def write_state(state: dict[str, Any], path: str | Path) -> Path:
    """state 를 기초율 워크북으로 쓴다. 저장할 수 없는 상태면 ``ValueError``."""
    problems = state_problems(state)
    if problems:
        raise ValueError("\n".join(problems))
    sheets, rules, longterm = state_to_sheets(state)
    return write_assumptions(Path(path), sheets, rules, longterm)


# ── 워크북 → state ───────────────────────────────────────────────

def raw_table_state(path: str | Path, *, size: object = "",
                    job_groups: list[str] | None = None) -> dict[str, Any]:
    """고시된 **표준률 원표** 워크북을 state 로.

    원표는 이 프로그램의 기초율 서식과 모양이 다르다 — 중도퇴직률·승급률·
    사망률이 각각 한 시트이고, 열이 ``No · 연령 · 300인↓ · 300인↑`` 다.
    표준률이 새로 고시될 때 오는 파일이 그 모양이므로, 사람이 288개 숫자를
    옮겨 적게 하는 대신 그대로 읽는다.

    :param size: 승급률·중도퇴직률에 쓸 사업장 규모. 원표는 두 규모를 한 표에
        담고 있어 어느 열을 쓸지 여기서 정한다.
    """
    from .standard_rates import (
        MORTALITY_HINT,
        PROMOTION_HINT,
        WITHDRAWAL_HINT,
        normalize_size,
        read_raw_workbook,
    )

    tables = read_raw_workbook(path)
    chosen = normalize_size(size)
    state = empty_state(job_groups)
    count = len(state["job_groups"])

    def spread(rows: list[list[float]]) -> list[list[str]]:
        """한 열짜리 표를 직군 수만큼 벌린다 — 표준률은 직군을 가리지 않는다."""
        return [[f"{age:g}", *[f"{rate:.6f}"] * count] for age, rate in rows]

    for hint, sheet in ((WITHDRAWAL_HINT, WITHDRAWAL_SHEET),
                        (PROMOTION_HINT, PROMOTION_SHEET)):
        found = tables.get(hint)
        if not found:
            continue
        # 규모 열이 하나뿐인 파일(한쪽만 떼어 온 것)이면 그것을 쓴다.
        rows = found.get(chosen) or next(iter(found.values()))
        state["grids"][sheet]["rows"] = spread(rows)

    mortality = tables.get(MORTALITY_HINT)
    if mortality:
        male = dict(mortality.get("남자", []))
        female = dict(mortality.get("여자", []))
        state["grids"][MORTALITY_SHEET]["rows"] = [
            [f"{age:g}", f"{male.get(age, 0.0):.6f}", f"{female.get(age, 0.0):.6f}"]
            for age in sorted(set(male) | set(female))
        ]
    state["size"] = chosen
    return state


def looks_like_raw_table(path: str | Path) -> bool:
    """표준률 원표 서식인지. 기초율 시트가 하나도 없을 때만 참이다."""
    from .workbook import open_workbook

    wb = open_workbook(path)
    try:
        names = set(wb.sheetnames)
    finally:
        wb.close()
    if any(spec["sheet"] in names for spec in FORM_SHEETS):
        return False
    return any(hint in name for name in names
               for hint in ("퇴직률", "승급률", "사망률"))


def read_state(path: str | Path, *, size: object = "") -> dict[str, Any]:
    """기초율 워크북을 state 로 되읽는다.

    엑셀에서 고친 파일을 화면으로 다시 불러오는 통로이므로, 시트가 빠져 있어도
    죽지 않고 빈 표로 둔다. 고시된 표준률 원표 서식이면 그쪽으로 읽는다.
    """
    from .workbook import open_workbook

    if looks_like_raw_table(path):
        return raw_table_state(path, size=size)

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

            # 급여 표는 직군 말고도 이름 붙인 열을 가질 수 있다. 파일의 열
            # 순서가 화면 순서와 다를 수 있으므로 **이름으로** 맞춰 읽는다 —
            # 자리로 읽으면 정규직 배수가 사망가산 칸에 들어간다.
            file_columns = [
                text(ws.cell(1, c).value) for c in range(1, ws.max_column + 1)
            ]
            if spec["fixed"]:
                targets = list(spec["fixed"])
            else:
                grid["extra"] = [
                    name for name in file_columns[1:]
                    if name and name not in state["job_groups"]
                ]
                # 파일에 직군 열이 하나도 없으면 규정 축만으로 쓰던 표다.
                # 직군 열을 도로 앞세워 그리면 빈 직군 열이 되살아난다.
                grid["rules_only"] = bool(grid["extra"]) and not any(
                    name in state["job_groups"] for name in file_columns[1:] if name
                )
                targets = grid_columns(state, spec["sheet"])
            at = {name: pos for pos, name in enumerate(file_columns) if name}

            rows = []
            for row in range(2, ws.max_row + 1):
                key_value = _cell_text(ws.cell(row, 1).value)
                # 표 아래에 적어 둔 설명·경고 줄은 값이 아니다. 첫 칸은 연차·연령·
                # 근속연수라 반드시 숫자다 — 숫자로 읽히지 않으면 자료가 아니다.
                # (앞글자만 보고 거르면 '[필수 확인] …' 같은 줄이 표에 섞여 들어온다.)
                if not _is_number(key_value):
                    continue
                values = [key_value]
                for position, name in enumerate(targets, start=2):
                    column = at.get(name, position - 1)
                    values.append(_cell_text(ws.cell(row, column + 1).value))
                if any(values):
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
                    "executive_nra": _cell_text(ws.cell(row, 15).value),
                    "excluded": text(ws.cell(row, 17).value).upper() in ("Y", "제외"),
                    "basis": text(ws.cell(row, 18).value) or SERVICE_DAILY,
                    "fraction": text(ws.cell(row, 19).value) or FRACTION_KEEP,
                    "unit": UNIT_LABELS.get(unit, "없음"),
                    # 빈 칸은 '반영'. 이 열이 없던 기존 파일과 동작을 맞춘다.
                    "base_up": text(ws.cell(row, 23).value) or "반영",
                    "promotion": text(ws.cell(row, 24).value) or "반영",
                    "withdrawal": text(ws.cell(row, 25).value) or "반영",
                    "mortality": text(ws.cell(row, 26).value) or "반영",
                    "allocation": text(ws.cell(row, 27).value) or "급여식",
                    "nra_timing": text(ws.cell(row, 28).value) or "연말",
                }
            # 명부 직군이 변환 직군과 다른 행이 하나라도 있으면 진짜 매핑이다.
            if any(source != target or kind for source, kind, target in mapping):
                state["mapping"] = mapping

        if LONGTERM_RULE_SHEET in wb.sheetnames:
            ws = wb[LONGTERM_RULE_SHEET]
            for row in range(2, ws.max_row + 1):
                rule = text(ws.cell(row, 1).value)
                if not rule or rule[:1] in _NOTE_PREFIXES:
                    continue
                # 항목 이름이 있으면 그것이 지급률 표의 열이고, 없으면 규정
                # 이름이 곧 열이다.
                column = _cell_text(ws.cell(row, 5).value) or rule
                state["longterm_rules"][column] = {
                    "rule": rule,
                    "kind": text(ws.cell(row, 2).value) or LT_VACATION,
                    "escalation": _percent(ws.cell(row, 3).value),
                    "note": text(ws.cell(row, 4).value),
                    "timing": text(ws.cell(row, 6).value) or LT_AT_MILESTONE,
                    "every": _cell_text(ws.cell(row, 7).value),
                    "accumulate": text(ws.cell(row, 8).value).upper() in ("Y", "예"),
                    "anniversary": _month_day(ws.cell(row, 9).value),
                }

        if EXIT_CAUSE_SHEET in wb.sheetnames:
            ws = wb[EXIT_CAUSE_SHEET]
            causes: list[list[str]] = []
            for row in range(2, ws.max_row + 1):
                name = text(ws.cell(row, 1).value)
                if not name or name[:1] in _NOTE_PREFIXES:
                    continue
                values = [
                    _cell_text(ws.cell(row, c).value)
                    for c in range(1, len(EXIT_CAUSE_HEADERS) + 1)
                ]
                if any(values[2:]):
                    causes.append(values)
            state["exit_causes"] = causes

        if BENEFIT_RULE_SHEET in wb.sheetnames:
            ws = wb[BENEFIT_RULE_SHEET]
            for row in range(2, ws.max_row + 1):
                name = text(ws.cell(row, 1).value)
                if not name or name[:1] in _NOTE_PREFIXES:
                    continue
                state["benefit_rules"][name] = {
                    "mode": text(ws.cell(row, 2).value) or STATUTORY_MODE,
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


def _is_number(token: str) -> bool:
    try:
        float(token.replace(",", ""))
    except (AttributeError, ValueError):
        return False
    return True


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


def _percent(value: object) -> str:
    """비율 셀을 화면 표기로. ``0.03`` → ``3%``."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value:
        return f"{float(value) * 100:g}%"
    return ""


def _month_day(value: object) -> str:
    """지급일 셀을 ``MM-DD`` 로. 엑셀이 날짜로 바꿔 둔 것도 받는다."""
    import datetime as _dt

    if isinstance(value, _dt.datetime):
        value = value.date()
    if isinstance(value, _dt.date):
        return f"{value.month:02d}-{value.day:02d}"
    return text(value)


def _parse_escalation(token: str) -> float:
    if token.endswith("%"):
        return _as_float(token.rstrip("%"), 0.0) / 100.0
    return _as_float(token, 0.0)
