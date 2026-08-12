"""검증 이슈 표현 및 수집.

검증은 중단하지 않고 **끝까지 돌며 전부 모은다.** 오류 하나에서 멈추면 담당자가
고치고 다시 돌리는 일을 오류 수만큼 되풀이해야 한다. 명부는 한 번에 손보는 것이
맞다.

치명적 오류(ERROR)가 하나라도 있으면 업로드 명부는 만들지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    """이슈 심각도."""

    ERROR = "ERROR"
    """업로드 명부를 만들 수 없는 오류. 산출을 멈춰야 하는 등급이다."""

    WARNING = "WARNING"
    """산출은 가능하지만 담당자가 확인해야 하는 사항."""

    INFO = "INFO"
    """확인할 것이 없는 안내.

    검증 결과를 읽는 시간은 한정돼 있다. 확인이 필요 없는 항목이 목록을 채우면
    정작 봐야 할 경고가 묻힌다. 실제 명부 6건에서 경고 1,452건 중 1,211건이
    '성명이 비어 있습니다' 였는데, 여섯 파일 모두 성명이 **한 명도** 없었다.
    개인정보를 지우고 사번으로만 보내 온 것이지 누락이 아니다.
    """


@dataclass(frozen=True, slots=True)
class Issue:
    """명부 한 건에 대한 검증 결과."""

    severity: Severity
    code: str
    """``JAE_BIRTH_INVALID`` 같은 안정적인 식별자. 리포트 집계 키로 쓴다."""

    message: str
    sheet: str = ""
    row: int | None = None
    """엑셀 시트 기준 실제 행 번호(1-based). 담당자가 바로 찾아갈 수 있게 한다."""

    seq: int | None = None
    """명부 내 순번. 오류 메시지의 "N 번째 임직원" 과 같은 값."""

    employee_id: str = ""
    column: str = ""
    """엑셀 열 문자(예: ``H``). 비어 있으면 행 전체에 대한 이슈."""

    value: str = ""

    def location(self) -> str:
        parts = [p for p in (self.sheet, f"{self.column}{self.row}" if self.column and self.row else (str(self.row) if self.row else ""))if p]
        return "!".join(parts)

    def __str__(self) -> str:
        loc = self.location()
        head = f"[{self.severity.value}] {self.code}"
        if loc:
            head += f" ({loc})"
        if self.employee_id:
            head += f" 사번={self.employee_id}"
        return f"{head}: {self.message}"


@dataclass(slots=True)
class IssueLog:
    """이슈 수집기."""

    issues: list[Issue] = field(default_factory=list)

    def add(
        self,
        severity: Severity,
        code: str,
        message: str,
        *,
        sheet: str = "",
        row: int | None = None,
        seq: int | None = None,
        employee_id: str = "",
        column: str = "",
        value: object = "",
    ) -> Issue:
        issue = Issue(
            severity=severity,
            code=code,
            message=message,
            sheet=sheet,
            row=row,
            seq=seq,
            employee_id=employee_id,
            column=column,
            value="" if value is None else str(value),
        )
        self.issues.append(issue)
        return issue

    def error(self, code: str, message: str, **kw: object) -> Issue:
        return self.add(Severity.ERROR, code, message, **kw)  # type: ignore[arg-type]

    def warning(self, code: str, message: str, **kw: object) -> Issue:
        return self.add(Severity.WARNING, code, message, **kw)  # type: ignore[arg-type]

    def info(self, code: str, message: str, **kw: object) -> Issue:
        return self.add(Severity.INFO, code, message, **kw)  # type: ignore[arg-type]

    def extend(self, issues: list[Issue]) -> None:
        self.issues.extend(issues)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    @property
    def notices(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.INFO]

    def has_errors(self) -> bool:
        return any(i.severity is Severity.ERROR for i in self.issues)

    def count_by_code(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for issue in self.issues:
            counts[issue.code] = counts.get(issue.code, 0) + 1
        return counts

    def __len__(self) -> int:
        return len(self.issues)

    def __iter__(self):
        return iter(self.issues)


class PensionDataError(ValueError):
    """명부 데이터가 산출 불가능한 상태일 때 발생."""

    def __init__(self, message: str, issues: list[Issue] | None = None) -> None:
        super().__init__(message)
        self.issues = issues or []


class DateParseError(ValueError):
    """날짜 문자열을 어떤 형식으로도 해석할 수 없을 때 발생."""

    def __init__(self, raw: str, reason: str = "") -> None:
        self.raw = raw
        self.reason = reason
        detail = f": {reason}" if reason else ""
        super().__init__(f"날짜를 해석할 수 없습니다: {raw!r}{detail}")
