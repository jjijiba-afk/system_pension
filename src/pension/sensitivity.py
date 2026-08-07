"""민감도분석.

K-IFRS 1019호 문단 145 는 유의적인 각 보험수리적 가정의 합리적으로 가능한
변동이 확정급여채무에 미치는 영향을 공시하도록 요구한다. 여기서는 가정 하나만
바꾸고 나머지는 고정한 채(다른 가정 불변) DBO 를 다시 산출해 그 차이를 낸다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .assumptions import Assumptions
from .config import CalculationConfig
from .models import Roster
from .valuation import value_roster

__all__ = ["DEFAULT_SHOCKS", "SensitivityCase", "SensitivityResult", "run_sensitivity"]


@dataclass(frozen=True, slots=True)
class Shock:
    """가정 하나에 대한 충격 정의."""

    name: str
    kind: str
    """``discount`` / ``salary`` / ``mortality`` / ``withdrawal``."""
    amount: float
    """``discount``·``salary`` 는 절대 변동폭(%p), 나머지는 배수 조정폭."""


DEFAULT_SHOCKS: tuple[Shock, ...] = (
    Shock("할인율 +0.5%p", "discount", +0.005),
    Shock("할인율 -0.5%p", "discount", -0.005),
    Shock("임금상승률 +0.5%p", "salary", +0.005),
    Shock("임금상승률 -0.5%p", "salary", -0.005),
    Shock("사망률 +10%", "mortality", +0.10),
    Shock("사망률 -10%", "mortality", -0.10),
    Shock("퇴직률 +10%", "withdrawal", +0.10),
    Shock("퇴직률 -10%", "withdrawal", -0.10),
)


@dataclass(slots=True)
class SensitivityCase:
    """민감도 한 건의 결과."""

    name: str
    dbo: float
    change: float
    """기준 DBO 대비 증감액."""

    @property
    def change_ratio(self) -> float:
        base = self.dbo - self.change
        return self.change / base if base else 0.0


@dataclass(slots=True)
class SensitivityResult:
    base_dbo: float
    cases: list[SensitivityCase] = field(default_factory=list)


def apply_shock(assumptions: Assumptions, shock: Shock) -> Assumptions:
    """가정 하나만 흔든 사본을 만든다."""
    if shock.kind == "discount":
        return assumptions.replace(
            discount=assumptions.discount.shifted(shock.amount),
            label=f"{assumptions.label} / {shock.name}",
        )
    if shock.kind == "salary":
        return assumptions.replace(
            salary=assumptions.salary.shifted(shock.amount),
            label=f"{assumptions.label} / {shock.name}",
        )
    if shock.kind == "mortality":
        return assumptions.replace(
            mortality=assumptions.mortality.scaled(1.0 + shock.amount),
            label=f"{assumptions.label} / {shock.name}",
        )
    if shock.kind == "withdrawal":
        factor = 1.0 + shock.amount
        return assumptions.replace(
            withdrawal=assumptions.withdrawal.transformed(lambda c: c.scaled(factor)),
            label=f"{assumptions.label} / {shock.name}",
        )
    raise ValueError(f"알 수 없는 민감도 유형입니다: {shock.kind}")


def run_sensitivity(
    roster: Roster,
    config: CalculationConfig,
    assumptions: Assumptions,
    *,
    shocks: tuple[Shock, ...] = DEFAULT_SHOCKS,
    base_dbo: float | None = None,
) -> SensitivityResult:
    """가정별 민감도를 모두 계산한다.

    :param base_dbo: 이미 산출한 기준 DBO. 주지 않으면 여기서 한 번 더 계산한다.
    """
    if base_dbo is None:
        base_dbo = value_roster(roster, config, assumptions).dbo

    result = SensitivityResult(base_dbo=base_dbo)
    for shock in shocks:
        shocked = value_roster(roster, config, apply_shock(assumptions, shock))
        result.cases.append(
            SensitivityCase(name=shock.name, dbo=shocked.dbo, change=shocked.dbo - base_dbo)
        )
    return result
