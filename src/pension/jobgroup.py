"""명부 직급·직군을 산출 직군으로 묶기.

명부의 `직군` 열에 무엇이 들어올지는 회사가 정한다. 실제로 받아 본 파일만 해도
이렇게 갈렸다.

* `정규직` / `계약직` — 고용형태가 그대로 들어온 경우
* `사원` / `주임` / `계장` / `과장` / `부장` / `대표이사` — **직급** 이 들어온 경우
* 열 자체가 비어 있고 `임직원구분` 만 `정규사원` / `촉탁사원` / `임원(별정)` 인 경우

산출 가정(퇴직률·승급률·지급률)은 이 낱낱의 직급마다 따로 만들지 않는다.
비슷한 것끼리 묶어 **변환 직군** 한 벌을 만들고, 그 단위로 기초율을 세운다.
기본 묶음은 정규직 / 계약직 / 임원 세 가지다.

다만 이 셋으로 충분하다는 보장은 없다. 생산직과 관리직의 퇴직률이 확연히 다른
회사라면 정규직을 다시 갈라야 하고, 그때 프로그램이 세 칸만 내어 주면 쓸 수
없다. 그래서 **묶음 이름 자체를 사용자가 정한다.** 이 모듈은 기본값을 제안할 뿐,
강제하지 않는다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Final

from .normalize import EmployeeType, normalize_employee_type, text

__all__ = [
    "DEFAULT_GROUPS",
    "GROUP_CONTRACT",
    "GROUP_EXECUTIVE",
    "GROUP_REGULAR",
    "RosterGroup",
    "scan_roster",
    "suggest_group",
    "suggest_mapping",
]

GROUP_REGULAR: Final = "정규직"
GROUP_CONTRACT: Final = "계약직"
GROUP_EXECUTIVE: Final = "임원"

#: 기본 묶음. 화면에서 얼마든지 고치거나 늘릴 수 있다.
DEFAULT_GROUPS: Final[tuple[str, ...]] = (GROUP_REGULAR, GROUP_CONTRACT, GROUP_EXECUTIVE)

# 명부에서 실제로 본 계약직 표기. 부분일치로 본다 — '촉탁사원', '계약직(무기)',
# '기간제 근로자' 처럼 뒤에 설명이 붙는 경우가 많다.
_CONTRACT_TOKENS: Final[tuple[str, ...]] = (
    "계약", "촉탁", "기간제", "임시", "일용", "파견", "인턴", "수습", "위촉", "알바",
    "아르바이트", "단시간", "시간제", "비정규",
)

# 직급 열에 임원이 섞여 들어오는 표기. `임직원구분` 이 비어 있을 때 최후 수단이다.
_EXECUTIVE_TOKENS: Final[tuple[str, ...]] = (
    "임원", "이사", "대표", "사장", "부사장", "전무", "상무", "회장", "감사", "고문",
)


def decide_employee_type(
    employee_type: object = "", mapped_group: object = ""
) -> EmployeeType:
    """이 사람이 임원인지.

    임직원구분 열을 명부에서 받지 않는다 — 직군과 겹치는 칸이라, 둘이 어긋나면
    어느 쪽을 믿을지 정할 수 없다. 대신 **사람이 정한 직군 매핑** 을 따른다.
    명부의 '상무'·'등기이사' 를 어느 묶음으로 볼지는 [직군 규칙] 에서 사람이
    정하고, 그 결과가 ``mapped_group`` 이다.

    자동으로 넘겨짚지 않는 이유는 같은 '촉탁사원' 이라도 정년 후 재고용이면
    계약직, 임원 예우 재고용이면 임원인 회사가 있기 때문이다. :func:`suggest_group`
    이 제안은 하되 확정은 화면에서 한다.

    임직원구분이 적혀 온 옛 명부는 그쪽을 먼저 본다.
    """
    raw = text(employee_type)
    if raw:
        return normalize_employee_type(raw)
    mapped = text(mapped_group)
    return (EmployeeType.EXECUTIVE if mapped and GROUP_EXECUTIVE in mapped
            else EmployeeType.STAFF)


@dataclass(slots=True)
class RosterGroup:
    """명부에서 실제로 발견된 (직군, 임직원구분) 조합 하나."""

    source_name: str
    """명부 `직군` 열 원문. 비어 있을 수 있다."""
    employee_type: str
    """명부 `임직원구분` 열 **원문**(`정규사원`, `촉탁사원`, `임원(별정)`).

    정규화한 `임원`/`직원` 을 쓰지 않는 이유는 그 둘로 줄이면 `정규사원` 과
    `촉탁사원` 이 한 칸에 모여 다시 갈라낼 수 없기 때문이다.
    :meth:`CalculationConfig.find_job_group` 이 원문도 함께 보므로 이대로
    `지급규정` 시트에 적으면 그대로 맞는다.
    """
    normalized_type: str = ""
    """정규화 결과(`임원` / `직원`). 임원 판정이 맞는지 눈으로 확인하는 용도."""
    active: int = 0
    """재직자 수."""
    retired: int = 0
    """퇴직자 수."""

    @property
    def headcount(self) -> int:
        return self.active + self.retired

    @property
    def key(self) -> tuple[str, str]:
        """`지급규정` 시트에 적을 (명부직군, 임직원구분) 짝."""
        return (self.source_name, self.employee_type)

    @property
    def label(self) -> str:
        """화면에 쓸 이름. 한쪽이 비면 나머지만 쓴다."""
        if self.source_name and self.employee_type:
            return f"{self.source_name} / {self.employee_type}"
        return self.source_name or self.employee_type or "(빈 값)"


def suggest_group(
    source_name: object,
    employee_type: object = "",
    groups: tuple[str, ...] | list[str] = DEFAULT_GROUPS,
) -> str:
    """직군·임직원구분을 보고 어느 묶음일지 제안한다.

    **제안일 뿐이다.** 회사 규정이 우선이므로 화면에서 반드시 고칠 수 있어야 한다.
    자동으로 확정하지 않는 이유는, 같은 '촉탁사원' 이라도 정년 후 재고용이면
    계약직이지만 임원 예우 재고용이면 임원인 회사가 있기 때문이다.

    :param groups: 고를 수 있는 묶음 이름. 여기 없는 이름은 제안하지 않고
        첫 번째 묶음으로 떨어뜨린다. 사용자가 `생산직/관리직/임원` 처럼
        전혀 다른 이름을 쓸 수 있기 때문이다.
    """
    names = [text(g) for g in groups if text(g)]
    fallback = names[0] if names else GROUP_REGULAR

    job = text(source_name)
    raw_type = text(employee_type)
    haystack = f"{job} {raw_type}"

    def pick(candidate: str) -> str | None:
        return candidate if candidate in names else None

    if normalize_employee_type(raw_type) is EmployeeType.EXECUTIVE:
        return pick(GROUP_EXECUTIVE) or fallback
    if any(token in haystack for token in _EXECUTIVE_TOKENS):
        return pick(GROUP_EXECUTIVE) or fallback
    if any(token in haystack for token in _CONTRACT_TOKENS):
        return pick(GROUP_CONTRACT) or fallback
    return pick(GROUP_REGULAR) or fallback


def suggest_mapping(
    found: list[RosterGroup], groups: tuple[str, ...] | list[str] = DEFAULT_GROUPS
) -> dict[tuple[str, str], str]:
    """발견된 조합 전부에 대한 제안. 키는 :attr:`RosterGroup.key`."""
    return {
        group.key: suggest_group(group.source_name, group.employee_type, groups)
        for group in found
    }


def scan_roster(workbook) -> list[RosterGroup]:
    """명부 두 장을 훑어 (직군, 임직원구분) 조합과 인원수를 센다.

    산출이 아니라 화면 채우기가 목적이므로, 읽다가 생긴 형식 오류는 버린다.
    시트가 아예 없으면 그 시트는 건너뛴다.

    :returns: 인원 많은 순으로 정렬된 조합 목록.
    """
    from .errors import IssueLog
    from .readers import _last_data_row, _resolve

    log = IssueLog()
    counts: dict[tuple[str, str], Counter] = {}

    for bucket in ("active", "retired"):
        try:
            ws, cols, layout = _resolve(workbook, bucket, log)
        except KeyError:
            continue

        job_col = cols.get("job_group")
        type_col = cols.get("employee_type")
        if job_col is None and type_col is None:
            continue

        first = layout.data_start_row
        for row in range(first, _last_data_row(ws, first, cols) + 1):
            job = text(ws.cell(row, job_col.index).value) if job_col else ""
            raw = text(ws.cell(row, type_col.index).value) if type_col else ""
            if not job and not raw:
                continue
            counts.setdefault((job, raw), Counter())[bucket] += 1

    found = [
        RosterGroup(
            source_name=job,
            employee_type=raw,
            normalized_type=normalize_employee_type(raw).value,
            active=tally["active"],
            retired=tally["retired"],
        )
        for (job, raw), tally in counts.items()
    ]
    found.sort(key=lambda g: (-g.headcount, g.source_name, g.employee_type))
    return found
