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
    settlement_gain: float = 0.0
    """정산손익. 소멸한 채무가 지급액보다 크면 **이익**(음수로 채무를 줄인다).

    문단 109~112 는 정산 시점에 소멸하는 확정급여채무와 정산가격의 차이를
    정산손익으로 **당기손익** 에 인식하도록 한다. 소멸 채무를 알려면 정산
    직전 그 사람의 채무를 알아야 하므로 프로그램이 혼자 알아낼 수 없다.
    담당자가 전기 개인별 결과에서 합계를 넣어 주면 그때 계산한다.
    """
    other_paid: float = 0.0
    """퇴직위로금 등 퇴직급여 이외 지급액. 총지급금액과 별도 칸으로 오는 금액이다."""
    transfers_in: float = 0.0
    """전입으로 인수한 금액. 전출 지급액과 짝을 이룬다."""
    experience_adjustment: float = 0.0
    """경험조정에 의한 보험수리적손익."""
    assumption_change: float = 0.0
    """가정변경에 의한 보험수리적손익."""
    past_service_cost: float = 0.0
    """제도개정에 의한 과거근무원가.

    지급률 규정이 전기와 다르면 그 차이가 여기로 온다. 보험수리적손익과 달리
    **당기손익** 으로 즉시 인식하므로(문단 103), 가정변경효과에 섞이면 영업이익이
    달라진다.
    """
    initial_recognition: float = 0.0
    """최초 평가에서 한꺼번에 인식한 기말채무(당기근무원가 제외분).

    전기 산출이 없으면 비교할 기초채무가 없으므로 **보험수리적손익은 0** 이다
    — 손익은 '가정과 실제의 차이'인데 비교 대상 자체가 없다. 잔액을 경험조정에
    밀어 넣으면 첫해에 거대한 가짜 손익이 찍히므로 별도 줄로 둔다.
    """

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
            - self.other_paid
            + self.transfers_in
            + self.settlement_gain
        )

    @property
    def actuarial_gain_loss(self) -> float:
        """보험수리적손익 합계. 양수면 채무 증가(손실)."""
        return self.experience_adjustment + self.assumption_change

    @property
    def closing_dbo(self) -> float:
        """기말 확정급여채무."""
        return (self.expected_closing_dbo + self.actuarial_gain_loss
                + self.initial_recognition)

    def as_rows(self) -> list[tuple[str, float]]:
        """공시 표 순서대로의 (항목, 금액) 목록."""
        rows = [
            ("기초 확정급여채무", self.opening_dbo),
            ("당기근무원가", self.service_cost),
            ("이자원가", self.interest_cost),
            ("과거근무원가(제도개정)", self.past_service_cost),
            ("급여지급액", -self.benefits_paid),
            ("정산지급액(중간정산·전출)", -self.settlement_paid),
            ("퇴직위로금 등 지급액", -self.other_paid),
            ("전입 인수액", self.transfers_in),
            ("정산손익", self.settlement_gain),
            ("보험수리적손익 - 경험조정", self.experience_adjustment),
            ("보험수리적손익 - 가정변경", self.assumption_change),
        ]
        if self.initial_recognition:
            rows.append(("최초 인식 (전기 산출 없음)", self.initial_recognition))
        rows.append(("기말 확정급여채무", self.closing_dbo))
        return rows


def build_rollforward(
    *,
    opening_dbo: float,
    service_cost: float,
    interest_cost: float,
    benefits_paid: float,
    closing_dbo: float,
    dbo_with_prior_assumptions: float | None = None,
    dbo_after_amendment: float | None = None,
    settlement_paid: float = 0.0,
    settlement_obligation: float = 0.0,
    other_paid: float = 0.0,
    transfers_in: float = 0.0,
    past_service_cost: float = 0.0,
) -> RollForward:
    """증감표를 만든다.

    :param dbo_with_prior_assumptions: 당기말 명부를 **전기 가정 그대로** 산출한
        확정급여채무(A). 주면 보험수리적손익이 경험조정과 가정변경으로 나뉜다.
        주지 않으면 전액을 경험조정으로 잡고 가정변경은 0 으로 둔다.
    :param dbo_after_amendment: 당기말 명부를 **전기 계리가정 + 당기 지급률** 로
        산출한 확정급여채무(B). 주면 ``B − A`` 를 제도개정 효과로 떼어 내고,
        남은 ``기말 − B`` 만 가정변경효과로 본다.
    :param settlement_obligation: 정산으로 소멸한 확정급여채무. 주면
        ``지급액 − 소멸채무`` 를 정산손익으로 인식한다.
    """
    roll = RollForward(
        opening_dbo=opening_dbo,
        service_cost=service_cost,
        interest_cost=interest_cost,
        benefits_paid=benefits_paid,
        settlement_paid=settlement_paid,
        other_paid=other_paid,
        transfers_in=transfers_in,
        past_service_cost=past_service_cost,
    )
    if settlement_obligation:
        # 소멸한 채무보다 적게 주고 끝냈으면 그만큼 이익(채무 감소)이다.
        roll.settlement_gain = settlement_paid - settlement_obligation

    expected = roll.expected_closing_dbo

    if dbo_with_prior_assumptions is None:
        roll.experience_adjustment = closing_dbo - expected
        roll.assumption_change = 0.0
        return roll

    # 제도개정을 떼어냈으면 기준점도 **개정 후** 채무여야 한다. 기대 기말채무에
    # 이미 과거근무원가가 들어가 있으므로, 개정 전 채무(A)와 비교하면 개정분이
    # 두 번 빠져 표가 맞지 않는다.
    reference = (
        dbo_after_amendment if dbo_after_amendment is not None
        else dbo_with_prior_assumptions
    )
    roll.experience_adjustment = reference - expected
    roll.assumption_change = closing_dbo - reference
    return roll


def initial_period(closing_dbo: float, service_cost: float) -> RollForward:
    """최초 평가용 증감표.

    비교 대상인 전기 산출이 없으므로 기초채무·이자원가는 0 이고, **보험수리적
    손익도 0** 이다 — 손익은 가정과 실제의 차이인데 비교할 전기 채무 자체가
    없다. 당기근무원가를 뺀 기말채무 잔액은 '최초 인식' 줄로 따로 표시한다.
    """
    roll = RollForward(
        opening_dbo=0.0,
        service_cost=service_cost,
        interest_cost=0.0,
        benefits_paid=0.0,
    )
    roll.initial_recognition = closing_dbo - roll.expected_closing_dbo
    return roll
