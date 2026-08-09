"""계리 기초율(가정) 정의와 로딩.

원본 통합문서의 종전 규칙 주석은 ``지급률``, ``지급률(사망)``, ``지급률(정년)`` 같은
기초율 시트를 참조하지만 배포된 샘플 파일에는 명부 시트만 들어 있다. 그래서
기초율은 별도 워크북으로 분리하고, 그 서식을 여기서 정의한다.
:func:`write_template` 로 빈 양식을 만들어 채워 넣으면 된다.

기초율 워크북 시트 구성::

    할인율        연차 | 할인율            (1행짜리면 전 기간 단일 할인율)
    임금상승률    연차 | 상승률            (Base-up)
    승급률        기준(연령/근속) | 규정명들…
    퇴직률        기준(연령/근속) | 규정명들…
    사망률        연령 | 남자 | 여자
    지급률        근속연수 | 규정명들…     (월평균임금 대비 누적 배수)
    장기급여지급률 근속연수 | 규정명들…    (일 기본급 대비 지급일수)

모든 표는 **계단식 조회** 다. 찾는 키보다 작거나 같은 가장 큰 행의 값을 쓴다.
예를 들어 퇴직률 표에 20·25·30세만 있으면 27세는 25세 행의 값을 쓴다.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from .formula import Formula, FormulaError
from .normalize import Gender, text

__all__ = [
    "ATTRIBUTIONS",
    "ATTRIB_IMMEDIATE",
    "ATTRIB_SERVICE",
    "BENEFIT_MODES",
    "CAUSE_DEATH",
    "CAUSE_NORMAL",
    "CAUSE_VOLUNTARY",
    "CUMULATIVE",
    "EXIT_CAUSES",
    "FORMULA",
    "LONGTERM_TYPES",
    "LT_AVERAGE_WAGE",
    "LT_CASH",
    "LT_IN_KIND",
    "LT_VACATION",
    "PROGRESSIVE",
    "STATUTORY_RULE",
    "Assumptions",
    "BenefitScale",
    "CauseBenefit",
    "CauseBenefits",
    "DiscountCurve",
    "LongTermRule",
    "MortalityTable",
    "RateCurve",
    "RateTable",
    "SalaryScale",
    "load_assumptions",
    "write_template",
]

STATUTORY_RULE: Final = "법정"
"""지급률 규정을 찾지 못했을 때 쓰는 기본 규정명.

근로자퇴직급여보장법의 법정 퇴직금(계속근로 1년당 30일분 평균임금)을 뜻한다.
30일 평균임금이 급여 기준이므로 지급 배수는 근속연수와 같다.
"""

DISCOUNT_SHEET: Final = "할인율"
SALARY_SHEET: Final = "임금상승률"
PROMOTION_SHEET: Final = "승급률"
WITHDRAWAL_SHEET: Final = "퇴직률"
MORTALITY_SHEET: Final = "사망률"
BENEFIT_SHEET: Final = "지급률"
LONGTERM_SHEET: Final = "장기급여지급률"
BENEFIT_RULE_SHEET: Final = "지급률규정"
"""규정별 방식(누적/누진/수식)과 수식 코드를 적는 시트."""
LONGTERM_RULE_SHEET: Final = "장기급여규정"
"""장기급여 규정별 지급유형과 현물 상승률을 적는 시트."""
EXIT_CAUSE_SHEET: Final = "퇴직사유"
"""퇴직사유(중도/사망/정년)별 지급 차등을 적는 시트."""

# ── 장기급여 지급유형 ────────────────────────────────────────────
# 근속 포상은 회사마다 주는 것이 다르다. 실제 규정에서 확인한 형태:
#   '휴가 10일', '현물 포상 + 기념품', '현물 포상', '평균임금의 500%', '100만원'
LT_VACATION: Final = "휴가"
"""일 기본급 × 지급일수. 임금상승률을 반영한다."""
LT_AVERAGE_WAGE: Final = "평균임금"
"""30일 평균임금 × 배수. 임금상승률을 반영한다."""
LT_IN_KIND: Final = "현물"
"""금·물품 등. 평가시점 시세를 정액으로 넣고 **현물 상승률** 로 올린다."""
LT_CASH: Final = "현금"
"""정액 현금. 규정 금액이 고정이므로 올리지 않는다."""

LONGTERM_TYPES: Final = (LT_VACATION, LT_AVERAGE_WAGE, LT_IN_KIND, LT_CASH)

DEFAULT_IN_KIND_ESCALATION: Final = 0.03
"""현물 상승률 기본값. 시세를 모를 때 쓰는 값이며 반드시 확인해야 한다."""


@dataclass(slots=True)
class RateCurve:
    """키(연령·근속연수·연차) 하나로 조회하는 계단식 비율 곡선."""

    points: dict[float, float] = field(default_factory=dict)
    whole_key: bool = True
    """조회 키를 정수로 내림할지.

    연령과 근속연수는 **만** 단위로 센다. 만 41.7세는 41세 행을 쓰는 것이 맞다.

    할인율의 연차는 다르다. 만기 3개월·6개월·1년6개월이 표준으로 들어오는데
    내림해 버리면 1년 미만 만기가 모두 0 으로 뭉치고 1년6월이 1년을 덮어쓴다.
    실제 금리표(만기 17개) 중 5개가 1년 미만이거나 반년 단위였다.
    """
    _keys: list[float] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._keys = sorted(self.points)

    def rate(self, key: float) -> float:
        """``key`` 이하의 가장 큰 구간 값. 표가 비었거나 키가 표보다 작으면 0."""
        if not self._keys:
            return 0.0
        lookup = math.floor(key) if self.whole_key else key
        idx = bisect_right(self._keys, lookup) - 1
        if idx < 0:
            return self.points[self._keys[0]]
        return self.points[self._keys[idx]]

    def scaled(self, factor: float) -> RateCurve:
        """모든 값에 배수를 적용한 새 곡선. 민감도 분석에 쓴다."""
        return RateCurve({k: v * factor for k, v in self.points.items()}, self.whole_key)

    def shifted(self, delta: float) -> RateCurve:
        """모든 값에 절대값을 더한 새 곡선(음수는 0 으로 자름)."""
        return RateCurve({k: max(0.0, v + delta) for k, v in self.points.items()}, self.whole_key)

    def __bool__(self) -> bool:
        return bool(self.points)


@dataclass(slots=True)
class RateTable:
    """규정명별 :class:`RateCurve` 묶음(퇴직률·승급률)."""

    basis: str = "연령"
    """조회 기준. ``"연령"`` 또는 ``"근속"``."""
    curves: dict[str, RateCurve] = field(default_factory=dict)
    default_rule: str = ""
    """규정명을 못 찾았을 때 대신 쓸 규정명. 비면 0 을 쓴다."""

    def curve(self, rule: str) -> RateCurve:
        """규정명에 해당하는 곡선. 없으면 빈 곡선(=요율 0).

        **호출부는 규정명이 비어 있지 않도록 보장해야 한다.** 빈 이름으로 물으면
        요율 0 이 조용히 돌아오는데, 퇴직률 0 은 '아무도 중도퇴직하지 않는다',
        승급률 0 은 '호봉 인상이 없다' 는 뜻이라 채무가 통째로 어긋난다. 실제로
        규정명 칸이 비어 있는 명부에서 기초율 시트가 통째로 무시된 적이 있다.
        그래서 :mod:`pension.valuation` 과 :mod:`pension.longterm` 은 규정명이
        비면 변환 직군명으로 대신 묻는다.
        """
        curve = self.curves.get(text(rule))
        if curve is None and self.default_rule:
            curve = self.curves.get(self.default_rule)
        return curve or RateCurve()

    def rate(self, rule: str, *, age: float, service: float) -> float:
        key = age if self.basis == "연령" else service
        return self.curve(rule).rate(key)

    def transformed(self, fn) -> RateTable:
        """모든 곡선에 같은 변환을 적용한 사본."""
        return RateTable(
            basis=self.basis,
            curves={name: fn(curve) for name, curve in self.curves.items()},
            default_rule=self.default_rule,
        )


@dataclass(slots=True)
class MortalityTable:
    """성별 연령별 사망률 ``qx``."""

    male: RateCurve = field(default_factory=RateCurve)
    female: RateCurve = field(default_factory=RateCurve)

    def qx(self, gender: Gender, age: float) -> float:
        curve = self.female if gender is Gender.FEMALE else self.male
        return min(1.0, max(0.0, curve.rate(age)))

    def scaled(self, factor: float) -> MortalityTable:
        return MortalityTable(self.male.scaled(factor), self.female.scaled(factor))

    def __bool__(self) -> bool:
        return bool(self.male) or bool(self.female)


@dataclass(slots=True)
class DiscountCurve:
    """할인율 기간구조.

    한 행만 주면 전 기간 단일 할인율로 본다. 여러 행이면 각 연차의 **현물이자율
    (spot rate)** 로 보고 ``v(t) = (1 + s_t)^-t`` 로 할인한다.
    """

    spot: RateCurve = field(default_factory=RateCurve)
    flat: float | None = None

    def rate(self, t: float) -> float:
        if self.flat is not None:
            return self.flat
        return self.spot.rate(t)

    def discount_factor(self, t: float) -> float:
        """``t`` 년 뒤 현금흐름의 현가계수."""
        if t <= 0:
            return 1.0
        return (1.0 + self.rate(t)) ** (-t)

    def shifted(self, delta: float) -> DiscountCurve:
        if self.flat is not None:
            return DiscountCurve(flat=max(0.0, self.flat + delta))
        return DiscountCurve(spot=self.spot.shifted(delta))

    @property
    def level_rate(self) -> float:
        """이자원가 계산 등에 쓰는 대표 할인율(1년 시점 기준)."""
        return self.rate(1)


@dataclass(slots=True)
class SalaryScale:
    """임금상승률.

    총 상승률 = **Base-up**(연차별 공통) + **승급률**(규정·연령/근속별)로 본다.
    ``1)일반사항`` 시트가 "승진·승급으로 인한 임금인상 제외" 한 Base-up 을 따로
    받는 것과 같은 구조다.
    """

    base_up: RateCurve = field(default_factory=RateCurve)
    promotion: RateTable = field(default_factory=RateTable)

    def rate(self, rule: str, *, year: int, age: float, service: float) -> float:
        return self.base_up.rate(year) + self.promotion.rate(rule, age=age, service=service)

    def shifted(self, delta: float) -> SalaryScale:
        return SalaryScale(base_up=self.base_up.shifted(delta), promotion=self.promotion)


STATUTORY_MODE: Final = "법정"
"""기본 방식. 지급률 표를 비워 두면 법정 퇴직금(배수 = 근속연수, 연속)이고,
표에 값을 넣으면 그 값을 누적 배수로 쓴다.

``누적`` 과 엔진 동작이 같다. 이름을 따로 둔 이유는 화면에서 **아무것도 설정하지
않았을 때 무엇이 적용되는지** 를 보이게 하기 위해서다. '누적' 이라고만 적혀
있으면 표가 비었을 때 배수가 0 이 되는지 법정이 되는지 알 수 없다.
"""

CUMULATIVE: Final = "누적"
"""지급률 방식 — 표 값이 해당 근속연수의 **누적** 배수 그 자체."""

PROGRESSIVE: Final = "누진"
"""지급률 방식 — 표 값이 **그 구간에서만** 적용되는 배수. 구간별로 쌓아 합산한다."""

FORMULA: Final = "수식"
"""지급률 방식 — 배수를 수식으로 직접 계산."""

BENEFIT_MODES: Final = (STATUTORY_MODE, CUMULATIVE, PROGRESSIVE, FORMULA)


def _progressive_multiple(curve: RateCurve, service: float) -> float:
    """누진 구간표의 누적 배수.

    표가 ``{0: 1.0, 5: 1.5, 10: 2.0}`` 이면 "0~5년 구간은 연 1.0배, 5~10년 구간은
    연 1.5배, 10년 이후는 연 2.0배" 라는 뜻이다. 근속 12년이면::

        5 × 1.0  +  5 × 1.5  +  2 × 2.0  =  16.5

    누진제 퇴직금 규정을 표만으로 적을 수 있어, 수식을 쓰지 않아도 되는 경우가
    대부분이다.
    """
    if service <= 0 or not curve.points:
        return 0.0

    bounds = sorted(curve.points)
    total = 0.0
    for index, start in enumerate(bounds):
        if service <= start:
            break
        end = bounds[index + 1] if index + 1 < len(bounds) else service
        span = min(service, end) - start
        if span > 0:
            total += span * curve.points[start]
    return total


@dataclass(slots=True)
class BenefitScale:
    """지급률 규정 묶음.

    퇴직급여는 근속연수에 대한 **월평균임금 배수**(법정이면 근속연수와 동일),
    장기급여는 **일 기본급 대비 지급일수** 를 담는다.

    규정마다 세 가지 방식 중 하나를 쓴다.

    ``누적``
        표 값이 그대로 누적 배수. 계단식 조회를 한다(기본값).
    ``누진``
        표 값이 구간별 연 배수. :func:`_progressive_multiple` 로 합산한다.
    ``수식``
        :class:`~pension.formula.Formula` 로 계산한다.
    """

    curves: dict[str, RateCurve] = field(default_factory=dict)
    statutory_when_missing: bool = True
    """규정을 못 찾으면 법정 퇴직금(배수 = 근속연수)으로 볼지."""

    modes: dict[str, str] = field(default_factory=dict)
    """규정명 → 방식. 없으면 ``누적``."""

    formulas: dict[str, Any] = field(default_factory=dict)
    """규정명 → :class:`Formula`. ``수식`` 방식일 때 쓴다."""

    def mode(self, rule: str) -> str:
        return self.modes.get(text(rule), CUMULATIVE)

    def multiple(self, rule: str, service: float, **context: Any) -> float:
        """근속 ``service`` 년의 지급 배수.

        :param context: 수식 방식에서 쓸 추가 변수(``x``, ``N``, ``S``, ``제도`` 등).
            표 방식에서는 무시된다.
        """
        name = text(rule)

        formula = self.formulas.get(name)
        if formula is not None:
            return formula.evaluate(t=service, **context)

        curve = self.curves.get(name)
        if curve is None:
            return service if self.statutory_when_missing else 0.0
        if self.mode(name) == PROGRESSIVE:
            return _progressive_multiple(curve, service)
        return curve.rate(service)

    def milestones(self, rule: str) -> list[tuple[int, float]]:
        """(근속연수, 지급값) 목록. 장기급여의 지급 시점 산정에 쓴다."""
        curve = self.curves.get(text(rule))
        if curve is None:
            return []
        return sorted(curve.points.items())

    def has_rule(self, rule: str) -> bool:
        name = text(rule)
        return name in self.curves or name in self.formulas


CAUSE_VOLUNTARY: Final = "중도"
"""퇴직사유 — 자발적 중도퇴직."""

CAUSE_DEATH: Final = "사망"
"""퇴직사유 — 재직 중 사망."""

CAUSE_NORMAL: Final = "정년"
"""퇴직사유 — 정년 도달."""

EXIT_CAUSES: Final = (CAUSE_VOLUNTARY, CAUSE_DEATH, CAUSE_NORMAL)

ATTRIB_SERVICE: Final = "근속비례"
"""가산 귀속 — 근속에 따라 나누어 쌓는다."""

ATTRIB_IMMEDIATE: Final = "즉시"
"""가산 귀속 — 전액을 지금 귀속한다."""

ATTRIBUTIONS: Final = (ATTRIB_SERVICE, ATTRIB_IMMEDIATE)


@dataclass(slots=True, frozen=True)
class CauseBenefit:
    """퇴직사유 하나에 붙는 지급 규정.

    같은 회사라도 중도퇴직·사망·정년퇴직의 지급률이 다른 경우가 흔하다.
    자료요청서 6번에도 세 줄이 따로 있다.

        중도퇴직시 퇴직금 지급률   6월이상 1년 미만은 1년분, 6월 미만은 1/2
        사망시 퇴직금 지급률       동일, 정액 가산금 5,000만원
        정년퇴직시 퇴직금 지급률   임원 배수 별도

    비워 둔 항목은 기본 지급률 규정을 그대로 쓴다.
    """

    benefit_rule: str = ""
    """이 사유일 때 대신 쓸 지급률 규정명. 비면 기본 규정."""
    extra_rule: str = ""
    """기본 급여에 **더할** 배수를 내는 지급률 규정명.

    '사망 시 기본급 3개월분 가산' 처럼 얹어 주는 몫이다. 근속에 따라 달라지면
    (10년 미만 3개월 / 이상 5개월) 지급률 표나 수식으로 적는다.
    """
    extra_amount: float = 0.0
    """정액 가산액(원). '정액 가산금 5,000만원' 같은 것."""
    min_service: float = 0.0
    """이 사유의 근속 하한(년).

    '재직 중 사망으로 퇴직 시 1년 미만도 1년으로 계산' 을 담는다. 가입자격
    문턱과 다르다 — 문턱은 못 넘으면 0 원이고, 이것은 짧은 근속을 끌어올린다.
    """
    attribution: str = ""
    """가산분의 귀속 방식. 비면 사유별 기본값(:meth:`attribution_basis`)."""

    def attribution_basis(self, cause: str) -> str:
        """가산분을 어떻게 귀속할지.

        사망 가산은 기본이 ``즉시`` 다. 재직 중 사망하면 근속이 하루든 20년이든
        같은 금액을 주므로, 더 일해도 급여가 늘지 않는다 — 문단 70 은 그 시점에
        귀속을 멈추라고 한다. 반대로 정년 가산은 정년까지 남아야 받으므로
        ``근속비례`` 로 쌓는다.
        """
        if self.attribution:
            return self.attribution
        return ATTRIB_IMMEDIATE if cause == CAUSE_DEATH else ATTRIB_SERVICE

    def is_empty(self) -> bool:
        return not (
            self.benefit_rule or self.extra_rule
            or self.extra_amount or self.min_service
        )


@dataclass(slots=True)
class CauseBenefits:
    """``(지급률 규정, 퇴직사유) → 사유별 규정`` 묶음."""

    rules: dict[tuple[str, str], CauseBenefit] = field(default_factory=dict)

    def get(self, rule: str, cause: str) -> CauseBenefit:
        return self.rules.get((text(rule), text(cause)), _NO_CAUSE_BENEFIT)

    def is_empty(self) -> bool:
        return not self.rules

    def rule_names(self) -> list[str]:
        """사유별 규정이 참조하는 지급률 규정명 전부. 검증에 쓴다."""
        names: list[str] = []
        for entry in self.rules.values():
            for name in (entry.benefit_rule, entry.extra_rule):
                if name and name not in names:
                    names.append(name)
        return names


_NO_CAUSE_BENEFIT: Final = CauseBenefit()


@dataclass(slots=True)
class LongTermRule:
    """장기급여 규정 하나의 지급유형.

    ``장기급여지급률`` 표의 값이 무엇을 뜻하는지는 유형에 따라 달라진다.

    ==========  ====================================================
    유형        표 값의 뜻
    ==========  ====================================================
    휴가        지급일수 (일 기본급 × 일수)
    평균임금    배수 (30일 평균임금 × 배수)
    현물        정액(원) — 평가시점 시세로 환산해 넣는다
    현금        정액(원)
    ==========  ====================================================
    """

    kind: str = LT_VACATION
    escalation: float = 0.0
    """현물 상승률(연). ``현물`` 유형에만 쓴다."""
    note: str = ""
    """'현물 포상 @ 2025-12-31 시세' 처럼 환산 근거를 남긴다."""


@dataclass(slots=True)
class Assumptions:
    """계리 산출에 필요한 기초율 일체."""

    discount: DiscountCurve = field(default_factory=DiscountCurve)
    salary: SalaryScale = field(default_factory=SalaryScale)
    withdrawal: RateTable = field(default_factory=RateTable)
    """중도퇴직률(사망 제외)."""
    mortality: MortalityTable = field(default_factory=MortalityTable)
    severance_benefit: BenefitScale = field(default_factory=BenefitScale)
    longterm_benefit: BenefitScale = field(
        default_factory=lambda: BenefitScale(statutory_when_missing=False)
    )
    longterm_rules: dict[str, LongTermRule] = field(default_factory=dict)
    """장기급여 규정명 → 지급유형. 없으면 ``휴가`` 로 본다(기존 동작)."""

    exit_causes: CauseBenefits = field(default_factory=CauseBenefits)
    """퇴직사유별 지급 차등. 비면 사유를 가리지 않는다(기존 동작)."""

    def longterm_rule(self, rule: str) -> LongTermRule:
        return self.longterm_rules.get(text(rule), LongTermRule())

    label: str = "당기 가정"
    """리포트에 표시할 이름. 민감도·증감분석에서 구분자로 쓴다."""

    max_projection_years: int = 60
    """투영 상한. 정년연령이 잘못 들어와도 무한 루프에 빠지지 않게 한다."""

    def replace(self, **changes: Any) -> Assumptions:
        """일부 가정만 바꾼 사본. 민감도 분석용."""
        from dataclasses import replace as _replace

        return _replace(self, **changes)


# ────────────────────────────────────────────────────────────────
# 워크북 입출력
# ────────────────────────────────────────────────────────────────


def _rows(ws, first_row: int = 2) -> Iterable[tuple[int, list[Any]]]:
    for row in range(first_row, ws.max_row + 1):
        values = [ws.cell(row, c).value for c in range(1, ws.max_column + 1)]
        if all(v in (None, "") for v in values):
            continue
        yield row, values


def _as_number(value: object) -> float | None:
    """숫자 셀 해석. ``%`` 표기는 100 으로 나눈다."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    token = text(value).replace(",", "")
    percent = token.endswith("%")
    if percent:
        token = token[:-1]
    try:
        number = float(token)
    except ValueError:
        return None
    return number / 100.0 if percent else number


def _as_rate(value: object) -> float | None:
    """비율 셀 해석. ``5%`` / ``0.05`` / ``5`` 를 모두 0.05 로 읽는다.

    엑셀 백분율 서식은 이미 0.05 로 저장되므로, 1 을 넘는 값만 "퍼센트 단위로
    적은 것" 으로 본다. 비율은 1(=100%)을 넘을 수 없으니 안전한 규칙이다.
    지급률처럼 1 을 넘을 수 있는 값에는 :func:`_as_number` 를 쓴다.
    """
    number = _as_number(value)
    if number is None:
        return None
    return number / 100.0 if number > 1.0 else number


def _as_int(value: object) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(float(text(value)))
    except ValueError:
        return None


def _read_rate_table(wb, sheet_name: str, *, as_rate: bool = True) -> RateTable:
    """규정명이 열 머리글인 표를 :class:`RateTable` 로.

    :param as_rate: 값을 비율(0~1)로 볼지. 지급률처럼 1 을 넘는 배수는 ``False``.
    """
    if sheet_name not in wb.sheetnames:
        return RateTable()
    ws = wb[sheet_name]
    parse = _as_rate if as_rate else _as_number

    header = [text(ws.cell(1, c).value) for c in range(1, ws.max_column + 1)]
    if not header:
        return RateTable()
    basis = "근속" if "근속" in header[0] else "연령"

    rules = [(idx + 2, name) for idx, name in enumerate(header[1:]) if name]
    curves: dict[str, dict[int, float]] = {name: {} for _, name in rules}

    for row, _ in _rows(ws):
        key = _as_int(ws.cell(row, 1).value)
        if key is None:
            continue
        for col, name in rules:
            rate = parse(ws.cell(row, col).value)
            if rate is not None:
                curves[name][key] = rate

    table = RateTable(
        basis=basis,
        curves={name: RateCurve(points) for name, points in curves.items() if points},
    )
    # 규정이 하나뿐이면 이름을 못 맞춰도 그 값을 쓰도록 기본값으로 지정한다.
    if len(table.curves) == 1:
        table.default_rule = next(iter(table.curves))
    return table


def _read_rule_modes(wb) -> tuple[dict[str, str], dict[str, Formula], list[str]]:
    """``지급률규정`` 시트에서 규정별 방식과 수식을 읽는다.

    시트가 없으면 전부 ``누적`` 방식으로 본다(기존 파일과 호환).

    :returns: (방식 표, 수식 표, 오류 메시지 목록)
    """
    modes: dict[str, str] = {}
    formulas: dict[str, Formula] = {}
    problems: list[str] = []

    if BENEFIT_RULE_SHEET not in wb.sheetnames:
        return modes, formulas, problems

    ws = wb[BENEFIT_RULE_SHEET]
    for row, _ in _rows(ws):
        name = text(ws.cell(row, 1).value)
        if not name:
            continue
        mode = text(ws.cell(row, 2).value) or CUMULATIVE
        if mode not in BENEFIT_MODES:
            problems.append(
                f"{BENEFIT_RULE_SHEET}!B{row}: 방식 '{mode}' 을(를) 알 수 없습니다 "
                f"({' / '.join(BENEFIT_MODES)} 중 하나)"
            )
            continue
        modes[name] = mode

        source = text(ws.cell(row, 3).value)
        if mode == FORMULA:
            if not source:
                problems.append(f"{BENEFIT_RULE_SHEET}!C{row}: '{name}' 은 수식 방식인데 수식이 비어 있습니다")
                continue
            try:
                formulas[name] = Formula(source)
            except FormulaError as exc:
                problems.append(f"{BENEFIT_RULE_SHEET}!C{row}: {exc}")
        elif source:
            problems.append(
                f"{BENEFIT_RULE_SHEET}!C{row}: '{name}' 은 {mode} 방식이라 수식을 쓰지 않습니다"
                " (방식을 '수식'으로 바꾸거나 수식을 비우세요)"
            )

    return modes, formulas, problems


def _read_benefit_scale(
    wb,
    sheet_name: str,
    *,
    statutory: bool,
    modes: dict[str, str] | None = None,
    formulas: dict[str, Formula] | None = None,
) -> BenefitScale:
    """지급률 표. 값은 배수·일수이므로 비율 환산을 하지 않는다."""
    table = _read_rate_table(wb, sheet_name, as_rate=False)
    return BenefitScale(
        curves=dict(table.curves),
        statutory_when_missing=statutory,
        modes=dict(modes or {}),
        formulas=dict(formulas or {}),
    )


def _read_single_curve(wb, sheet_name: str, *, whole_key: bool = True) -> RateCurve:
    """A열 키 · B열 비율 두 칸짜리 시트를 곡선으로 읽는다.

    :param whole_key: 키를 정수로 볼지. 할인율의 연차만 실수로 읽는다 —
        만기 3월·6월·1년6월이 표준으로 들어오는데 정수로 끊으면 1년 미만이
        모두 0 으로 뭉치고 1년6월이 1년을 덮어쓴다.
    """
    if sheet_name not in wb.sheetnames:
        return RateCurve(whole_key=whole_key)
    ws = wb[sheet_name]
    points: dict[float, float] = {}
    for row, _ in _rows(ws):
        key = _as_int(ws.cell(row, 1).value) if whole_key else _as_number(ws.cell(row, 1).value)
        rate = _as_rate(ws.cell(row, 2).value)
        if key is not None and rate is not None:
            points[key] = rate
    return RateCurve(points, whole_key)


def _read_mortality(wb) -> MortalityTable:
    if MORTALITY_SHEET not in wb.sheetnames:
        return MortalityTable()
    ws = wb[MORTALITY_SHEET]
    male: dict[int, float] = {}
    female: dict[int, float] = {}
    for row, _ in _rows(ws):
        age = _as_int(ws.cell(row, 1).value)
        if age is None:
            continue
        m = _as_rate(ws.cell(row, 2).value)
        f = _as_rate(ws.cell(row, 3).value)
        if m is not None:
            male[age] = m
        if f is not None:
            female[age] = f
    return MortalityTable(RateCurve(male), RateCurve(female))


def _read_longterm_rules(wb) -> tuple[dict[str, LongTermRule], list[str]]:
    """``장기급여규정`` 시트에서 규정별 지급유형을 읽는다.

    시트가 없으면 빈 표를 돌려준다 — 그러면 전부 ``휴가`` 로 보아 기존 동작과
    같아진다.
    """
    rules: dict[str, LongTermRule] = {}
    problems: list[str] = []
    if LONGTERM_RULE_SHEET not in wb.sheetnames:
        return rules, problems

    ws = wb[LONGTERM_RULE_SHEET]
    for row, _ in _rows(ws):
        name = text(ws.cell(row, 1).value)
        if not name:
            continue
        kind = text(ws.cell(row, 2).value) or LT_VACATION
        if kind not in LONGTERM_TYPES:
            problems.append(
                f"{LONGTERM_RULE_SHEET}!B{row}: 지급유형 '{kind}' 을(를) 알 수 없습니다 "
                f"({' / '.join(LONGTERM_TYPES)} 중 하나)"
            )
            continue

        escalation = _as_rate(ws.cell(row, 3).value) or 0.0
        if kind != LT_IN_KIND and escalation:
            problems.append(
                f"{LONGTERM_RULE_SHEET}!C{row}: '{name}' 은 {kind} 유형이라 현물 상승률을 "
                "쓰지 않습니다 (유형을 '현물'로 바꾸거나 상승률을 비우세요)"
            )
        rules[name] = LongTermRule(
            kind=kind,
            escalation=escalation if kind == LT_IN_KIND else 0.0,
            note=text(ws.cell(row, 4).value),
        )
    return rules, problems


def _read_exit_causes(wb) -> tuple[CauseBenefits, list[str]]:
    """``퇴직사유`` 시트에서 사유별 지급 차등을 읽는다.

    시트가 없으면 빈 표를 돌려준다 — 그러면 사유를 가리지 않아 기존 동작과
    같아진다.
    """
    result = CauseBenefits()
    problems: list[str] = []
    if EXIT_CAUSE_SHEET not in wb.sheetnames:
        return result, problems

    ws = wb[EXIT_CAUSE_SHEET]
    for row, _ in _rows(ws):
        rule = text(ws.cell(row, 1).value)
        if not rule or rule.startswith(("·", "*", "※", "#")):
            continue
        cause = text(ws.cell(row, 2).value)
        if cause not in EXIT_CAUSES:
            problems.append(
                f"{EXIT_CAUSE_SHEET}!B{row}: 퇴직사유 '{cause}' 을(를) 알 수 없습니다 "
                f"({' / '.join(EXIT_CAUSES)} 중 하나)"
            )
            continue

        basis = text(ws.cell(row, 7).value)
        if basis and basis not in ATTRIBUTIONS:
            problems.append(
                f"{EXIT_CAUSE_SHEET}!G{row}: 가산 귀속 '{basis}' 을(를) 알 수 없습니다 "
                f"({' / '.join(ATTRIBUTIONS)} 중 하나)"
            )
            continue

        entry = CauseBenefit(
            benefit_rule=text(ws.cell(row, 3).value),
            extra_rule=text(ws.cell(row, 4).value),
            extra_amount=_as_number(ws.cell(row, 5).value) or 0.0,
            min_service=_as_number(ws.cell(row, 6).value) or 0.0,
            attribution=basis,
        )
        if entry.is_empty():
            # 한 칸도 안 채운 줄은 규정이 아니다. 담아 두면 '사유별 차등이
            # 있다' 고 잘못 읽힌다.
            continue
        if (rule, cause) in result.rules:
            problems.append(
                f"{EXIT_CAUSE_SHEET}!A{row}: '{rule} / {cause}' 이 두 번 적혀 있습니다"
            )
            continue
        result.rules[(rule, cause)] = entry

    return result, problems


def load_assumptions(path: str | Path, *, label: str = "당기 가정") -> Assumptions:
    """기초율 워크북을 읽는다.

    :param path: ``.xlsx``/``.xlsm`` 경로.
    :raises FileNotFoundError: 파일이 없을 때.
    :raises ValueError: 할인율 시트가 비어 있을 때(할인율 없이는 산출 불가).
    """
    import openpyxl

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"기초율 파일을 찾을 수 없습니다: {path}")

    wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
    try:
        spot = _read_single_curve(wb, DISCOUNT_SHEET, whole_key=False)
        if not spot:
            raise ValueError(
                f"'{DISCOUNT_SHEET}' 시트에서 할인율을 읽지 못했습니다. "
                "A열에 연차, B열에 할인율을 입력하세요"
            )
        flat = next(iter(spot.points.values())) if len(spot.points) == 1 else None

        modes, formulas, problems = _read_rule_modes(wb)
        longterm_rules, longterm_problems = _read_longterm_rules(wb)
        exit_causes, cause_problems = _read_exit_causes(wb)
        problems = problems + longterm_problems + cause_problems
        if problems:
            raise ValueError(
                "지급 규정을 읽지 못했습니다:\n  · " + "\n  · ".join(problems)
            )

        return Assumptions(
            discount=DiscountCurve(spot=spot, flat=flat),
            salary=SalaryScale(
                base_up=_read_single_curve(wb, SALARY_SHEET),
                promotion=_read_rate_table(wb, PROMOTION_SHEET),
            ),
            withdrawal=_read_rate_table(wb, WITHDRAWAL_SHEET),
            mortality=_read_mortality(wb),
            severance_benefit=_read_benefit_scale(
                wb, BENEFIT_SHEET, statutory=True, modes=modes, formulas=formulas
            ),
            longterm_benefit=_read_benefit_scale(wb, LONGTERM_SHEET, statutory=False),
            longterm_rules=longterm_rules,
            exit_causes=exit_causes,
            label=label,
        )
    finally:
        wb.close()


def _as_stored_formula(source: str) -> str:
    """지급률 수식을 셀에 저장할 형태로.

    앞의 ``=`` 를 떼고 저장한다. 두 가지 이유가 있다.

    1. ``=`` 로 시작하는 문자열을 셀에 넣으면 엑셀 수식 셀이 된다. 그러면
       ``data_only=True`` 로 읽을 때 계산된 값(없으므로 ``None``)이 돌아와,
       수식 규정이 통째로 사라진다.
    2. 엑셀에서 그 파일을 열면 ``t`` 를 모르는 함수로 보고 ``#NAME?`` 를 띄운다.

    :func:`Formula` 는 ``=`` 가 있든 없든 받으므로 읽을 때는 문제가 없다.
    """
    text_value = text(source)
    return text_value[1:].strip() if text_value.startswith("=") else text_value


def write_assumptions(
    path: str | Path,
    sheets: dict[str, tuple[list[str], list[list[Any]]]],
    rules: dict[str, tuple[str, str]] | None = None,
    longterm_rules: dict[str, tuple[str, float, str]] | None = None,
) -> Path:
    """기초율 워크북을 쓴다.

    가정 입력 화면이 모은 값을 파일로 떨구는 통로다. 화면 위젯과 분리해 두어야
    파일 서식을 화면 없이도 검증할 수 있다.

    :param sheets: 시트명 → (머리글 목록, 행 목록).
    :param rules: 규정명 → (방식, 수식). ``지급률규정`` 시트로 나간다.
    """
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    path = Path(path)
    wb = openpyxl.Workbook()
    del wb["Sheet"]

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="44546A")

    def write_sheet(name: str, headers: list[str], rows: list[list[Any]]) -> None:
        ws = wb.create_sheet(name)
        for col, title in enumerate(headers, start=1):
            cell = ws.cell(1, col, title)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")
            ws.column_dimensions[cell.column_letter].width = max(12, len(str(title)) + 4)
        for offset, values in enumerate(rows, start=2):
            for col, value in enumerate(values, start=1):
                ws.cell(offset, col, value)
        ws.freeze_panes = "A2"

    for name, (headers, rows) in sheets.items():
        write_sheet(name, headers, rows)

    if longterm_rules is not None:
        write_sheet(
            LONGTERM_RULE_SHEET,
            ["규정명", "지급유형", "현물 상승률", "환산 근거"],
            [
                [name, kind, escalation or None, note]
                for name, (kind, escalation, note) in longterm_rules.items()
            ],
        )

    if rules is not None:
        write_sheet(
            BENEFIT_RULE_SHEET,
            ["규정명", "방식", "수식", "설명"],
            [
                [name, mode, _as_stored_formula(source), ""]
                for name, (mode, source) in rules.items()
            ],
        )

    wb.save(path)
    return path


def write_template(path: str | Path, *, job_groups: Iterable[str] = ()) -> Path:
    """빈 기초율 워크북 양식을 만든다.

    담당자가 채워 넣을 시트 구조와 예시 몇 줄을 넣어 준다.
    """
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    path = Path(path)
    rules = [text(g) for g in job_groups if text(g)] or ["정규직", "임원"]

    wb = openpyxl.Workbook()
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="44546A")
    note_font = Font(italic=True, color="808080")

    def make(name: str, headers: list[str], note: str, rows: list[list[Any]]) -> None:
        ws = wb.create_sheet(name)
        for col, title in enumerate(headers, start=1):
            cell = ws.cell(1, col, title)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            ws.column_dimensions[cell.column_letter].width = max(12, len(title) + 4)
        for offset, values in enumerate(rows, start=2):
            for col, value in enumerate(values, start=1):
                ws.cell(offset, col, value)
        ws.cell(len(rows) + 3, 1, note).font = note_font
        ws.freeze_panes = "A2"

    make(
        DISCOUNT_SHEET, ["연차", "할인율"],
        "· 한 줄만 넣으면 전 기간 단일 할인율입니다. 여러 줄이면 각 연차의 현물이자율(spot)로 봅니다.",
        [[1, 0.045]],
    )
    make(
        SALARY_SHEET, ["연차", "Base-up 상승률"],
        "· 승진·승급 제외한 공통 인상률입니다. 마지막 줄의 값이 그 이후 전 기간에 적용됩니다.",
        [[1, 0.03], [6, 0.025]],
    )
    make(
        PROMOTION_SHEET, ["연령", *rules],
        "· 승급(호봉·승진) 상승률. A1 을 '근속'으로 바꾸면 근속연수 기준으로 조회합니다.",
        [[20, *[0.02] * len(rules)], [40, *[0.01] * len(rules)], [55, *[0.0] * len(rules)]],
    )
    make(
        WITHDRAWAL_SHEET, ["연령", *rules],
        "· 사망을 제외한 연간 중도퇴직률입니다. A1 을 '근속'으로 바꾸면 근속연수 기준입니다.",
        [[20, *[0.15] * len(rules)], [35, *[0.06] * len(rules)], [50, *[0.02] * len(rules)]],
    )
    make(
        MORTALITY_SHEET, ["연령", "남자", "여자"],
        "· 연간 사망률 qx. 경험생명표 등 사용한 표의 출처를 함께 남겨 두세요.",
        [[20, 0.00040, 0.00020], [40, 0.00120, 0.00060], [60, 0.00600, 0.00250]],
    )
    make(
        BENEFIT_SHEET, ["근속연수", *rules],
        "· 30일 평균임금 대비 지급배수입니다. 비워 두면 법정 퇴직금(배수 = 근속연수)으로 봅니다."
        "\n· 값의 의미는 '지급률규정' 시트의 방식에 따라 달라집니다."
        " 누적=그 근속연수의 누적 배수, 누진=그 구간에서만 적용할 연 배수.",
        [[1, *[1.0] * len(rules)], [10, *[10.0] * len(rules)], [20, *[20.0] * len(rules)]],
    )
    make(
        BENEFIT_RULE_SHEET, ["규정명", "방식", "수식", "설명"],
        "· 방식: 누적(표 값이 누적 배수) / 누진(표 값이 구간별 연 배수) / 수식(아래 수식으로 계산)"
        "\n· 수식 변수: t=근속연수, x=연령, N=정년연령, S=30일 평균임금, 제도, 직군"
        "\n· 수식 함수: IF AND OR NOT MIN MAX ABS ROUND ROUNDDOWN ROUNDUP FLOOR CEILING TRUNC"
        "\n· 수식 예시: =IF(t<10, t*1.0, 10 + (t-10)*2.0)"
        "\n· 되도록 누진 방식(표)을 쓰세요. 수식은 표로 담기 어려운 규정에만 씁니다.",
        [[rule, CUMULATIVE, "", ""] for rule in rules],
    )
    make(
        LONGTERM_SHEET, ["근속연수", *rules],
        "· 근속 포상·장기근속휴가의 지급일수입니다(일 기본급 × 일수). 해당 근속연수 도달 시 지급으로 봅니다.",
        [[10, *[10] * len(rules)], [20, *[20] * len(rules)], [30, *[30] * len(rules)]],
    )
    make(
        EXIT_CAUSE_SHEET,
        ["지급률 규정", "퇴직사유", "대체 지급률 규정", "가산 규정",
         "가산액(원)", "근속 하한(년)", "가산 귀속"],
        "· 중도퇴직·사망·정년퇴직의 지급률이 다를 때만 씁니다. 비워 두면 사유를 가리지 않습니다."
        "\n· 퇴직사유: " + " / ".join(EXIT_CAUSES) +
        "\n· 대체 지급률 규정: 그 사유일 때 기본 규정 대신 쓸 '지급률' 시트의 열 이름"
        "\n· 가산 규정: 기본 급여에 **더할** 배수를 내는 규정 (예: 사망 시 기본급 3개월분)"
        "\n· 가산액(원): 정액 가산 (예: 정액 가산금 5,000만원)"
        "\n· 근속 하한(년): '사망 시 1년 미만도 1년으로 계산' 처럼 짧은 근속을 끌어올릴 때"
        "\n· 가산 귀속: " + " / ".join(ATTRIBUTIONS) +
        " — 비우면 사망은 '즉시', 나머지는 '근속비례'"
        "\n· 예) 정규직 | 사망 | (비움) | (비움) | 50000000 | 1 | (비움)",
        [],
    )

    del wb["Sheet"]
    wb.save(path)
    return path
