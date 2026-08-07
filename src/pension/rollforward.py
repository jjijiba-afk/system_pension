"""확정급여채무 증감분석(Roll-forward)과 보험수리적손익 분해.

K-IFRS 1019호 문단 141 은 기초 채무에서 기말 채무에 이르는 변동을 근무원가,
이자원가, 급여지급액, 보험수리적손익으로 나누어 공시하도록 요구한다. 보험수리적
손익은 다시 **경험조정** 과 **가정변경효과** 로 나뉜다.

분해 방법
---------
필요한 산출은 세 벌이다.

======================  ==================  =============================
산출                    명부                가정
======================  ==================  =============================
``prior``               전기말 명부         전기 가정
``current_prior_assum`` 당기말 명부         **전기** 가정
``current``             당기말 명부         당기 가정
======================  ==================  =============================

이때::

    예상 기말채무 = 기초채무 + 근무원가 + 이자원가 - 급여지급액
    경험조정     = current_prior_assum − 예상 기말채무
    가정변경효과 = current − current_prior_assum

즉 **가정을 그대로 둔 채** 실제 명부로 다시 산출한 값과 예상값의 차이가 경험조정
(퇴직·사망·임금인상이 가정과 달랐던 부분)이고, 명부를 고정한 채 가정만 바꿔
생긴 차이가 가정변경효과다.

전기 산출 결과가 없으면(최초 평가) :func:`initial_period` 로 기초채무 0 에서
시작하는 표를 만든다.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RollForward", "build_rollforward", "initial_period"]


@dataclass(slots=True)
class RollForward:
    """기초 → 기말 확정급여채무 증감표."""

    opening_dbo: float
    """기초 확정급여채무(전기말)."""
    service_cost: float
    """당기근무원가."""
    interest_cost: float
    """이자원가."""
    benefits_paid: float
    """당기 급여지급액(퇴직자에게 실제 지급된 퇴직급여)."""
    settlement_paid: float = 0.0
    """중간정산·전출 등 정산 지급액."""
    experience_adjustment: float = 0.0
    """경험조정에 의한 보험수리적손익."""
    assumption_change: float = 0.0
    """가정변경에 의한 보험수리적손익."""
    past_service_cost: float = 0.0
    """제도개정에 의한 과거근무원가."""

    @property
    def expected_closing_dbo(self) -> float:
        """가정대로 흘러갔을 때의 기말 채무."""
        return (
            self.opening_dbo
            + self.service_cost
            + self.interest_cost
            + self.past_service_cost
            - self.benefits_paid
            - self.settlement_paid
        )

    @property
    def actuarial_gain_loss(self) -> float:
        """보험수리적손익 합계. 양수면 채무 증가(손실)."""
        return self.experience_adjustment + self.assumption_change

    @property
    def closing_dbo(self) -> float:
        """기말 확정급여채무."""
        return self.expected_closing_dbo + self.actuarial_gain_loss

    def as_rows(self) -> list[tuple[str, float]]:
        """공시 표 순서대로의 (항목, 금액) 목록."""
        return [
            ("기초 확정급여채무", self.opening_dbo),
            ("당기근무원가", self.service_cost),
            ("이자원가", self.interest_cost),
            ("과거근무원가(제도개정)", self.past_service_cost),
            ("급여지급액", -self.benefits_paid),
            ("정산지급액(중간정산·전출)", -self.settlement_paid),
            ("보험수리적손익 - 경험조정", self.experience_adjustment),
            ("보험수리적손익 - 가정변경", self.assumption_change),
            ("기말 확정급여채무", self.closing_dbo),
        ]


def build_rollforward(
    *,
    opening_dbo: float,
    service_cost: float,
    interest_cost: float,
    benefits_paid: float,
    closing_dbo: float,
    dbo_with_prior_assumptions: float | None = None,
    settlement_paid: float = 0.0,
    past_service_cost: float = 0.0,
) -> RollForward:
    """증감표를 만든다.

    :param dbo_with_prior_assumptions: 당기말 명부를 **전기 가정** 으로 산출한
        확정급여채무. 주면 보험수리적손익이 경험조정과 가정변경으로 나뉜다.
        주지 않으면 전액을 경험조정으로 잡고 가정변경은 0 으로 둔다.
    """
    roll = RollForward(
        opening_dbo=opening_dbo,
        service_cost=service_cost,
        interest_cost=interest_cost,
        benefits_paid=benefits_paid,
        settlement_paid=settlement_paid,
        past_service_cost=past_service_cost,
    )
    expected = roll.expected_closing_dbo

    if dbo_with_prior_assumptions is None:
        roll.experience_adjustment = closing_dbo - expected
        roll.assumption_change = 0.0
    else:
        roll.experience_adjustment = dbo_with_prior_assumptions - expected
        roll.assumption_change = closing_dbo - dbo_with_prior_assumptions

    return roll


def initial_period(closing_dbo: float, service_cost: float) -> RollForward:
    """최초 평가용 증감표.

    비교 대상인 전기 산출이 없으므로 기초채무·이자원가를 0 으로 두고 기말채무
    전액을 경험조정 자리에 표시한다(공시 시에는 '최초 인식' 으로 표기).
    """
    roll = RollForward(
        opening_dbo=0.0,
        service_cost=service_cost,
        interest_cost=0.0,
        benefits_paid=0.0,
    )
    roll.experience_adjustment = closing_dbo - roll.expected_closing_dbo
    return roll
