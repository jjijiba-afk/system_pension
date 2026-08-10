"""계리평가 보고서 — K-IFRS 1019 공시 요구 항목이 빠짐없이 실려야 한다."""

from __future__ import annotations

import pytest

from pension.pipeline import PlanAssetInput, PriorPeriod, RunOptions, run_valuation
from pension.webreport import render_html


@pytest.fixture
def full_run(roster_path, assumptions_path, tmp_path):
    """전기 연결·사외적립자산까지 갖춘 산출 — 보고서의 모든 표가 채워진다."""
    return run_valuation(RunOptions(
        roster_path=roster_path,
        assumptions_path=assumptions_path,
        output_path=tmp_path / "산출결과.xlsx",
        include_sensitivity=True,
        include_longterm=True,
        allow_errors=True,
        prior=PriorPeriod(dbo=300_000_000, service_cost=30_000_000,
                          discount_rate=0.04),
        plan_assets=PlanAssetInput(
            opening_fair_value=200_000_000, closing_fair_value=230_000_000,
            contributions=50_000_000, benefits_paid=20_000_000,
        ),
    ))


class TestSeveranceReport:
    def test_covers_every_disclosure_requirement(self, full_run) -> None:
        """문단 135~147 의 공시 항목이 전부 제목으로 존재해야 한다."""
        page = render_html(full_run, kind="severance", client="시험 주식회사")

        for required in (
            "퇴직급여 확정급여부채 평가보고서",     # 표지
            "시험 주식회사",
            "평가개요",
            "순확정급여부채(자산) 현황",            # 문단 63
            "손익계산서",                           # 문단 120·141
            "확정급여채무의 변동내역",              # 문단 140(a)(ii)
            "사외적립자산의 변동내역",              # 문단 140(a)(i)
            "재측정요소 분석",                      # 문단 141(c)
            "차년도 예상 퇴직급여 비용",
            "민감도 분석",                          # 문단 145
            "제도의 일반사항과 위험",               # 문단 139
            "기본적인 보험수리적 가정",             # 문단 144
            "예측단위적립방식",                     # 문단 67~68
            "경과기간별 예상 확정급여채무",         # 문단 147(c)
            "차년도 예상 기여금",                   # 문단 147(b)
            "임직원 분포 현황",
            "용어 정리",
        ):
            assert required in page, f"보고서에 '{required}' 이(가) 없다"

    def test_numbers_come_from_the_run(self, full_run) -> None:
        """보고서 숫자는 산출 결과를 그대로 옮긴 것이어야 한다."""
        page = render_html(full_run, kind="severance")
        assert f"{full_run.valuation.dbo:,.0f}" in page
        assert f"{full_run.rollforward.service_cost:,.0f}" in page
        # 듀레이션과 민감도 기준값
        assert f"{full_run.valuation.duration:.2f}년" in page
        assert f"{full_run.sensitivity.base_dbo:,.0f}" in page

    def test_maturity_buckets_add_up(self, full_run) -> None:
        """만기분석 구간 합 = 전체 기대지급액. 구간을 빠뜨리면 합이 깨진다."""
        from pension.webreport import maturity_buckets

        flows = full_run.valuation.cash_flows()
        buckets = maturity_buckets(flows)
        assert sum(v for _, v in buckets) == pytest.approx(sum(flows.values()))
        assert buckets[0][0] == "1년미만"
        assert buckets[-1][0] == "20년이상"

    def test_rate_tables_are_attached(self, full_run) -> None:
        """첨부에 승급률·퇴직률·사망률·지급률 표가 실린다 (문단 144 근거)."""
        page = render_html(full_run, kind="severance")
        for table in ("승급률", "중도퇴직률", "사망률 qx", "퇴직급여 지급률"):
            assert table in page

    def test_without_prior_link_it_says_so(self, roster_path, assumptions_path,
                                           tmp_path) -> None:
        """전기 연결이 없으면 없는 대로 — 빈 표 대신 이유를 적는다."""
        run = run_valuation(RunOptions(
            roster_path=roster_path, assumptions_path=assumptions_path,
            output_path=tmp_path / "r.xlsx", allow_errors=True,
            include_sensitivity=False, include_longterm=False,
        ))
        page = render_html(run, kind="severance")
        assert "전기 산출 결과를 연결하지 않아" in page
        assert "민감도분석을 끄고 산출했습니다" in page


class TestLongtermReport:
    def test_longterm_report_renders(self, full_run) -> None:
        page = render_html(full_run, kind="longterm", client="시험 주식회사")
        for required in (
            "장기종업원급여부채 평가보고서",
            "문단 154",                # 재측정 당기손익 인식
            "확정급여채무의 변동내역",
            "차년도 예상 장기급여 비용",
            "문단 158",                # 간이 공시
            "장기급여 지급 항목",
        ):
            assert required in page, f"장기급여 보고서에 '{required}' 이(가) 없다"
        assert f"{full_run.longterm.dbo:,.0f}" in page

    def test_longterm_without_run_is_refused(self, roster_path, assumptions_path,
                                             tmp_path) -> None:
        run = run_valuation(RunOptions(
            roster_path=roster_path, assumptions_path=assumptions_path,
            output_path=tmp_path / "r.xlsx", allow_errors=True,
            include_sensitivity=False, include_longterm=False,
        ))
        with pytest.raises(ValueError, match="장기급여를 산출하지 않았습니다"):
            render_html(run, kind="longterm")

    def test_unknown_kind_is_refused(self, full_run) -> None:
        with pytest.raises(ValueError, match="보고서 종류"):
            render_html(full_run, kind="pension")
