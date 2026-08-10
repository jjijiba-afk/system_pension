"""차년도 예측 — 다음 결산의 채무와 자산이 어디쯤 갈지.

평가보고서는 당기 결과와 함께 **차년도 예상 퇴직급여 비용** 과 채무·자산의
예측표를 싣는다. 회사가 다음 해 예산을 잡고 부담금 규모를 정하는 데 쓰는
숫자다. 문단 147(b) 의 '차기 보고기간에 납부할 것으로 예상되는 기여금'도
여기서 나온다.

계산은 증감표를 앞으로 한 번 더 돌리는 것과 같다.

===================================  ======================================
기시 확정급여채무                    당기말 채무
＋ 당기근무원가                      산출이 낸 차년도 근무원가
＋ 이자원가                          기시 채무 × 할인율
－ 예상 지급액                       만기분석의 1년 미만 구간
＝ 기말 확정급여채무 (예측)          보험수리적손익은 0 으로 본다
===================================  ======================================

보험수리적손익을 0 으로 두는 것은 예측이기 때문이다 — 가정이 그대로 실현된다고
보면 손익이 생기지 않는다. 실제로는 생기고, 그것이 다음 결산의 재측정요소다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["Projection", "project_next_year"]


@dataclass(slots=True)
class Projection:
    """차년도 예측 한 벌."""

    opening_dbo: float = 0.0
    service_cost: float = 0.0
    interest_cost: float = 0.0
    benefits_paid: float = 0.0
    """예상 급여지급액(할인 전 명목금액, 1년 미만 구간)."""

    opening_assets: float = 0.0
    expected_return: float = 0.0
    contributions: float = 0.0
    """예상 부담금 납입액 (문단 147(b))."""
    assets_paid: float = 0.0
    """자산에서 나갈 것으로 보는 지급액."""

    has_assets: bool = False
    """사외적립자산 입력이 있었는지. 없으면 자산 쪽 표를 감춘다."""

    @property
    def closing_dbo(self) -> float:
        return (self.opening_dbo + self.service_cost + self.interest_cost
                - self.benefits_paid)

    @property
    def closing_assets(self) -> float:
        return (self.opening_assets + self.expected_return + self.contributions
                - self.assets_paid)

    @property
    def expense(self) -> float:
        """차년도 손익에 갈 퇴직급여 비용(순이자 반영)."""
        return self.service_cost + self.interest_cost - self.expected_return

    def dbo_rows(self) -> list[tuple[str, float]]:
        return [
            ("기시 확정급여채무의 현재가치", self.opening_dbo),
            ("당기근무원가", self.service_cost),
            ("확정급여채무의 이자원가", self.interest_cost),
            ("예상 퇴직급여 지급액", -self.benefits_paid),
            ("기말 확정급여채무의 현재가치", self.closing_dbo),
        ]

    def asset_rows(self) -> list[tuple[str, float]]:
        return [
            ("기시 사외적립자산 공정가치", self.opening_assets),
            ("사외적립자산의 기대수익", self.expected_return),
            ("예상 부담금 납입액", self.contributions),
            ("예상 지급액", -self.assets_paid),
            ("기말 사외적립자산 공정가치", self.closing_assets),
        ]

    def expense_rows(self) -> list[tuple[str, float]]:
        """차년도 예상 퇴직급여 비용."""
        return [
            ("당기근무원가", self.service_cost),
            ("확정급여채무의 이자원가", self.interest_cost),
            ("사외적립자산의 기대수익", -self.expected_return),
            ("합계", self.expense),
        ]


def project_next_year(run: Any, *, contributions: float | None = None) -> Projection:
    """산출 한 회차에서 차년도 예측을 만든다.

    :param contributions: 차년도 예상 부담금. 비우면 당기 납입액을 그대로 본다
        — 적립정책이 바뀌지 않는 한 그것이 가장 나은 추정이다.
    """
    from .webreport import maturity_buckets

    val = run.valuation
    assets = run.plan_assets
    rate = (run.assumptions.discount.flat
            if run.assumptions.discount.flat is not None
            else val.single_discount_rate())

    # 1년 안에 나갈 것으로 보는 금액. 만기분석의 첫 구간이 곧 그 값이다.
    first_year = dict(maturity_buckets(val.benefit_cash_flows())).get("1년미만", 0.0)

    made = Projection(
        opening_dbo=val.dbo,
        service_cost=val.service_cost,
        interest_cost=val.dbo * rate,
        benefits_paid=first_year,
        has_assets=assets is not None,
    )
    if assets is not None:
        made.opening_assets = assets.closing_fair_value
        made.expected_return = assets.closing_fair_value * rate
        made.contributions = (assets.contributions if contributions is None
                              else contributions)
        # 자산에서 나가는 몫은 당기 실적 비율을 그대로 이어 본다. 전액을
        # 자산에서 준다고 보면 적립비율이 낮은 회사의 자산이 음수가 된다.
        share = (assets.benefits_paid / first_year
                 if first_year and assets.benefits_paid else assets.funded_ratio)
        made.assets_paid = first_year * min(1.0, max(0.0, share))
    return made
