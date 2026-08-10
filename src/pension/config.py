"""``Input`` 시트에서 산출 기준을 읽어온다.

종전 규칙 는 이 값들을 규칙 실행 때마다 ``Cells(3, 3).Value`` 처럼 직접 읽는다.
여기서는 한 번 읽어 :class:`CalculationConfig` 로 고정한 뒤 파이프라인 전체에
넘긴다.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from .dates import to_date
from .normalize import text

__all__ = ["INPUT_SHEET", "CalculationConfig", "JobGroupRule", "read_config"]

INPUT_SHEET = "Input"

#: 직군 규칙 테이블의 첫 데이터 행(``Input`` B12).
_RULE_FIRST_ROW = 12

#: 종전 규칙 가 훑는 직군 행 수 상한(``직군수`` 배열이 1 To 25 로 선언되어 있다).
_RULE_MAX_ROWS = 25


@dataclass(slots=True)
class JobGroupRule:
    """직군 하나에 대한 산출 규칙(``Input`` B~N 열 한 행)."""

    source_name: str
    """명부 직군명(재직/퇴직자명부 E열과 대조할 키). ``Input`` B열.

    빈 문자열이면 직군을 가리지 않고 :attr:`employee_type_filter` 로만 판정한다.
    """
    mapped_name: str
    """변환 직군명. ``Input`` C열.

    산출·기초율은 이 이름으로 묶인다. 여러 명부 직군을 같은 이름으로 보내면
    한 덩어리가 된다 — 실제 명부의 직군 열에 '과장/부장/계장/대표이사' 처럼
    직급이 들어오는 경우가 있어, 이를 '정규직/임원' 으로 묶어야 한다.
    반대로 '정규직' 을 '생산직/관리직/일반직' 으로 쪼갤 수도 있다.
    """
    severance_nra: int = 0
    """퇴직급여 정년연령. ``Input`` D열."""
    longterm_nra: int = 0
    """장기급여 정년연령. ``Input`` E열."""
    over_nra_add_age: int = 0
    """정년연령 초과자에게 더할 연수. ``Input`` F열."""

    employee_type_filter: str = ""
    """이 규칙을 적용할 임직원구분. 비면 임직원구분을 가리지 않는다.

    같은 직군이라도 임원과 직원의 정년·지급배수가 다른 경우가 흔하다. 실제
    명부에서 임원의 직군이 '정규직'/'과장' 으로 적혀 있어 직군만으로는 갈라낼
    수 없었다.
    """
    executive_nra: int = 0
    """임원 퇴직급여 정년연령. ``Input`` P열. 0 이면 직원과 같게 본다.

    임원은 대개 정년이 따로 없어 규정에 '없음'/'제약 無' 로 적혀 온다. 그럴 때는
    기본 정년(60세)에 초과자 가산연령을 얹어 처리한다. 직군이 아니라 **임직원
    구분** 으로 갈리므로 직군 규칙 안에 따로 둔다 — 실제 명부에서 임원의 직군이
    '정규직' 으로 적혀 있어 직군만으로는 임원을 분리할 수 없었다.
    """
    executive_over_nra_add_age: int = 0
    """임원 정년 초과자에게 더할 연수. ``Input`` Q열. 0 이면 직원 값을 쓴다."""

    service_basis: str = "일할"
    """근속기간 산정방법. ``Input`` S열. ``일할`` / ``월할`` / ``연할``."""
    service_fraction: str = "그대로"
    """근속연수 단수 처리. ``Input`` T열. ``그대로`` / ``절사`` / ``절상`` / ``반올림``."""
    benefit_rounding_unit: int = 0
    """지급액 반올림 단위(원). ``Input`` U열. 0 이면 반올림하지 않는다."""
    benefit_rounding_mode: str = "반올림"
    """지급액 반올림 방식. ``Input`` V열."""

    # ── 직군별 가정 적용 여부 ────────────────────────────────────
    # 어떤 가정을 쓸지가 직군마다 다른 경우가 실제로 있다. 임원은 정년까지
    # 근무한다고 보아 퇴직률을 적용하지 않거나, 호봉표가 없는 계약직에 승급률을
    # 주지 않거나, 임금이 계약으로 고정돼 Base-up 을 반영하지 않는 식이다.
    #
    # 미반영은 **그 가정의 요율을 0 으로 두는 것** 과 같다. 기초율 표를 직군마다
    # 0 으로 채워도 결과는 같지만, 그러면 '값이 0 인 것' 과 '적용하지 않기로 한 것'
    # 을 나중에 구별할 수 없다. 근거자료로 남으려면 의도가 드러나야 한다.
    apply_base_up: bool = True
    """Base-up(공통 임금인상률) 적용 여부."""
    apply_promotion: bool = True
    """승급률(호봉·승진 인상) 적용 여부."""
    apply_withdrawal: bool = True
    """중도퇴직률 적용 여부. 끄면 정년까지 전원 근무한다고 본다."""
    apply_mortality: bool = True
    """사망률 적용 여부."""

    excluded: bool = False
    """이 직군을 퇴직급여 산출에서 통째로 뺄지. ``Input`` R열.

    '계약직은 퇴직금 대상에서 제외' 처럼 직군 단위로 대상이 아닌 경우가 있다.
    """

    min_service_years: float = 0.0
    """퇴직급여 지급 대상이 되는 최소 근속연수(가입자격). ``Input`` O열.

    회사 규정마다 다르다 — 실제 사례에서 '근속 1년 이상'(다수)과 '근속 3년 이상'
    이 확인되었다. 근로자퇴직급여보장법 제4조 단서도 계속근로 1년 미만은 지급
    대상에서 제외한다. 0 이면 제한 없음.
    """

    # 재직자용 기초율 규정(G~L). 비면 명부 칼럼 값을 쓴다.
    severance_benefit: str = ""
    longterm_benefit: str = ""
    severance_withdrawal: str = ""
    severance_salary_increase: str = ""
    longterm_withdrawal: str = ""
    longterm_salary_increase: str = ""

    # 퇴직자용 기초율 규정(M~N). 비면 명부 칼럼 값을 쓴다.
    retired_severance_withdrawal: str = ""
    retired_longterm_withdrawal: str = ""


@dataclass(slots=True)
class CalculationConfig:
    """산출 기준 일체."""

    base_date: _dt.date
    """산출기준일. ``Input`` C3."""
    wage_check_amount: float = 0.0
    """평균임금 하한 체크금액. ``Input`` C5. 이 값보다 작으면 오류로 본다."""
    job_group_rules: list[JobGroupRule] = field(default_factory=list)

    inferred: bool = False
    """``Input`` 시트 없이 명부에서 끌어낸 설정인지. 참이면 직군 규칙이 잠정값이다."""

    min_age: int = 15
    """허용 최소 만 연령. 종전 규칙 하드코딩 값."""
    max_age: int = 100
    """허용 최대 만 연령. 종전 규칙 하드코딩 값."""

    _by_source: dict[str, tuple[int, JobGroupRule]] = field(
        default_factory=dict, init=False, repr=False
    )
    _by_pair: dict[tuple[str, str], tuple[int, JobGroupRule]] = field(
        default_factory=dict, init=False, repr=False
    )
    _by_type: dict[str, tuple[int, JobGroupRule]] = field(
        default_factory=dict, init=False, repr=False
    )
    _by_any_source: dict[str, tuple[int, JobGroupRule]] = field(
        default_factory=dict, init=False, repr=False
    )
    """임직원구분을 무시하고 직군만 본 색인. 최후 수단이다."""

    def __post_init__(self) -> None:
        self._by_source = {}
        self._by_pair = {}
        self._by_type = {}
        self._by_any_source = {}
        for index, rule in enumerate(self.job_group_rules):
            entry = (index, rule)
            if rule.source_name and rule.employee_type_filter:
                self._by_pair[(rule.source_name, rule.employee_type_filter)] = entry
            elif rule.employee_type_filter:
                self._by_type.setdefault(rule.employee_type_filter, entry)
            elif rule.source_name:
                self._by_source.setdefault(rule.source_name, entry)
            if rule.source_name:
                self._by_any_source.setdefault(rule.source_name, entry)

    def mapped_names(self) -> list[str]:
        """변환 직군명 목록(중복 제거, 등장 순서). 기초율 열 머리글이 된다."""
        names: list[str] = []
        for rule in self.job_group_rules:
            if rule.mapped_name and rule.mapped_name not in names:
                names.append(rule.mapped_name)
        return names

    @property
    def base_year(self) -> int:
        return self.base_date.year

    def referenced_rule_names(self) -> list[str]:
        """``Input`` 시트가 참조하는 기초율 규정명 목록(중복 제거, 등장 순서).

        기초율 워크북의 열 머리글은 **직군명이 아니라 이 규정명** 과 같아야 한다.
        양식 생성기가 열을 채울 때 쓴다. G~N 열이 모두 비어 있으면 명부 칼럼에서
        규정명을 받는다는 뜻이므로, 대신 변환 직군명을 돌려준다.
        """
        names: list[str] = []
        for rule in self.job_group_rules:
            for value in (
                rule.severance_benefit,
                rule.longterm_benefit,
                rule.severance_withdrawal,
                rule.severance_salary_increase,
                rule.longterm_withdrawal,
                rule.longterm_salary_increase,
                rule.retired_severance_withdrawal,
                rule.retired_longterm_withdrawal,
            ):
                if value and value not in names:
                    names.append(value)

        if names:
            return names
        return [r.mapped_name for r in self.job_group_rules if r.mapped_name]

    def find_job_group(
        self,
        source_name: object,
        employee_type: object = "",
        raw_employee_type: object = "",
    ) -> tuple[int, JobGroupRule] | None:
        """명부 직군명(과 임직원구분)으로 규칙을 찾는다. 없으면 ``None``.

        찾는 순서는 **좁은 것부터** 다.

        1. 직군 + 임직원구분 원문 (`과장` + `촉탁사원`)
        2. 직군 + 정규화된 임직원구분 (`과장` + `직원`)
        3. 임직원구분 원문만 (직군 무관)
        4. 정규화된 임직원구분만
        5. 직군만 지정된 규칙
        6. 직군이 같은 아무 규칙 (임직원구분을 무시)

        마지막 단계는 명부의 임직원구분 칸이 비었을 때를 위한 것이다. 규정이
        전부 `직군+임직원구분` 짝으로 적혀 있으면 5 번에서 걸리지 않아 그 사람만
        규칙 없이 남는다. 직군이라도 맞는 규칙을 쓰는 편이 기본값으로 떨어지는
        것보다 낫다.

        종전 규칙 는 직군 완전일치 하나만 보았다. 임원의 직군이 '정규직' 으로 적혀
        오는 명부가 있어 임원을 갈라낼 수 없었다.

        원문까지 보는 이유는 정규화가 `임원`/`직원` 둘로만 줄이기 때문이다.
        같은 `과장` 이라도 `정규사원` 과 `촉탁사원` 은 계약 형태가 달라 퇴직률과
        지급률이 갈리는데, 정규화 결과만으로는 둘 다 `직원` 이라 구분이 사라진다.
        """
        job = text(source_name)
        kind = text(employee_type)
        raw = text(raw_employee_type)

        for token in (raw, kind):
            if not token:
                continue
            found = self._by_pair.get((job, token))
            if found is not None:
                return found
        for token in (raw, kind):
            if not token:
                continue
            found = self._by_type.get(token)
            if found is not None:
                return found
        found = self._by_source.get(job)
        if found is not None:
            return found
        return self._by_any_source.get(job)


#: 적용 여부 칸에서 '미반영' 으로 읽을 표기. 비어 있으면 **반영** 이 기본이다 —
#: 기존 파일에는 이 칸이 아예 없으므로 빈 값이 곧 종전 동작이어야 한다.
_NOT_APPLIED = frozenset({"미반영", "미적용", "N", "NO", "아니오", "아니요", "0", "FALSE", "X", "제외"})


def _apply(value: object) -> bool:
    """직군별 가정 적용 여부 칸. 비면 적용한다."""
    token = text(value).upper()
    return token not in _NOT_APPLIED if token else True


PAYOUT_SHEET = "지급규정"
"""가정 입력 화면이 저장하는 지급규정 시트. ``Input`` 과 열 배치가 같다."""


def read_payout_rules(workbook) -> list[JobGroupRule]:
    """기초율 워크북의 ``지급규정`` 시트를 직군 규칙으로 읽는다.

    명부의 ``Input`` 시트가 비어 있거나 없을 때, 가정 입력 화면에서 저장한
    규정을 그대로 쓸 수 있게 한다. 머리글 한 줄 아래부터 데이터다.

    표 아래에 적힌 안내문은 건너뛴다. 첫 칸에 글자가 있다는 것만으로 규칙으로
    보면 '· 명부의 Input 시트보다 우선합니다' 같은 주석이 직군 이름이 되어,
    기초율 열 머리글에 통째로 끼어든다.
    """
    from .workbook import find_sheet

    ws = find_sheet(workbook, PAYOUT_SHEET)
    if ws is None:
        return []

    rules: list[JobGroupRule] = []
    for row in range(2, ws.max_row + 1):
        source_name = text(ws.cell(row, 1).value)
        if not source_name or source_name.startswith(("·", "*", "※", "#")):
            continue
        # 정년연령 칸이 숫자가 아니면 규칙 행이 아니다. 안내문은 한 칸만 채운다.
        if not isinstance(ws.cell(row, 3).value, (int, float)):
            continue
        rules.append(
            JobGroupRule(
                source_name=source_name,
                mapped_name=text(ws.cell(row, 2).value) or source_name,
                severance_nra=_int(ws.cell(row, 3).value),
                longterm_nra=_int(ws.cell(row, 4).value),
                over_nra_add_age=_int(ws.cell(row, 5).value),
                severance_benefit=text(ws.cell(row, 6).value),
                longterm_benefit=text(ws.cell(row, 7).value),
                severance_withdrawal=text(ws.cell(row, 8).value),
                severance_salary_increase=text(ws.cell(row, 9).value),
                longterm_withdrawal=text(ws.cell(row, 10).value),
                longterm_salary_increase=text(ws.cell(row, 11).value),
                retired_severance_withdrawal=text(ws.cell(row, 12).value),
                retired_longterm_withdrawal=text(ws.cell(row, 13).value),
                min_service_years=_float(ws.cell(row, 14).value),
                executive_nra=_int(ws.cell(row, 15).value),
                executive_over_nra_add_age=_int(ws.cell(row, 16).value),
                excluded=text(ws.cell(row, 17).value).upper() in ("Y", "제외", "TRUE", "1"),
                service_basis=text(ws.cell(row, 18).value) or "일할",
                service_fraction=text(ws.cell(row, 19).value) or "그대로",
                benefit_rounding_unit=_int(ws.cell(row, 20).value),
                benefit_rounding_mode=text(ws.cell(row, 21).value) or "반올림",
                employee_type_filter=text(ws.cell(row, 22).value),
                apply_base_up=_apply(ws.cell(row, 23).value),
                apply_promotion=_apply(ws.cell(row, 24).value),
                apply_withdrawal=_apply(ws.cell(row, 25).value),
                apply_mortality=_apply(ws.cell(row, 26).value),
            )
        )
    return rules


def read_config(workbook, sheet_name: str = INPUT_SHEET, *,
                base_date: _dt.date | None = None) -> CalculationConfig:
    """``Input`` 시트를 :class:`CalculationConfig` 로 읽는다.

    ``Input`` 시트가 없는 통합문서(자료요청서 원본 등)면 명부에서 산출기준일과
    직군을 끌어내 최소한의 설정을 만든다. 그때 직군 규칙은 정년 60세·가산 2년의
    잠정값이므로, 반드시 확인하고 확정해야 한다.

    :param workbook: ``openpyxl`` 워크북(``data_only=True`` 로 연 것).
    :param base_date: 화면에서 지정한 산출기준일. 명부 어디에도 기준일이 없을 때
        **이것이 있으면 그것으로 읽는다.** 예전에는 화면에 날짜를 넣어 두고도
        이 함수가 먼저 터져서, 담당자는 넣은 값이 왜 무시되는지 알 수 없었다.
    :raises ValueError: 산출기준일을 어디에서도 찾지 못했고 ``base_date`` 도 없을 때.
    """
    from .workbook import find_sheet

    ws = find_sheet(workbook, sheet_name)
    if ws is None:
        return infer_config(workbook, base_date=base_date)

    found = to_date(ws.cell(3, 3).value) or base_date
    if found is None:
        raise ValueError(
            f"{sheet_name}!C3 산출기준일이 비어 있습니다. "
            "화면의 [산출 기준일] 칸에 날짜를 넣어도 됩니다"
        )
    base_date = found

    raw_check = ws.cell(5, 3).value
    wage_check = float(raw_check) if isinstance(raw_check, (int, float)) else 0.0

    rules: list[JobGroupRule] = []
    for offset in range(_RULE_MAX_ROWS):
        row = _RULE_FIRST_ROW + offset
        # 종전 규칙 는 CountA(C12:C26) 으로 행 수를 세지만, 중간에 빈 행이 있으면
        # 뒤쪽 직군을 통째로 놓친다. 여기서는 전 구간을 훑고 빈 행만 건너뛴다.
        source_name = text(ws.cell(row, 2).value)
        mapped_name = text(ws.cell(row, 3).value)
        if not source_name and not mapped_name:
            continue

        rules.append(
            JobGroupRule(
                source_name=source_name,
                mapped_name=mapped_name or source_name,
                severance_nra=_int(ws.cell(row, 4).value),
                longterm_nra=_int(ws.cell(row, 5).value),
                over_nra_add_age=_int(ws.cell(row, 6).value),
                severance_benefit=text(ws.cell(row, 7).value),
                longterm_benefit=text(ws.cell(row, 8).value),
                severance_withdrawal=text(ws.cell(row, 9).value),
                severance_salary_increase=text(ws.cell(row, 10).value),
                longterm_withdrawal=text(ws.cell(row, 11).value),
                longterm_salary_increase=text(ws.cell(row, 12).value),
                retired_severance_withdrawal=text(ws.cell(row, 13).value),
                retired_longterm_withdrawal=text(ws.cell(row, 14).value),
                min_service_years=_float(ws.cell(row, 15).value),
                executive_nra=_int(ws.cell(row, 16).value),
                executive_over_nra_add_age=_int(ws.cell(row, 17).value),
                excluded=text(ws.cell(row, 18).value).upper() in ("Y", "제외", "TRUE", "1"),
                service_basis=text(ws.cell(row, 19).value) or "일할",
                service_fraction=text(ws.cell(row, 20).value) or "그대로",
                benefit_rounding_unit=_int(ws.cell(row, 21).value),
                benefit_rounding_mode=text(ws.cell(row, 22).value) or "반올림",
                employee_type_filter=text(ws.cell(row, 23).value),
            )
        )

    return CalculationConfig(
        base_date=base_date,
        wage_check_amount=wage_check,
        job_group_rules=rules,
    )


def _float(value: object) -> float:
    if isinstance(value, bool) or value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(text(value))
    except ValueError:
        return 0.0


def _int(value: object) -> int:
    if isinstance(value, bool) or value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    token = text(value)
    try:
        return int(float(token))
    except ValueError:
        return 0


# ────────────────────────────────────────────────────────────────
# Input 시트가 없는 통합문서
# ────────────────────────────────────────────────────────────────

DEFAULT_NRA = 60
"""직군 규칙을 명부에서 끌어낼 때 쓰는 잠정 정년연령."""

DEFAULT_OVER_NRA_ADD = 2
"""정년을 이미 넘긴 사람에게 더할 잠정 연수."""


def infer_config(workbook, *, base_date: _dt.date | None = None) -> CalculationConfig:
    """``Input`` 시트 없이 명부만 있는 통합문서에서 설정을 끌어낸다.

    산출기준일은 재직자명부의 '작성기준일' 칸에서, 직군은 명부에 실제로 나오는
    값에서 모은다. 정년·가산연수는 알 길이 없으므로 잠정값을 넣는다.
    """
    from .layout import ACTIVE_HEADER_ALIASES, find_data_start, find_header_row, normalize_header
    from .workbook import find_sheet

    ws = find_sheet(workbook, "재직자명부", "2)재직자명부", "재직자")
    if ws is None:
        raise ValueError(
            "Input 시트도 재직자명부도 없어 산출기준일을 정할 수 없습니다"
        )

    base_date = _find_base_date(ws) or base_date
    if base_date is None:
        raise ValueError(
            "산출기준일을 찾지 못했습니다. 화면의 [산출 기준일] 칸에 날짜를 넣거나, "
            "Input 시트 C3 또는 재직자명부의 '작성기준일' 칸을 채우세요"
        )

    header_row = find_header_row(ws, ACTIVE_HEADER_ALIASES)
    job_col = 5
    if header_row:
        for col in range(1, ws.max_column + 1):
            if normalize_header(ws.cell(header_row, col).value) == "직군":
                job_col = col
                break

    start = find_data_start(ws, header_row) if header_row else 26
    names: list[str] = []
    for row in range(start, ws.max_row + 1):
        name = text(ws.cell(row, job_col).value)
        if name and name not in names:
            names.append(name)

    rules = [
        JobGroupRule(
            source_name=name,
            mapped_name=name,
            severance_nra=DEFAULT_NRA,
            longterm_nra=DEFAULT_NRA,
            over_nra_add_age=DEFAULT_OVER_NRA_ADD,
        )
        for name in names
    ]
    return CalculationConfig(base_date=base_date, job_group_rules=rules, inferred=True)


def _find_base_date(ws) -> _dt.date | None:
    """재직자명부 위쪽에서 '작성기준일' 이 적힌 칸을 찾는다."""
    for row in range(1, min(ws.max_row, 30) + 1):
        for col in range(1, min(ws.max_column, 12) + 1):
            label = text(ws.cell(row, col).value).replace(" ", "")
            if label in ("작성기준일", "산출기준일", "평가기준일"):
                for offset in range(1, 4):
                    found = to_date(ws.cell(row, col + offset).value)
                    if found is not None:
                        return found
    return None
