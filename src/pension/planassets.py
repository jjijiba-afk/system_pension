"""사외적립자산과 순확정급여부채.

확정급여채무만으로는 재무제표를 못 만든다. K-IFRS 1019호 문단 63 은 재무상태표에
**순확정급여부채(자산)** — 확정급여채무에서 사외적립자산 공정가치를 뺀 값 — 을
인식하도록 하고, 문단 140 은 채무와 자산의 증감을 각각 표로 공시하도록 한다.

자산 쪽은 계리적으로 투영할 것이 없다. 담당자가 신탁회사에서 받는 네 숫자면
표가 완성된다.

===================  ==========================================================
기초 공정가치        전기말 사외적립자산 잔액
부담금 납입액        당기 중 회사가 넣은 돈
지급액               자산에서 직접 나간 퇴직급여
기말 공정가치        결산일 잔액 (신탁 명세서의 숫자 그대로)
===================  ==========================================================

나머지는 계산된다.

``이자수익``
    기초 자산 × 할인율. 문단 125 는 **채무에 쓴 것과 같은 할인율** 을 자산에도
    쓰도록 한다. 자산의 실제 수익률을 쓰지 않는다는 점이 중요하다.

``자산 재측정손익``
    기말 공정가치 − (기초 + 부담금 − 지급액 + 이자수익). 이자수익을 넘는
    초과수익(또는 미달)이며 **기타포괄손익** 으로 간다(문단 120(b)).

자산인식상한(문단 64)은 다루지 않는다. 순확정급여**자산** 이 나오는 제도, 즉
초과적립 상태는 국내 퇴직급여제도에서 드물고, 상한을 재려면 환급·미래부담금
절감액의 현재가치라는 별도 판단이 필요하기 때문이다. 자산이 채무를 넘으면
경고를 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["PlanAssets", "build_plan_assets"]


@dataclass(slots=True)
class PlanAssets:
    """사외적립자산 증감과 순확정급여부채."""

    opening_fair_value: float = 0.0
    """기초 공정가치."""
    contributions: float = 0.0
    """당기 부담금 납입액."""
    benefits_paid: float = 0.0
    """사외적립자산에서 지급된 퇴직급여."""
    interest_income: float = 0.0
    """이자수익 = 기초 자산 × 할인율(채무와 같은 율)."""
    remeasurement: float = 0.0
    """자산 재측정손익. 이자수익을 넘는 초과수익이면 양수."""
    closing_fair_value: float = 0.0
    """기말 공정가치."""
    closing_dbo: float = 0.0
    """기말 확정급여채무. 순부채를 내기 위해 함께 들고 있는다."""
    unpaid_benefits: float = 0.0
    """기준일 현재 미지급 퇴직급여.

    이미 퇴직했는데 결산일까지 지급하지 않은 금액이다. 재직자 채무에는 잡히지
    않지만 회사가 여전히 지고 있는 의무라 순부채에 더해야 한다."""

    @property
    def expected_closing(self) -> float:
        """재측정 전 기말 자산."""
        return (
            self.opening_fair_value
            + self.contributions
            - self.benefits_paid
            + self.interest_income
        )

    @property
    def total_obligation(self) -> float:
        """확정급여채무 + 미지급 퇴직급여."""
        return self.closing_dbo + self.unpaid_benefits

    @property
    def net_liability(self) -> float:
        """순확정급여부채. 음수면 순확정급여자산(초과적립)이다."""
        return self.total_obligation - self.closing_fair_value

    @property
    def funded_ratio(self) -> float:
        """적립비율. 채무가 0 이면 0."""
        total = self.total_obligation
        return self.closing_fair_value / total if total else 0.0

    @property
    def overfunded(self) -> bool:
        """자산이 채무를 넘었는지. 넘으면 자산인식상한을 따로 검토해야 한다."""
        return self.net_liability < 0

    def as_rows(self) -> list[tuple[str, float]]:
        """공시 표 순서대로의 (항목, 금액) 목록."""
        return [
            ("기초 사외적립자산 공정가치", self.opening_fair_value),
            ("이자수익", self.interest_income),
            ("부담금 납입액", self.contributions),
            ("급여지급액", -self.benefits_paid),
            ("자산 재측정손익", self.remeasurement),
            ("기말 사외적립자산 공정가치", self.closing_fair_value),
        ]

    def net_rows(self) -> list[tuple[str, float]]:
        """순확정급여부채 표."""
        rows = [("확정급여채무 현재가치", self.closing_dbo)]
        if self.unpaid_benefits:
            rows.append(("미지급 퇴직급여", self.unpaid_benefits))
        rows += [
            ("사외적립자산 공정가치", -self.closing_fair_value),
            ("순확정급여부채", self.net_liability),
        ]
        return rows


def build_plan_assets(
    *,
    opening_fair_value: float,
    closing_fair_value: float,
    contributions: float = 0.0,
    benefits_paid: float = 0.0,
    discount_rate: float = 0.0,
    closing_dbo: float = 0.0,
    unpaid_benefits: float = 0.0,
    period_years: float = 1.0,
) -> PlanAssets:
    """자산 증감표를 만든다.

    :param discount_rate: 채무에 쓴 할인율. 자산의 기대수익률이 아니다.
    :param period_years: 산출 기간(년). 결산기가 바뀌어 1년이 아니면 이자수익도
        그만큼만 인식한다.

    이자수익은 기초 잔액에 기간분, 기중 들어오고 나간 돈에 절반 기간분을 준다
    (기중 균등발생 가정). 채무 쪽 이자원가와 같은 방식이라야 순이자가 맞는다.
    """
    interest = discount_rate * period_years * (
        opening_fair_value + (contributions - benefits_paid) * 0.5
    )
    assets = PlanAssets(
        opening_fair_value=opening_fair_value,
        contributions=contributions,
        benefits_paid=benefits_paid,
        interest_income=interest,
        closing_fair_value=closing_fair_value,
        closing_dbo=closing_dbo,
        unpaid_benefits=unpaid_benefits,
    )
    # 재측정손익은 나머지로 정한다 — 신탁 명세서의 기말 잔액이 사실이고,
    # 그 값에 맞추려면 얼마가 더 벌리거나 덜 벌렸어야 하는지를 역산한다.
    assets.remeasurement = closing_fair_value - assets.expected_closing
    return assets
