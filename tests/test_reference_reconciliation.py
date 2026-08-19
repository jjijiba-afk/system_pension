"""참고 산출 시스템과 **셀 한 칸씩** 맞대어 본다.

총액만 맞대면 못 잡는다. 채무 총액이 2% 어긋났을 때, 그것이 할인 시점 반년인지
임금 시점 한 해인지 귀속 분모인지 정년 처리인지는 총액 하나로는 영영 갈리지
않는다 — 네 가지가 저마다 조금씩 다르고 그것들이 곱해져 하나로 뭉쳐 오기
때문이다. 연차별로 같은 자를 대고 한 줄씩 맞대야 짚인다.

받은 산출 표본에서 **입력과 결과를 함께** 떠 놓았다
(``tests/data/상용대사-표본.json``). 인적사항은 담지 않았다 — 근속연수와
연령만 있으면 같은 값이 나오므로, 사번·생년월일·날짜는 필요가 없다.

여기서 지키는 것은 :mod:`pension.reference` 가 그 표본을 재현하는지 하나다.
우리 엔진(:mod:`pension.valuation`)이 같은 값을 내야 한다는 뜻이 아니다 —
관행이 다른 대목이 있고, 그것을 **알고** 고르는 것이 이 대사의 목적이다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pension import reference

CASE = Path(__file__).with_name("data") / "상용대사-표본.json"

#: 상대오차 허용치. 부동소수점 누적 오차만 봐주고 셈법 차이는 봐주지 않는다.
TOLERANCE = 1e-9


@pytest.fixture(scope="module")
def sample() -> dict:
    return json.loads(CASE.read_text(encoding="utf-8"))


def severance_of(sample: dict, allocation: str) -> list[reference.SeveranceYear]:
    part = sample["퇴직급여"]
    return reference.severance_table(reference.SeveranceCase(
        discount_rate=part["할인율"], base_up=part["베이스업"], nra=part["정년"],
        wage=part["임금"], alloc_service=part["할당근속"],
        total_service=part["총근속"],
        age=part["연령"], promotion=part["승급률"],
        withdrawal=part["중도퇴직률"], mortality=part["사망률"],
        early=part["명예퇴직률"],
        multiple_normal=part["지급률_정년"], multiple_voluntary=part["지급률_중도"],
        multiple_death=part["지급률_사망"], multiple_early=part["지급률_명예"],
        allocation=allocation,
    ))


def long_term_of(sample: dict) -> list[reference.LongTermYear]:
    part = sample["장기급여"]
    return reference.long_term_table(reference.LongTermCase(
        discount_rate=part["할인율"], base_up=part["베이스업"], nra=part["정년"],
        wage=part["평균임금"], daily_wage=part["일기본급"],
        total_service=part["총근속"], gold_price=part["금현물가격"],
        gold_growth=part["금상승률"],
        age=part["연령"], promotion=part["승급률"],
        withdrawal=part["중도퇴직률"], mortality=part["사망률"],
        staying_wage=part["재직_평균임금"], staying_gold=part["재직_금현물"],
        staying_cash=part["재직_현금"], staying_leave=part["재직_휴가일수"],
        leaving_wage=part["탈퇴_평균임금"], leaving_gold=part["탈퇴_금현물"],
        leaving_cash=part["탈퇴_현금"], leaving_leave=part["탈퇴_휴가일수"],
    ))


def extra_pay_of(sample: dict) -> list[reference.ExtraPayYear]:
    part = sample["추가지급"]
    rates = sample["퇴직급여"]
    return reference.extra_pay_table(reference.ExtraPayCase(
        discount_rate=rates["할인율"], base_up=rates["베이스업"], nra=rates["정년"],
        base_pay=part["기본급"], service=part["근속"],
        age=part["연령"], promotion=rates["승급률"],
        withdrawal=rates["중도퇴직률"], mortality=rates["사망률"],
        multiple=part["지급률"],
    ))


def compare(name: str, mine: list[float], want: list[float]) -> None:
    """한 열을 통째로 맞댄다. 어긋나면 **몇 번째 연차** 인지까지 말한다."""
    assert len(mine) == len(want), f"{name}: 연차 수가 다르다"
    off = []
    for t, (got, expected) in enumerate(zip(mine, want)):
        scale = max(abs(got), abs(expected), 1.0)
        if abs(got - expected) / scale > TOLERANCE:
            off.append(f"t={t} 표본={expected!r} 우리={got!r}")
    assert not off, f"{name} 어긋남 {len(off)}개 — " + " · ".join(off[:5])


# ── 퇴직급여 ────────────────────────────────────────────────────────


def test_the_year_axis_matches(sample) -> None:
    """근속·임금·할인계수·잔존확률. 여기가 어긋나면 뒤는 볼 것도 없다."""
    rows = severance_of(sample, reference.ALLOCATION_SERVICE)
    want = sample["기대값"]
    compare("귀속근속(D)", [r.alloc_service for r in rows], want["D"])
    compare("총근속(E)", [r.total_service for r in rows], want["E"])
    compare("할인계수(I)", [r.discount for r in rows], want["I"])
    compare("연초 재직(N)", [r.at_work for r in rows], want["N"])
    compare("중도(O)", [r.voluntary for r in rows], want["O"])
    compare("사망(P)", [r.death for r in rows], want["P"])
    compare("명예퇴직(Q)", [r.early for r in rows], want["Q"])
    compare("연말 잔존(R)", [r.surviving for r in rows], want["R"])
    compare("임금(S)", [r.wage for r in rows], want["S"])


def test_service_allocation_matches_cell_by_cell(sample) -> None:
    """근속기간할당 — 사유별 채무와 근무원가를 한 칸씩."""
    rows = severance_of(sample, reference.ALLOCATION_SERVICE)
    want = sample["기대값"]
    compare("정년 채무(Y)", [r.dbo_normal for r in rows], want["Y"])
    compare("중도 채무(Z)", [r.dbo_voluntary for r in rows], want["Z"])
    compare("사망 채무(AA)", [r.dbo_death for r in rows], want["AA"])
    compare("명퇴 채무(AB)", [r.dbo_early for r in rows], want["AB"])
    compare("채무 합(AC)", [r.dbo for r in rows], want["AC"])
    compare("정년 원가(AI)", [r.cost_normal for r in rows], want["AI"])
    compare("중도 원가(AJ)", [r.cost_voluntary for r in rows], want["AJ"])
    compare("사망 원가(AK)", [r.cost_death for r in rows], want["AK"])
    compare("원가 합(AM)", [r.service_cost for r in rows], want["AM"])
    compare("할인전 현금흐름(AP)", [r.cash_flow for r in rows], want["AP"])


def test_multiple_allocation_matches_cell_by_cell(sample) -> None:
    """지급률할당 — 스위치를 넘겼을 때의 열(AD~AH)."""
    rows = severance_of(sample, reference.ALLOCATION_MULTIPLE)
    want = sample["기대값"]
    compare("정년 채무(AD)", [r.dbo_normal for r in rows], want["AD"])
    compare("중도 채무(AE)", [r.dbo_voluntary for r in rows], want["AE"])
    compare("사망 채무(AF)", [r.dbo_death for r in rows], want["AF"])
    compare("명퇴 채무(AG)", [r.dbo_early for r in rows], want["AG"])
    compare("채무 합(AH)", [r.dbo for r in rows], want["AH"])


def test_the_two_allocations_do_not_agree(sample) -> None:
    """두 방식은 **다른 답** 을 낸다 — 스위치가 실제로 갈리는지 확인한다.

    누진 지급률이 걸린 규정에서 근속비와 지급률비는 같은 값이 아니다. 둘이
    우연히 같아지면 스위치가 어딘가에서 무시되고 있다는 뜻이다.
    """
    by_service = sum(r.dbo for r in severance_of(
        sample, reference.ALLOCATION_SERVICE))
    by_multiple = sum(r.dbo for r in severance_of(
        sample, reference.ALLOCATION_MULTIPLE))
    assert by_service > 0 and by_multiple > 0
    assert abs(by_multiple - by_service) / by_service > 0.1


# ── 장기급여 · 추가지급 ─────────────────────────────────────────────


def test_long_term_matches_cell_by_cell(sample) -> None:
    """장기급여 네 갈래(평균임금·금등 현물·현금·휴가일수)."""
    rows = long_term_of(sample)
    want = sample["기대값"]
    compare("총근속(AW)", [r.total_service for r in rows], want["AW"])
    compare("할인계수(BG)", [r.discount for r in rows], want["BG"])
    compare("연초 재직(BA)", [r.at_work for r in rows], want["BA"])
    compare("연말 잔존(BD)", [r.surviving for r in rows], want["BD"])
    compare("평균임금(BE)", [r.wage for r in rows], want["BE"])
    compare("日기본급(BF)", [r.daily_wage for r in rows], want["BF"])
    compare("평균임금분(BP)", [r.dbo_wage for r in rows], want["BP"])
    compare("금등 현물(BQ)", [r.dbo_gold for r in rows], want["BQ"])
    compare("현금(BR)", [r.dbo_cash for r in rows], want["BR"])
    compare("휴가일수(BS)", [r.dbo_leave for r in rows], want["BS"])
    compare("채무 합(BT)", [r.dbo for r in rows], want["BT"])
    compare("근무원가(BU)", [r.service_cost for r in rows], want["BU"])


def test_extra_pay_matches_cell_by_cell(sample) -> None:
    """추가지급(전별금) — 중도·사망을 합쳐 세는 급부."""
    rows = extra_pay_of(sample)
    want = sample["기대값"]
    compare("근속(CL)", [r.service for r in rows], want["CL"])
    compare("기본급(CM)", [r.base_pay for r in rows], want["CM"])
    compare("채무(CO)", [r.dbo for r in rows], want["CO"])
    compare("근무원가(CQ)", [r.service_cost for r in rows], want["CQ"])


# ── 합계 ────────────────────────────────────────────────────────────


def test_totals_match_the_sample(sample) -> None:
    """표본이 화면에 띄우는 요약 숫자와 맞는지.

    합계는 열별 합에서 다시 나오므로 앞의 시험들이 통과하면 따라오지만, 요약을
    내는 :func:`pension.reference.totals` 가 엉뚱한 열을 더하는 실수는 여기서만
    잡힌다.
    """
    want = sample["기대값"]
    rows = severance_of(sample, reference.ALLOCATION_SERVICE)
    summary = reference.totals(rows)
    assert summary["확정급여채무"] == pytest.approx(sum(want["AC"]), rel=TOLERANCE)
    assert summary["당기근무원가"] == pytest.approx(sum(want["AM"]), rel=TOLERANCE)
    assert summary["할인전 현금흐름"] == pytest.approx(sum(want["AP"]), rel=TOLERANCE)
    # 만기 = Σ(채무 × 시점) ÷ Σ채무. 표본의 AO 열이 그 분자다.
    assert summary["가중평균만기"] == pytest.approx(
        sum(want["AO"]) / sum(want["AC"]), rel=TOLERANCE)

    long_term = reference.totals(long_term_of(sample))
    assert long_term["확정급여채무"] == pytest.approx(sum(want["BT"]), rel=TOLERANCE)
    assert long_term["당기근무원가"] == pytest.approx(sum(want["BU"]), rel=TOLERANCE)
    assert long_term["가중평균만기"] == pytest.approx(
        sum(want["CA"]) / sum(want["BT"]), rel=TOLERANCE)


def test_the_sample_carries_no_personal_data() -> None:
    """표본에 사람을 가리키는 것이 남아 있지 않은지.

    저장소가 공개다. 근속연수와 연령만 있으면 같은 값이 나오므로, 사번·이름·
    생년월일·날짜는 애초에 담지 않는다. 다음에 표본을 다시 뜰 때 무심코
    딸려 들어오는 것을 여기서 막는다.
    """
    import re

    text = CASE.read_text(encoding="utf-8")
    assert "사번" not in text and "생년월일" not in text and "이름" not in text
    # 날짜꼴 8자리(20221231). 월·일 자리까지 봐야 금액과 갈린다 — 이천만원을
    # 적은 ``20000000`` 은 월이 ``00`` 이라 날짜가 아니다.
    assert not re.search(
        r"(?<![\d.])(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?![\d.])",
        text)
