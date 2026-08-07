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
    """명부 직군명(재직/퇴직자명부 E열과 대조할 키). ``Input`` B열."""
    mapped_name: str
    """변환 직군명. ``Input`` C열."""
    severance_nra: int = 0
    """퇴직급여 정년연령. ``Input`` D열."""
    longterm_nra: int = 0
    """장기급여 정년연령. ``Input`` E열."""
    over_nra_add_age: int = 0
    """정년연령 초과자에게 더할 연수. ``Input`` F열."""

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

    min_age: int = 15
    """허용 최소 만 연령. 종전 규칙 하드코딩 값."""
    max_age: int = 100
    """허용 최대 만 연령. 종전 규칙 하드코딩 값."""

    _by_source: dict[str, tuple[int, JobGroupRule]] = field(
        default_factory=dict, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self._by_source = {
            rule.source_name: (idx, rule) for idx, rule in enumerate(self.job_group_rules)
        }

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

    def find_job_group(self, source_name: object) -> tuple[int, JobGroupRule] | None:
        """명부 직군명으로 규칙을 찾는다. 없으면 ``None``.

        종전 규칙 는 ``Cells(jc1, 5).Value = list_jkn(j)`` 로 완전일치 비교만 한다.
        앞뒤 공백 때문에 매칭이 깨지는 사고가 잦아 여기서는 공백을 정리한 뒤
        비교한다.
        """
        return self._by_source.get(text(source_name))


def read_config(workbook, sheet_name: str = INPUT_SHEET) -> CalculationConfig:
    """``Input`` 시트를 :class:`CalculationConfig` 로 읽는다.

    :param workbook: ``openpyxl`` 워크북(``data_only=True`` 로 연 것).
    :raises KeyError: ``Input`` 시트가 없을 때.
    :raises ValueError: 산출기준일(C3)이 비어 있을 때.
    """
    ws = workbook[sheet_name]

    base_date = to_date(ws.cell(3, 3).value)
    if base_date is None:
        raise ValueError(f"{sheet_name}!C3 산출기준일이 비어 있습니다")

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
            )
        )

    return CalculationConfig(
        base_date=base_date,
        wage_check_amount=wage_check,
        job_group_rules=rules,
    )


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
