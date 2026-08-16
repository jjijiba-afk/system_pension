"""시험용 명부 생성기 — 난수로 만드는 세 가지 사례.

기본 명부 하나만으로는 프로그램을 충분히 두드려 볼 수 없다. 실제로 받는
명부는 회사마다 생김새가 크게 다르고, 무엇보다 **이상한 자료가 섞여 온다.**
그래서 성격이 다른 세 벌을 만들어 둔다.

``표준``
    교과서 같은 명부. 값이 모두 갖춰져 있고 검증 오류가 없다. 산출 결과가
    상식적인 범위에 들어오는지 보는 기준선이다.

``복합제도``
    제도와 인사 구조가 복잡한 회사. DC 전환자·중간정산자·임금피크·정년 초과
    재고용·전입자·개별 지급배수가 섞여 있다. **자료 자체는 옳다** — 엔진의
    특수 경로가 제대로 도는지 보는 용도다.

``자료불량``
    실무에서 실제로 받는 명부. 두 가지가 섞여 있다.

    하나는 **틀린 자료** — 날짜 서식이 뒤섞이고 제도구분이 비고 임금이 0 이며
    사번이 중복된다. 검증 리포트가 무엇을 어떻게 잡아내는지 보여 준다.

    다른 하나는 **자료는 옳은데 산출이 까다로운 경우** (:data:`PRACTICE_CASES`).
    임원 세법한도 동결, 연봉제 전환 누진 보전, 동결 DC전환자, DC전환 후
    퇴직, 명예퇴직, 사망 정액 가산 같은 것들이며 비고란에 무엇인지 적어 둔다.
    검증이 잡아 주지 않고 담당자가 규정을 읽어 반영해야 하는 것들이라, 시험
    자료에 있어야 연습이 된다.

    **[검증 오류가 있어도 산출 강행]** 을 켜야 끝까지 돈다.

난수는 씨앗을 고정하므로 같은 인자로 부르면 같은 파일이 나온다. 산출 결과를
비교할 때 명부가 매번 바뀌면 비교 자체가 되지 않기 때문이다.

성명·사번은 지어낸 것이고 실제 인물과 무관하다.
"""

from __future__ import annotations

import datetime as _dt
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

__all__ = [
    "CASES", "DEFAULT_CASE", "CaseSpec", "make_population",
    "write_case_roster", "write_case_rosters",
]

BASE_DATE: Final = _dt.date(2025, 12, 31)
"""기본 산출기준일. 생성 함수에 ``base_date`` 를 주면 그 날짜로 만든다."""

_SURNAMES: Final = (
    "김", "이", "박", "최", "정", "강", "조", "윤", "장", "임",
    "한", "오", "서", "신", "권", "황", "안", "송", "류", "전",
)
_GIVEN_FIRST: Final = (
    "민", "지", "서", "현", "태", "준", "예", "도", "하", "수",
    "재", "성", "은", "정", "영", "찬", "동", "우", "경", "선",
)
_GIVEN_LAST: Final = (
    "준", "우", "진", "호", "연", "아", "빈", "율", "현", "수",
    "희", "국", "철", "환", "일", "석", "규", "만", "화", "숙",
)

#: 임직원구분 원문. 명부마다 표기가 다르다는 사실 자체가 시험 대상이다.
#: 표준 양식에는 이 열이 없다 — 직군으로 갈린다. 직군과 어긋나는 사람만
#: 회사가 덧붙여 보낸 열처럼 명부에 남는다(:func:`_declared_type`).
_TYPE_STAFF: Final = "직원"
_TYPE_EXEC: Final = "임원"


@dataclass(slots=True)
class CaseSpec:
    """사례 하나의 성격."""

    key: str
    title: str
    """파일 이름에 쓸 이름."""
    summary: str
    """Input 시트 위에 적어 둘 한 줄 설명."""
    active: int = 290
    retired: int = 30
    groups: tuple[tuple[str, float], ...] = ()
    """``(명부 직군 원문, 비중)``. 비중은 합이 1 이 아니어도 된다."""
    wage_check: int = 0
    notes: tuple[str, ...] = ()
    """Input 시트에 남길 설명 줄."""
    flags: dict[str, Any] = field(default_factory=dict)
    """사례별 특수 처리 스위치."""


CASES: Final[tuple[CaseSpec, ...]] = (
    CaseSpec(
        key="표준",
        title="시험명부1_표준",
        summary="자료가 모두 갖춰진 표준 명부입니다. 검증 오류 없이 그대로 산출됩니다.",
        active=290, retired=28,
        groups=(("정규직", 0.82), ("계약직", 0.12), ("임원", 0.06)),
        notes=(
            "· 재직 290명 / 퇴직 28명. 제도는 DB 와 퇴직금제도만 씁니다.",
            "· 직군 표기가 정규직·계약직·임원 그대로라 직군 매핑을 손댈 일이 없습니다.",
        ),
    ),
    CaseSpec(
        key="복합제도",
        title="시험명부2_복합제도",
        summary="DC 전환·중간정산·임금피크·정년초과 재고용·전입이 섞인 명부입니다. "
                "자료는 옳으므로 강행 없이 산출됩니다.",
        active=290, retired=42,
        groups=(
            ("정규사원", 0.46), ("생산직", 0.20), ("촉탁사원", 0.13),
            ("계약직(기간제)", 0.09), ("파견", 0.04), ("별정직", 0.03),
            ("임원(별정)", 0.03), ("등기임원", 0.02),
        ),
        notes=(
            "· 직군 표기가 회사 인사규정대로라 [직군 매핑] 탭에서 묶어 주어야 합니다.",
            "· DC 가입자는 확정급여채무에서 빠지고, 중간정산자는 정산일부터 근속을 다시 셉니다.",
            "· 임금피크 대상자가 있어 정년연령이 그 연령으로 당겨집니다.",
            "· 정년을 넘겨 재고용된 사람이 있어 '정년초과 가산연령' 이 실제로 쓰입니다.",
            "· 전입자·휴직차감·개별 지급배수가 섞여 있습니다.",
        ),
        flags={
            "dc_share": 0.14, "settlement_share": 0.10, "wage_peak_share": 0.35,
            "over_nra_share": 0.05, "transfer_in_share": 0.05,
            "multiple_share": 0.05,
            "leave_share": 0.06, "longterm_share": 0.55,
        },
    ),
    CaseSpec(
        key="자료불량",
        title="시험명부3_자료불량",
        summary="실무에서 받는 명부 그대로입니다. 손봐야 하는 자료와 규정을 읽어야 "
                "풀리는 특이사항이 함께 들어 있습니다. [검증 오류가 있어도 산출 강행] 을 켜세요.",
        active=290, retired=35,
        groups=(("정규직", 0.70), ("계약직", 0.14), ("임원", 0.06),
                ("촉탁", 0.06), ("", 0.04)),
        wage_check=1_000_000,
        notes=(
            "· 두 가지가 섞여 있습니다. 하나는 **틀린 자료** — 검증 리포트가 잡아냅니다.",
            "  날짜 서식 혼재 · 제도구분 누락 · 임금 0 · 사번 중복 · 생년월일과 입사일 역전 ·",
            "  Input 에 없는 직군 · 퇴사일이 입사일보다 이른 퇴직자 · 지급액 0 등입니다.",
            "· 다른 하나는 **자료는 옳은데 산출이 까다로운 경우** 입니다. 비고란을 보세요.",
            "  임원 세법한도 동결(같은 사번 두 줄) · 연봉제 전환 누진 보전 ·",
            "  동결 DC전환자 · DC전환 후 퇴직(재직·퇴직 사번 중복) · 퇴직예정자 ·",
            "  명예퇴직 예정자 · 정년 시 기본급 추가지급 · 휴직차감 · 사망 정액 가산 ·",
            "  명예퇴직 위로금 · 임금 단위 혼재 · 장기급여 대상 표기 혼재.",
            "· 평균임금 체크금액을 1,000,000원으로 두어 그 미만인 사람도 걸립니다.",
        ),
        flags={
            "dirty": True, "practice": True, "dc_share": 0.06,
            "settlement_share": 0.05, "longterm_share": 0.35,
            "leave_share": 0.04, "multiple_share": 0.03,
        },
    ),
)


DEFAULT_CASE: Final = CaseSpec(
    key="기본",
    title="명부_기본",
    summary="처음 한 번 돌려 보는 기본 명부입니다. 실제 결산에서 마주치는 "
            "형태를 담되 자료는 옳으므로 강행 없이 산출됩니다.",
    active=260, retired=24,
    # 기초율_기본값.xlsx 의 지급규정과 같은 이름이어야 짝이 맞는다.
    groups=(("정규직", 0.82), ("계약직", 0.12), ("임원", 0.06)),
    notes=(
        "· 재직 260명 / 퇴직 24명. 난수로 만든 가상 명부이며 실존 인물이 아닙니다.",
        "· DC 가입자가 섞여 있어 확정급여채무 산출대상에서 빠집니다.",
        "· 임원인데 직군 칸이 '정규직' 인 사람이 있습니다 — 직군만으로는 갈라낼 수 "
        "없어 임직원구분까지 봅니다.",
        "· 중간정산자는 정산일부터 근속을 다시 셉니다.",
    ),
    flags={
        "dc_share": 0.10, "settlement_share": 0.07, "longterm_share": 0.45,
        "exec_in_regular_share": 0.04,
    },
)
"""배포본에 같이 넣는 기본 명부.

**난수로 만든다.** 실제 명부를 넣으면 프로그램을 건네는 순간 그 자료도 같이
건네진다. 성명이 없어도 생년월일·입사일·30일 평균임금이 사람마다 한 줄씩이면
같은 회사 안에서는 특정될 수 있다.

처음 한 번 돌려 보는 것이 목적이므로 실제 값일 필요가 전혀 없고, 형태만
같으면 된다.
"""


# ── 사람 만들기 ──────────────────────────────────────────────────

def _name(rng: random.Random) -> str:
    return (
        rng.choice(_SURNAMES) + rng.choice(_GIVEN_FIRST) + rng.choice(_GIVEN_LAST)
    )


def _pick_group(rng: random.Random, spec: CaseSpec) -> str:
    names = [name for name, _ in spec.groups]
    weights = [weight for _, weight in spec.groups]
    return rng.choices(names, weights=weights)[0]


def _is_executive(group: str) -> bool:
    return any(token in group for token in ("임원", "등기"))


def _birth_for_age(rng: random.Random, age: int, base: _dt.date) -> _dt.date:
    """만 나이가 ``age`` 가 되도록 생년월일을 흩뿌린다."""
    year = base.year - age
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    born = _dt.date(year, month, day)
    # 생일이 아직 안 지났으면 만 나이가 하나 적어지므로 한 해 당긴다.
    if (born.month, born.day) > (base.month, base.day):
        born = born.replace(year=year - 1)
    return born


def _resident(birth: _dt.date, male: bool) -> str:
    """주민등록번호 **앞 7자리**. 뒷 여섯 자리는 만들지 않는다.

    양식이 앞 7자리만 달라고 적어 두었으니 시험 자료도 그만큼만 갖춘다.
    모양을 다 갖춰 두면 진짜 주민번호와 구별되지 않아, 이 파일이 어디로
    흘러가든 곤란해진다.
    """
    marker = (1 if male else 2) if birth.year < 2000 else (3 if male else 4)
    return f"{birth:%y%m%d}-{marker}"


def _declared_type(row: dict[str, Any]) -> dict[str, Any]:
    """직군으로 알 수 있는 임직원구분은 명부에서 지운다.

    표준 양식에 임직원구분 열이 없기 때문이다 — 직군과 겹친다. 다만 직군은
    '정규직' 인데 실제로는 임원인 사람은 직군만으로 갈라낼 수 없어, 그런
    사람의 것만 남긴다. 회사가 자기네 열을 하나 덧붙여 보낸 모양이 되고,
    머리글로 여분 열을 잡아내는 경로까지 시험 자료가 짚고 간다.
    """
    expected = _TYPE_EXEC if _is_executive(row.get("job_group", "")) else _TYPE_STAFF
    if row.get("employee_type") == expected:
        return {k: v for k, v in row.items() if k != "employee_type"}
    return row


def _hire_date(birth: _dt.date, service_years: float, base: _dt.date) -> _dt.date:
    days = int(service_years * 365.25)
    hired = base - _dt.timedelta(days=days)
    earliest = birth + _dt.timedelta(days=int(19 * 365.25))
    return max(hired, earliest)


def _wage(rng: random.Random, age: int, service: float, executive: bool) -> int:
    """30일 평균임금. 근속·연령에 따라 오르고 사람마다 흩어진다."""
    base = 2_900_000 + service * 118_000 + max(0, age - 30) * 26_000
    spread = rng.gauss(1.0, 0.16)
    if executive:
        base = base * 2.3 + 3_000_000
        spread = rng.gauss(1.0, 0.22)
    return max(2_100_000, int(base * max(0.6, spread) / 1_000) * 1_000)


@dataclass(slots=True)
class _Person:
    """만들어 낸 재직자 한 사람. 열 이름은 명부 서식 키와 같다."""

    values: dict[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self.values.get(key)


def _make_active(
    rng: random.Random, spec: CaseSpec, index: int, base: _dt.date
) -> dict[str, Any]:
    group = _pick_group(rng, spec)
    executive = _is_executive(group)
    flags = spec.flags

    if executive:
        age = rng.randint(48, 62)
        service = min(age - 25, rng.gauss(19, 6))
    else:
        age = int(min(60, max(23, rng.gauss(41, 9.5))))
        service = min(age - 20, max(0.4, rng.gauss(11, 7.5)))

    # 정년을 넘겨 재고용된 사람 — '정년초과 가산연령' 이 실제로 쓰인다.
    if rng.random() < flags.get("over_nra_share", 0):
        age = rng.randint(61, 65)
        service = min(age - 25, max(1.0, rng.gauss(24, 6)))

    birth = _birth_for_age(rng, age, base)
    hire = _hire_date(birth, max(0.3, service), base)
    service = (base - hire).days / 365.25
    wage = _wage(rng, age, service, executive)

    plan = "DB"
    if rng.random() < flags.get("dc_share", 0):
        plan = "DC"
    elif rng.random() < 0.22:
        plan = "퇴직금제도"

    row: dict[str, Any] = {
        "employee_id": f"A{index:04d}",
        "employee_type": _TYPE_EXEC if executive else _TYPE_STAFF,
        "job_group": group,
        "name": _name(rng),
        "resident_number": _resident(
            birth, rng.random() < (0.78 if executive else 0.62)),
        "birth_date": birth.isoformat(),
        "hire_date": hire.isoformat(),
        "monthly_wage": wage,
        "daily_base_pay": int(wage / 30 / 100) * 100,
        "plan": plan,
        "longterm_target": "Y" if rng.random() < flags.get("longterm_share", 0.4) else "N",
        "cost_code": rng.choice(("제조원가", "판관비", "연구개발비")),
    }

    # ── 사례별 특수 항목 ────────────────────────────────────────
    if rng.random() < flags.get("settlement_share", 0):
        # 중간정산 — 정산일 이후로 근속을 다시 센다.
        span = max(1, int((base - hire).days * 0.6))
        settled = hire + _dt.timedelta(days=rng.randint(1, span))
        row["settlement_date"] = settled.isoformat()
        row["settlement_amount"] = int(wage * (settled - hire).days / 365.25 / 1_000) * 1_000

    # 임금피크는 진입 직전 연령대에 붙인다. 이미 56세를 넘긴 사람에게 주면
    # 정년이 현재 연령보다 낮아져 값이 무시된다.
    if not executive and 50 <= age <= 55 and rng.random() < flags.get("wage_peak_share", 0):
        row["wage_peak_age"] = rng.choice((56, 57, 58))

    if rng.random() < flags.get("transfer_in_share", 0):
        moved = base - _dt.timedelta(days=rng.randint(200, 2_600))
        if moved > hire:
            row["transfer_in_date"] = moved.isoformat()
            row["transfer_in_amount"] = int(wage * rng.uniform(1.5, 6.0) / 1_000) * 1_000

    if rng.random() < flags.get("leave_share", 0):
        row["leave_days"] = rng.choice((30, 90, 180, 365))

    if rng.random() < flags.get("multiple_share", 0):
        row["payout_multiple"] = rng.choice((1.5, 2.0, 2.5))

    # 임원인데 직군 칸에는 '정규직' 이라고 적혀 오는 명부가 있다. 직군만으로는
    # 임원을 갈라낼 수 없어 :meth:`CalculationConfig.find_job_group` 이 임직원
    # 구분까지 보는 것인데, 시험 자료에 그 형태가 없으면 그 길이 한 번도 밟히지
    # 않는다.
    #
    # 비중을 **먼저** 본다. 뒤에 두면 이 사례에 없는 항목인데도 ``rng.random()``
    # 이 불려 난수 흐름이 한 칸씩 밀리고, 다른 사례의 명부가 통째로 달라진다.
    exec_in_regular = flags.get("exec_in_regular_share", 0)
    if exec_in_regular and not executive and age >= 50 and rng.random() < exec_in_regular:
        row["employee_type"] = _TYPE_EXEC

    return row


def _ensure_special_cases(
    rng: random.Random, spec: CaseSpec, rows: list[dict[str, Any]], base: _dt.date
) -> None:
    """사례가 내세운 특이사항이 하나도 안 걸렸으면 억지로라도 넣는다.

    확률로만 뿌리면 씨앗이나 기준일에 따라 '임금피크 대상 0명' 인 명부가 나온다.
    그런데 이 사례는 안내문에 임금피크가 있다고 적어 두므로, 없으면 안내문이
    거짓말이 된다. 시험 자료는 무엇이 들어 있는지가 곧 쓸모다.
    """
    if not spec.flags:
        return

    def eligible(key: str, pick):
        return [row for row in rows if not row.get(key) and pick(row)]

    def age_of(row) -> int:
        return base.year - int(str(row["birth_date"])[:4])

    wanted = [
        ("wage_peak_age", lambda r: r["employee_type"] == _TYPE_STAFF
                                    and 50 <= age_of(r) <= 55,
         lambda r: rng.choice((56, 57, 58)), "wage_peak_share"),
        ("transfer_in_date", lambda r: True,
         lambda r: (base - _dt.timedelta(days=rng.randint(200, 2_600))).isoformat(),
         "transfer_in_share"),
        ("payout_multiple", lambda r: True,
         lambda r: rng.choice((1.5, 2.0, 2.5)), "multiple_share"),
        ("leave_days", lambda r: True,
         lambda r: rng.choice((30, 180, 365)), "leave_share"),
    ]
    for key, pick, make, share in wanted:
        if not spec.flags.get(share):
            continue
        if any(row.get(key) for row in rows):
            continue
        pool = eligible(key, pick)
        for row in rng.sample(pool, min(3, len(pool))):
            row[key] = make(row)


def _make_retired(
    rng: random.Random, spec: CaseSpec, index: int, base: _dt.date
) -> dict[str, Any]:
    group = _pick_group(rng, spec)
    executive = _is_executive(group)
    flags = spec.flags

    age = rng.randint(50, 63) if executive else int(min(64, max(24, rng.gauss(42, 11))))
    birth = _birth_for_age(rng, age, base)

    # 퇴직자명부는 당기에 나간 사람을 담는다. 퇴사일을 먼저 잡고 거기서
    # 근속을 거슬러 입사일을 만든다 — 반대로 하면 퇴사일이 산출기준일을
    # 넘어가는 사람이 생긴다.
    exit_date = base - _dt.timedelta(days=rng.randint(0, 364))
    service = min(age - 20, max(0.3, rng.gauss(9, 7)))
    hire = exit_date - _dt.timedelta(days=int(service * 365.25))
    hire = max(hire, birth + _dt.timedelta(days=int(19 * 365.25)))
    if hire >= exit_date:
        hire = exit_date - _dt.timedelta(days=rng.randint(40, 300))
    served = max(0.0, (exit_date - hire).days / 365.25)
    wage = _wage(rng, age, served, executive)

    # 사유: 대부분 중도퇴직. 복합제도 사례에서는 정년·사망·전출·처분도 섞는다.
    if flags.get("dc_share"):
        reason = rng.choices(
            ("1", "4", "3", "5", "2", "6"),
            weights=(58, 14, 12, 8, 4, 4),
        )[0]
    else:
        reason = rng.choices(("1", "4", "2"), weights=(84, 13, 3))[0]

    plan = "DB" if rng.random() < 0.7 else "퇴직금제도"
    if reason == "3":
        plan = "DC"

    total = int(wage * served / 1_000) * 1_000
    row: dict[str, Any] = {
        "employee_id": f"T{index:04d}",
        "employee_type": _TYPE_EXEC if executive else _TYPE_STAFF,
        "job_group": group,
        "name": _name(rng),
        "resident_number": _resident(birth, rng.random() < 0.62),
        "birth_date": birth.isoformat(),
        "hire_date": hire.isoformat(),
        "exit_date": exit_date.isoformat(),
        "reason": reason,
        "plan": plan,
        "total_payment": max(0, total),
        "longterm_target": "Y" if rng.random() < flags.get("longterm_share", 0.4) else "N",
        "cost_code": rng.choice(("제조원가", "판관비", "연구개발비")),
    }
    if total > 0 and rng.random() < 0.75:
        row["fund_payment"] = int(total * rng.uniform(0.5, 1.0) / 1_000) * 1_000
        row["fund_payment_date"] = (
            exit_date + _dt.timedelta(days=rng.randint(1, 30))
        ).isoformat()
    if reason == "5":   # 계열사 전출
        row["transfer_out_payment"] = total
    if reason == "4" and rng.random() < 0.4:
        row["other_payment"] = int(wage * rng.uniform(1, 6) / 1_000) * 1_000
    if row["longterm_target"] == "Y" and rng.random() < 0.3:
        row["longterm_payment"] = int(wage * rng.uniform(0.2, 1.2) / 1_000) * 1_000
    return row


# ── 자료를 일부러 망가뜨리기 ─────────────────────────────────────
# 검증 리포트가 무엇을 잡아내는지 보여 주려면 잡을 것이 있어야 한다. 실제
# 명부에서 본 적 있는 형태만 넣는다 — 있을 법하지 않은 오류를 넣으면 검증이
# 과하다는 인상만 준다.

def _dirty_date(rng: random.Random, value: str) -> Any:
    """날짜 서식을 뒤섞는다. 파서가 견디는지 보는 것이다."""
    year, month, day = value.split("-")
    style = rng.randint(0, 4)
    if style == 0:
        return f"{year}.{month}.{day}"
    if style == 1:
        return f"{year}{month}{day}"
    if style == 2:
        return f"{year}/{month}/{day}"
    if style == 3:
        return _dt.date(int(year), int(month), int(day))   # 진짜 날짜 셀
    return f"{year}년 {int(month)}월 {int(day)}일"


#: 실무 스터디에서 실제로 마주친 산출 특이사항. 확률로 흩뿌리지 않고 **반드시
#: 한 건씩** 심는다 — 안내문이 있다고 적어 둔 것은 명부에 있어야 한다.
PRACTICE_CASES: Final[tuple[tuple[str, str], ...]] = (
    ("임원 세법한도 동결",
     "같은 사번이 두 줄. 2019년 이전은 3배수·이후는 2배수로 지급구간이 갈린다."),
    ("연봉제 전환 누진 보전",
     "호봉제 시절 근속분의 누진 배수를 보전한다. 근속은 이어지고 배수만 갈린다."),
    ("동결 DC전환자",
     "DC 로 전환했지만 전환 전 과거분은 퇴직금으로 남아 있다."),
    ("DC전환 후 퇴직 — 사번 중복",
     "같은 사번이 재직자명부와 퇴직자명부에 함께 있다."),
    ("퇴직예정자",
     "기준일 뒤 퇴사가 확정돼 비고에 적혀 왔다. 기준일 현재는 재직자다."),
    ("명예퇴직 예정자",
     "명예퇴직 산정용 임금이 따로 적혀 있다."),
    ("정년퇴직 시 기본급 추가지급",
     "정년으로 나가는 사람에게 기본급을 얹어 준다(전장직원 예우)."),
    ("휴직 차감",
     "휴직한 날수만큼 근속 기산일을 뒤로 민다."),
    ("사망 추가지급",
     "재직 중 사망하면 정액 가산금을 얹는다."),
    ("명예퇴직 위로금",
     "희망퇴직자에게 퇴직금과 별도로 위로금을 준다."),
    ("임금 단위 혼재",
     "월평균임금을 천원 단위로 적어 보낸 줄이 섞여 있다."),
    ("장기급여 대상 표기 혼재",
     "Y/N 대신 ○·×·1·0 으로 적어 왔다."),
)


def _add_practice_cases(
    rng: random.Random, actives: list[dict[str, Any]], retirees: list[dict[str, Any]],
    base: _dt.date,
) -> dict[str, str]:
    """실무에서 마주치는 산출 특이사항을 한 건씩 심는다.

    ``_spoil_active`` 가 '자료가 틀린' 경우를 만든다면 여기는 '자료는 옳은데
    산출이 까다로운' 경우를 만든다. 둘은 성격이 달라 따로 둔다 — 틀린 자료는
    고쳐 달라고 돌려보내지만, 이쪽은 규정을 읽고 산출에 반영해야 하는 것들이다.

    :returns: ``{특이사항 이름: 사번}``. 안내문이 "어느 사번을 보라" 고 짚어
        주려면 심은 자리를 기억해야 한다 — 비고 글자를 되짚어 찾으면 비슷한
        문구끼리 엉킨다.
    """
    planted: dict[str, str] = {}

    def take(pool: list[dict[str, Any]], **want: Any) -> dict[str, Any]:
        """조건에 맞는 아직 안 쓴 줄 하나. 없으면 아무거나."""
        fit = [r for r in pool if not r.get("note")
               and all(r.get(k) == v for k, v in want.items())]
        return rng.choice(fit or pool)

    def date_of(row: dict[str, Any], key: str) -> _dt.date:
        return _dt.date.fromisoformat(str(row[key])[:10])

    # ── 임원 세법한도 동결 — 같은 사번을 두 줄로 나눈다 ──────────
    exec_row = take(actives, employee_type=_TYPE_EXEC)
    hire = date_of(exec_row, "hire_date")
    split = _dt.date(2019, 12, 31)
    if hire < split < base:
        wage = float(exec_row["monthly_wage"])
        exec_row["period_end"] = split.isoformat()
        exec_row["payout_multiple"] = 3.0
        exec_row["note"] = "19.12.31 이전 기간만 3배수 (세법한도 동결)"
        later = dict(exec_row)
        later["period_start"] = (split + _dt.timedelta(days=1)).isoformat()
        later.pop("period_end", None)
        later["payout_multiple"] = 2.0
        later["monthly_wage"] = int(wage * 1.35 / 1_000) * 1_000
        later["note"] = "20.1.1 이후 기간만 2배수"
        actives.insert(actives.index(exec_row) + 1, later)
        planted["임원 세법한도 동결"] = exec_row["employee_id"]

    # ── 연봉제 전환 누진 보전 ────────────────────────────────────
    row = take(actives, employee_type=_TYPE_STAFF)
    served = (base - date_of(row, "hire_date")).days / 365.25
    if served > 8:
        row["progressive_service"] = round(served * 0.45, 1)
        row["progressive_rate"] = rng.choice((1.2, 1.3, 1.5))
        row["note"] = "연봉제 전환 이전 누진 보전 + 이후 법정제"
        planted["연봉제 전환 누진 보전"] = row["employee_id"]

    # ── 동결 DC전환자 (과거분은 퇴직금으로 남음) ───────────────
    frozen = take(actives, employee_type=_TYPE_STAFF)
    frozen["plan"] = "DC"
    moved = base - _dt.timedelta(days=rng.randint(400, 2_000))
    if moved > date_of(frozen, "hire_date"):
        frozen["settlement_date"] = moved.isoformat()
        frozen["settlement_amount"] = int(
            float(frozen["monthly_wage"]) * rng.uniform(2, 7) / 1_000) * 1_000
    frozen["note"] = "DC 전환(동결). 전환 전 과거분은 퇴직금 지급"
    planted["동결 DC전환자"] = frozen["employee_id"]

    # ── DC전환 후 퇴직 — 재직·퇴직 양쪽에 같은 사번 ──────────────
    if retirees:
        leaver = rng.choice(retirees)
        leaver["employee_id"] = frozen["employee_id"]
        leaver["name"] = frozen["name"]
        leaver["reason"] = "3"
        leaver["plan"] = "DC"
        leaver["note"] = "DC 전환 후 퇴직 — 재직자명부와 사번 중복"
        planted["DC전환 후 퇴직 — 사번 중복"] = leaver["employee_id"]

    # ── 퇴직예정자 · 명예퇴직 예정자 ─────────────────────────────
    leaving = take(actives)
    leaving["note"] = (
        f"{(base + _dt.timedelta(days=rng.randint(20, 90))).isoformat()} 퇴사 예정")
    planted["퇴직예정자"] = leaving["employee_id"]
    honorary = take(actives, employee_type=_TYPE_STAFF)
    honorary["honorary_wage"] = int(
        float(honorary["monthly_wage"]) * 1.15 / 1_000) * 1_000
    honorary["note"] = "명예퇴직 신청 — 명예퇴직 산정용 임금 별도"
    planted["명예퇴직 예정자"] = honorary["employee_id"]

    # ── 정년퇴직 시 기본급 추가지급 ──────────────────────────────
    extra = take(actives, employee_type=_TYPE_STAFF)
    extra["extra_pay_base_wage"] = int(
        float(extra["monthly_wage"]) * 0.8 / 1_000) * 1_000
    extra["note"] = "정년퇴직 시 기본급 추가지급 대상"
    planted["정년퇴직 시 기본급 추가지급"] = extra["employee_id"]

    # ── 휴직 차감 ────────────────────────────────────────────────
    added = take(actives)
    added["leave_days"] = rng.choice((180, 365, 540))
    added["note"] = "휴직 차감 대상"
    planted["휴직 차감"] = added["employee_id"]

    # ── 사망 추가지급 · 명예퇴직 위로금 ──────────────────────────
    if len(retirees) >= 2:
        dead, honor = rng.sample(retirees, 2)
        dead["reason"] = "2"
        dead["other_payment"] = 50_000_000
        dead["note"] = "재직 중 사망 — 정액 가산금 별도"
        honor["other_payment"] = int(
            float(honor.get("total_payment") or 0) * 0.4 / 1_000) * 1_000
        honor["note"] = "명예퇴직 위로금"
        planted["사망 추가지급"] = dead["employee_id"]
        planted["명예퇴직 위로금"] = honor["employee_id"]

    # ── 임금 단위 혼재 · 장기급여 표기 혼재 ──────────────────────
    scaled = take(actives)
    scaled["monthly_wage"] = int(float(scaled["monthly_wage"]) / 1_000)
    scaled["note"] = "임금을 천원 단위로 기재 (단위 확인 필요)"
    planted["임금 단위 혼재"] = scaled["employee_id"]
    mixed = rng.sample(actives, min(8, len(actives)))
    for row in mixed:
        row["longterm_target"] = rng.choice(("○", "×", "1", "0", "Y", "N"))
    planted["장기급여 대상 표기 혼재"] = mixed[0]["employee_id"]
    return planted


def _spoil_active(
    rng: random.Random, rows: list[dict[str, Any]], base: _dt.date
) -> None:
    total = len(rows)

    def sample(share: float) -> list[dict[str, Any]]:
        return rng.sample(rows, max(1, int(total * share)))

    for row in sample(0.35):                       # 날짜 서식 혼재
        row["birth_date"] = _dirty_date(rng, str(row["birth_date"]))
        if rng.random() < 0.5:
            row["hire_date"] = _dirty_date(rng, str(row["hire_date"]))

    for row in sample(0.05):                       # 제도구분 누락·오기
        row["plan"] = rng.choice(("", "", "확정급여", "1"))

    for row in sample(0.03):                       # 임금 0 또는 체크금액 미만
        row["monthly_wage"] = rng.choice((0, "", 780_000))

    for row in sample(0.02):                       # 생년월일·입사일 역전
        row["birth_date"], row["hire_date"] = row["hire_date"], row["birth_date"]

    for row in sample(0.03):                       # 주민번호 표기 혼재
        value = str(row.get("resident_number", ""))
        row["resident_number"] = rng.choice((
            value.replace("-", ""),      # 붙여 쓴 것 — 읽힌다
            value[:6],                   # 성별 자리가 없다 — 생년월일만 읽힌다
            "",                          # 아예 빈 칸
            value + "******",            # 뒷자리까지 보내 왔다 — 지우라고 알린다
        ))

    for row in sample(0.02):                       # 성명 누락
        row["name"] = ""

    for row in sample(0.02):                       # 추계액이 음수
        row["accrued_benefit"] = -rng.randint(1, 9) * 1_000_000

    # 사번 중복 — 인사시스템에서 두 번 뽑아 붙인 명부에서 흔하다.
    for row in rng.sample(rows, 3):
        row["employee_id"] = rows[0]["employee_id"]

    # 입사일이 산출기준일보다 늦은 사람(다음 해 입사 예정자가 섞여 옴)
    for row in rng.sample(rows, 2):
        row["hire_date"] = (base + _dt.timedelta(days=rng.randint(5, 60))).isoformat()


def _spoil_retired(rng: random.Random, rows: list[dict[str, Any]]) -> None:
    total = len(rows)

    def sample(share: float) -> list[dict[str, Any]]:
        return rng.sample(rows, max(1, int(total * share)))

    for row in sample(0.30):
        row["exit_date"] = _dirty_date(rng, str(row["exit_date"]))

    for row in sample(0.06):                       # 총지급액 0
        row["total_payment"] = 0
        row.pop("fund_payment", None)

    for row in sample(0.05):                       # 사외자산이 총지급액을 넘음
        row["fund_payment"] = int(row.get("total_payment", 0)) + 5_000_000

    for row in sample(0.04):                       # 퇴사일이 입사일보다 이름
        row["exit_date"] = (
            _dt.date.fromisoformat(str(row["hire_date"])[:10]) - _dt.timedelta(days=30)
        ).isoformat() if str(row["hire_date"])[:4].isdigit() else row["exit_date"]

    for row in sample(0.04):                       # 제도구분 누락
        row["plan"] = ""


# ── 파일 쓰기 ────────────────────────────────────────────────────

def _case_report(
    spec: CaseSpec, actives: list[dict[str, Any]], retirees: list[dict[str, Any]],
    base: _dt.date, planted: dict[str, str] | None = None,
) -> str:
    """이 명부에 무엇이 들어 있는지 적은 안내문.

    난수로 만든 명부는 열어 봐도 무엇을 시험하려는 자료인지 알 수 없다. 몇 명이
    DC 이고 어떤 오류를 몇 건 심었는지 세어 함께 내보낸다 — 산출 결과가 이상해
    보일 때 명부 탓인지 프로그램 탓인지 가리는 근거가 된다.
    """
    planted = planted or {}

    def count(rows, key) -> int:
        return sum(1 for row in rows if row.get(key) not in (None, "", 0))

    lines = [
        f"시험용 명부 — {spec.key}",
        "=" * 40,
        "",
        spec.summary,
        "",
        f"산출기준일   {base}",
        f"재직자       {len(actives):,}명",
        f"퇴직자       {len(retirees):,}명",
        f"평균임금 체크금액  {spec.wage_check:,}원",
        "",
        "[직군 구성]",
    ]
    groups: dict[str, int] = {}
    for row in actives:
        groups[str(row.get("job_group") or "(빈 값)")] = (
            groups.get(str(row.get("job_group") or "(빈 값)"), 0) + 1
        )
    for name, number in sorted(groups.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {name:<16} {number:>4,}명")

    plans: dict[str, int] = {}
    for row in actives:
        plans[str(row.get("plan") or "(빈 값)")] = (
            plans.get(str(row.get("plan") or "(빈 값)"), 0) + 1
        )
    lines += ["", "[제도구분]"]
    for name, number in sorted(plans.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {name:<16} {number:>4,}명")

    lines += ["", "[산출 특이사항]"]
    special = [
        ("중간정산자 (정산일부터 근속을 다시 셈)", count(actives, "settlement_date")),
        ("임금피크 대상 (정년연령이 그 연령으로 당겨짐)", count(actives, "wage_peak_age")),
        ("전입자 (전입일·전입액 있음)", count(actives, "transfer_in_date")),
        ("휴직차감일수 있음", count(actives, "leave_days")),
        ("개별 지급배수 지정", count(actives, "payout_multiple")),
        ("장기급여 산출대상 (Y)",
         sum(1 for row in actives if row.get("longterm_target") == "Y")),
        ("정년(60세) 초과 재고용 추정",
         sum(1 for row in actives
             if str(row.get("birth_date", ""))[:4].isdigit()
             and base.year - int(str(row["birth_date"])[:4]) > 60)),
    ]
    for label, number in special:
        if number:
            lines.append(f"  {label}: {number:,}명")
    if not any(number for _, number in special):
        lines.append("  없음 — 평범한 구성입니다.")

    reasons = {"1": "중도퇴직", "2": "사망퇴직", "3": "DC전환", "4": "정년퇴직",
               "5": "계열사 전출", "6": "사업처분"}
    tally: dict[str, int] = {}
    for row in retirees:
        tally[str(row.get("reason"))] = tally.get(str(row.get("reason")), 0) + 1
    lines += ["", "[퇴직 사유]"]
    for code, number in sorted(tally.items()):
        lines.append(f"  {reasons.get(code, code):<12} {number:>3,}명")

    if spec.flags.get("practice"):
        lines += [
            "",
            "[규정을 읽어야 풀리는 특이사항 — 자료는 옳습니다]",
            "  비고란에 무엇인지 적어 두었습니다. 해당 사번을 찾아보세요.",
        ]
        for title, detail in PRACTICE_CASES:
            emp = planted.get(title)
            lines.append(f"  · {title} (사번 {emp}) — {detail}" if emp
                         else f"  · {title} — {detail}")
        lines += [
            "  ※ '명예퇴직 산정용 임금' 과 '추가지급 기본급' 은 값이 있으면 늘 경고가",
            "    붙습니다. 산출에 어떻게 반영할지는 규정을 보고 정해야 하기 때문입니다.",
        ]

    if spec.flags.get("dirty"):
        lines += [
            "",
            "[일부러 심어 둔 자료 오류]",
            "  검증 리포트가 무엇을 잡아내는지 보기 위한 것입니다.",
            "  · 날짜 서식 혼재 — 1985.03.02 / 19850302 / 1985/03/02 / 1985년 3월 2일 / 날짜 셀",
            "  · 제도구분 누락 또는 해석 불가('확정급여', '1')",
            "  · 30일 평균임금 0 또는 체크금액(1,000,000원) 미만",
            "  · 생년월일과 입사일이 뒤바뀐 사람",
            "  · 사번 중복 (같은 사번 4건)",
            "  · 입사일이 산출기준일보다 늦은 사람",
            "  · 주민등록번호 표기 혼재 (하이픈 없음 / 앞 6자리만 / 빈 값 / 뒷자리까지)",
            "  · 성명 누락, 퇴직급여추계액 음수",
            "  · 규정에 없는 직군 (Input 직군 규칙에서 한 직군을 뺐습니다)",
            "  · 퇴직자: 총지급액 0, 사외자산 지급액이 총지급액 초과, 퇴사일이 입사일보다 이름",
            "",
            "  → 산출하려면 [검증 오류가 있어도 산출 강행] 을 켜야 합니다.",
        ]
    else:
        lines += ["", "[검증]", "  검증 오류가 없도록 만든 명부입니다. 그대로 산출됩니다."]

    lines += [
        "",
        "[기본정보·퇴직급여규정·사외적립자산 — 담당자가 채워 보낸 것으로 두었습니다]",
        "  이 명부를 올리면 아래 항목이 화면에 저절로 들어갑니다. 다시 적을 필요가 없습니다.",
        "  · [기본정보] 산출기준일·산출 시작일·상시근로자 수·회사채 신용등급·직군 규칙",
        "  · [퇴직급여규정] 지급규정 열 항목과 특이사항 — 산출가정 초안의 근거가 됩니다",
        "  · [사외적립자산] 퇴직급여추계액 변동내역 — 퇴직자명부의 지급액과 맞춰 두었습니다",
        "  · [사외적립자산] 사외적립자산 변동내역 — 검증줄(기초+유입−유출−기말)이 0 원입니다",
        "  · [사외적립자산] 세부내역 — 문단 142 공시용 분류별 공정가치",
        "  · [사외적립자산] 기중 장기근속 지급액",
        "",
        "[같이 만든 기초율]",
        f"  {spec.title}_기초율.xlsx — 이 명부의 직군 매핑이 들어 있습니다.",
        "  기초율의 '지급규정' 시트가 명부 [기본정보] 의 직군 규칙보다 우선합니다.",
        "  다른 기초율을 쓰면 직군이 맞지 않아 전원 '직군을 찾지 못함' 오류가 납니다.",
        "",
        "이 명부는 난수로 만든 가상 자료입니다. 성명·사번은 실제 인물과 무관합니다.",
    ]
    return "\n".join(lines) + "\n"


def _obligation_totals(
    retirees: list[dict[str, Any]],
) -> dict[str, float]:
    """퇴직자명부에서 5-1) 퇴직급여추계액 변동내역 금액을 뽑는다.

    회사가 손으로 채워 보내는 표지만, 시험 명부에서는 **명부와 맞아떨어져야**
    한다. 임의의 숫자를 넣으면 증감표 검산이 어긋나 그것이 시스템 오류인지
    시험 자료 탓인지 알 수 없다.
    """
    totals = {
        "benefits_paid": 0.0, "settlement_paid": 0.0, "dc_converted": 0.0,
        "other_paid": 0.0, "transfer_out": 0.0, "disposal": 0.0,
    }
    for row in retirees:
        total = float(row.get("total_payment") or 0)
        other = float(row.get("other_payment") or 0)
        reason = str(row.get("reason") or "1")
        if reason == "3":
            totals["dc_converted"] += total
        elif reason == "5":
            totals["transfer_out"] += float(row.get("transfer_out_payment") or total)
        elif reason == "6":
            totals["disposal"] += total
        else:
            # 위로금은 따로 센다 — 지급액에 섞으면 증감표에서 갈라낼 수 없다.
            totals["benefits_paid"] += max(0.0, total - other)
        totals["other_paid"] += other
    return totals


def _asset_numbers(
    spec: CaseSpec, actives: list[dict[str, Any]],
    retirees: list[dict[str, Any]], rng: random.Random,
) -> dict[str, Any]:
    """[사외적립자산] 시트에 넣을 금액 일체.

    회사가 신탁 명세서를 보고 채워 보내는 표지만, 시험 명부에서는 **명부와
    맞아떨어져야** 한다. 임의의 숫자를 넣으면 증감표 검산이 어긋나 그것이
    시스템 오류인지 시험 자료 탓인지 알 수 없다.
    """
    paid = _obligation_totals(retirees)
    settlement = sum(float(row.get("settlement_amount") or 0) for row in actives)
    transfer_in = sum(float(row.get("transfer_in_amount") or 0) for row in actives)

    # 검산줄이 맞아떨어지도록 **기말을 역산** 한다. 기초와 유출입을 임의로 넣고
    # 기말도 임의로 넣으면 표가 스스로 안 맞아, 산출이 그 차이를 경고로 뱉는다 —
    # 시험 자료가 시스템을 거짓으로 고발하는 셈이다.
    fund_paid = sum(float(row.get("fund_payment") or 0) for row in retirees)
    accrued = sum(float(row.get("accrued_benefit") or 0) for row in actives)
    opening = round(max(accrued * rng.uniform(0.55, 0.85), fund_paid * 3), -3)
    contributions = round(opening * rng.uniform(0.06, 0.13), -3)
    actual_return = round(opening * rng.uniform(0.02, 0.04), -3)
    management_fee = round(opening * 0.0009, -3)
    custody_fee = round(opening * 0.0013, -3)
    national_pension = (
        round(opening * 0.01, -3) if spec.flags.get("national_pension") else 0.0
    )
    closing = (
        opening + contributions + actual_return
        - fund_paid - management_fee - custody_fee
    )
    longterm_paid = sum(float(row.get("longterm_payment") or 0) for row in retirees)

    cash = round(closing * 0.8, -3)
    return {
        "obligation": {
            "계열사 전입": round(transfer_in),
            "합병으로 받은 금액": 0,
            "퇴직금 지급액": round(paid["benefits_paid"]),
            "중간정산금": round(settlement),
            "DC전환 지급액": round(paid["dc_converted"]),
            "퇴직위로금 (명예퇴직금 등)": round(paid["other_paid"]),
            "계열사 전출": round(paid["transfer_out"]),
            "사업처분·분할": round(paid["disposal"]),
        },
        "opening": (round(opening - national_pension), round(national_pension)),
        "asset": {
            "부담금 납입액": (round(contributions), 0),
            "이자수익": (round(actual_return), 0),
            "계열사 전입": (0, 0),
            "합병으로 받은 금액": (0, 0),
            "퇴직금 지급액": (round(fund_paid), 0),
            "중간정산금": (0, 0),
            "DC전환 지급액": (0, 0),
            "계열사 전출": (0, 0),
            "사업처분·분할": (0, 0),
            "운용관리수수료": (round(management_fee), 0),
            "자산관리수수료": (round(custody_fee), 0),
        },
        "closing": (round(closing - national_pension), round(national_pension)),
        "breakdown": [
            ("현금 및 현금등가물", cash, "있음"),
            ("정기예금·원리금보장 GIC", round(closing) - cash, "없음"),
        ],
        "extras": {
            "자산인식상한 (문단 64)": "",
            "기준일 현재 미지급 퇴직급여": 0,
            "기중 장기근속 지급액": round(longterm_paid),
            "기중 장기근속 받은금액": 0,
        },
    }


def _rule_rows(spec: CaseSpec) -> list[tuple[Any, ...]]:
    """[기본정보] 의 직군 규칙 표.

    '자료불량' 사례는 규정에서 한 직군을 일부러 뺀다. 명부에는 있는데 규정에는
    없는 직군이 실제로 흔하고, 그때 검증이 무엇을 말해 주는지가 이 사례의
    존재 이유다.
    """
    from .jobgroup import suggest_group

    names = [name for name, _ in spec.groups if name]
    if spec.flags.get("dirty"):
        names = names[:-1]
    rows: list[tuple[Any, ...]] = []
    for name in names:
        executive = _is_executive(name)
        mapped = suggest_group(name, _TYPE_EXEC if executive else _TYPE_STAFF)
        nra = 65 if executive else 60
        rows.append((name, mapped, nra, nra, 2))
    return rows


def _special_rows(spec: CaseSpec) -> dict[str, str]:
    """[퇴직급여규정] 의 특이사항 칸. 사례가 무엇을 담고 있는지 적어 둔다."""
    special = {"그 밖에": " ".join(note.lstrip("· ") for note in spec.notes)}
    if spec.flags.get("dc_share"):
        special["DC 전환"] = "일부 가입자가 DC 로 전환했습니다. 전환자는 전환일에 정산했습니다."
    if spec.flags.get("settlement_share"):
        special["중간정산"] = "중간정산자가 있습니다. 근속을 정산일부터 다시 셉니다."
    if spec.flags.get("wage_peak_share"):
        special["임금피크"] = "임금피크 대상자가 있습니다. 정년이 그 연령으로 당겨집니다."
    if spec.flags.get("longterm_share"):
        special["장기급여"] = "근속 10·20·30년에 근속포상이 있습니다."
    if spec.flags.get("practice"):
        special["제도 변경"] = "연봉제 전환 시점의 누진 지급률을 보전하는 사람이 있습니다."
        special["명예퇴직"] = "명예퇴직 예정자가 있습니다. 산정용 임금이 따로 적혀 있습니다."
    return special


def _laid_out(
    records: list[dict[str, Any]], columns: list, aliases: dict[str, tuple[str, ...]]
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    """필드 이름을 이 양식의 열 이름으로 바꾼다.

    양식에 없는 항목(누진 보전·지급구간 등)은 표준 표기 그대로 두어 오른쪽에
    덧붙게 한다. 회사가 자기네 열을 더해 보내는 모양 그대로여야, 머리글로 열을
    찾아내는 경로까지 시험 자료가 짚고 간다.
    """
    from . import rostertemplate as tpl

    known = tpl.label_for(columns, aliases)
    extras: list[str] = []
    rows: list[dict[str, Any]] = []
    for offset, record in enumerate(records, start=1):
        line: dict[str, Any] = {"순번": offset}
        for key, value in record.items():
            label = known.get(key)
            if label is None:
                label = aliases.get(key, (key,))[0]
                if label not in extras:
                    extras.append(label)
            line[label] = value
        rows.append(line)
    return rows, tuple(extras)


def make_population(
    spec: CaseSpec, *, seed: int = 20251231, base_date: _dt.date | None = None,
    rng: random.Random | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """이 사례의 사람들. 특이사항도 자료 오류도 아직 없는 상태다.

    여러 명부가 **같은 사람** 을 놓고 한 가지씩만 달라야 할 때 쓴다. 명부마다
    다시 뽑으면 사람이 바뀌어, 채무 차이가 특이사항 때문인지 사람이 달라서인지
    가릴 수 없다.

    :param rng: 이어서 쓸 난수기. 주지 않으면 씨앗으로 새로 만든다. 뒤에
        특이사항을 더 심을 것이라면 **같은 난수기를 넘겨받아 이어 써야** 한다 —
        새로 만들면 난수 흐름이 처음으로 되감겨 명부가 통째로 달라진다.
    """
    base = base_date or BASE_DATE
    rng = rng or random.Random(f"{seed}:{spec.key}:{base}")
    actives = [_make_active(rng, spec, i + 1, base) for i in range(spec.active)]
    retirees = [_make_retired(rng, spec, i + 1, base) for i in range(spec.retired)]
    _ensure_special_cases(rng, spec, actives, base)
    return actives, retirees


def write_case_roster(
    spec: CaseSpec, path: str | Path, *, seed: int = 20251231,
    report_path: str | Path | None = None,
    base_date: _dt.date | None = None,
    population: tuple[list[dict[str, Any]], list[dict[str, Any]]] | None = None,
) -> Path:
    """사례 하나를 명부 통합문서로 쓴다.

    서식은 회사에 보내는 :mod:`pension.rostertemplate` 의 표준 양식 그대로다 —
    빈 양식과 시험 명부가 갈라지면, 정작 받아 본 파일에서 처음 어긋난다.

    :param report_path: 주면 그 자리에 특이사항 안내문(.txt)도 쓴다.
    :param base_date: 명부의 산출기준일. 생략하면 :data:`BASE_DATE`.
        연령·근속·퇴사일이 모두 이 날짜를 기준으로 만들어진다.
    :param population: 이미 만들어 둔 ``(재직자, 퇴직자)``. 주면 그대로 쓴다 —
        같은 사람을 놓고 한 가지만 바꾼 명부를 여러 벌 낼 때 쓴다.
    """
    from . import rostertemplate as tpl
    from .layout import ACTIVE_HEADER_ALIASES, RETIRED_HEADER_ALIASES

    base = base_date or BASE_DATE
    rng = random.Random(f"{seed}:{spec.key}:{base}")
    planted: dict[str, str] = {}
    if population is not None:
        actives, retirees = population
    else:
        actives, retirees = make_population(
            spec, seed=seed, base_date=base, rng=rng)
        # 특이사항을 먼저 심고 그 위에 자료 오류를 뿌린다. 순서를 바꾸면 오류가
        # 특이사항 줄을 덮어써 무엇을 보려던 자료인지 알 수 없게 된다.
        if spec.flags.get("practice"):
            planted = _add_practice_cases(rng, actives, retirees, base)
        if spec.flags.get("dirty"):
            _spoil_active(rng, actives, base)
            _spoil_retired(rng, retirees)

    active_rows, active_extras = _laid_out(
        [_declared_type(row) for row in actives], tpl.ACTIVE, ACTIVE_HEADER_ALIASES
    )
    retired_rows, retired_extras = _laid_out(
        [_declared_type(row) for row in retirees], tpl.RETIRED, RETIRED_HEADER_ALIASES
    )
    period_start = base.replace(year=base.year - 1) + _dt.timedelta(days=1)

    path = Path(path)
    workbook = tpl.build_workbook(
        note=f"시험용 명부 — {spec.key}. {spec.summary}",
        basics={
            "단체명": "○○주식회사",
            "산출기준일": base.isoformat(),
            "산출 시작일": period_start.isoformat(),
            "상시근로자 수": spec.active,
            "회사채 신용등급": spec.flags.get("credit_grade", "AA0"),
            "평균임금 하한 점검액": spec.wage_check,
        },
        groups=_rule_rows(spec),
        rules_filled=True,
        specials=_special_rows(spec),
        numbers=_asset_numbers(spec, actives, retirees, rng),
        actives=active_rows,
        retirees=retired_rows,
        active_extras=active_extras,
        retired_extras=retired_extras,
    )
    workbook.save(path)
    # 수식 칸에 값을 심는다. 없으면 엑셀 '제한된 보기' 에서 검증줄과 합계가
    # 빈칸으로 보여, 표가 깨진 것처럼 읽힌다.
    tpl._embed_values(path)

    if report_path is not None:
        Path(report_path).write_text(
            _case_report(spec, actives, retirees, base, planted), encoding="utf-8"
        )
    return path


def _case_mapping(spec: CaseSpec) -> list[list[str]]:
    """``(명부직군, 임직원구분, 변환직군)`` — 이 사례의 직군 매핑.

    '자료불량' 사례는 규정에서 한 직군을 일부러 뺀다. 명부에는 있는데 규정에는
    없는 직군이 실제로 흔하고, 그때 검증이 무엇을 말해 주는지가 이 사례의
    존재 이유다.
    """
    from .jobgroup import suggest_group

    names = [name for name, _ in spec.groups if name]
    if spec.flags.get("dirty"):
        names = names[:-1]
    return [
        [name, "", suggest_group(
            name, _TYPE_EXEC if _is_executive(name) else _TYPE_STAFF
        )]
        for name in names
    ]


def write_case_assumptions(spec: CaseSpec, path: str | Path) -> Path:
    """사례에 맞는 기초율 워크북.

    기초율의 ``지급규정`` 시트는 명부 ``Input`` 의 직군 규칙을 **대체한다.**
    그래서 명부만 주고 아무 기초율이나 붙이면 직군이 하나도 맞지 않는다.
    사례마다 짝이 되는 기초율을 같이 만들어 두는 이유다.
    """
    from . import assumption_form as form
    from .samples import write_standard_assumptions
    from .standard_rates import size_for

    mapping = _case_mapping(spec)
    groups = list(dict.fromkeys(row[2] for row in mapping))

    path = Path(path)
    # 표준률로 한 벌 채운 뒤, 이 사례의 직군 매핑을 얹는다. 승급률·퇴직률은
    # 사업장 규모로 갈리므로 이 사례의 재직 인원으로 열을 고른다.
    write_standard_assumptions(
        path, job_groups=tuple(groups), size=size_for(spec.active)
    )
    state = form.read_state(path)
    state["mapping"] = mapping
    for group in groups:
        rule = state["payout"].setdefault(group, form.default_payout())
        if group == "임원":
            rule["nra"] = rule["executive_nra"] = "65"
            # 임원은 정년까지 근무한다고 보아 중도퇴직률을 빼는 회사가 많다.
            rule["withdrawal"] = "미반영"

    # 근속 포상(장기근속휴가) — 표가 비어 있으면 장기급여채무가 0 으로 나와
    # 기타장기종업원급여 산출을 눌러 봐도 볼 것이 없다.
    from .assumptions import LONGTERM_SHEET

    state["grids"][LONGTERM_SHEET] = {
        "key": "근속연수",
        "rows": [
            [str(years), *[str(days)] * len(groups)]
            for years, days in ((10, 10), (20, 20), (30, 30))
        ],
    }
    return form.write_state(state, path)


def write_case_pack(
    directory: str | Path, *, seed: int = 20251231,
    base_date: _dt.date | None = None,
) -> list[Path]:
    """세 사례의 명부와 짝이 되는 기초율을 한꺼번에 만든다."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    for spec in CASES:
        report = directory / f"{spec.title}_특이사항.txt"
        made.append(write_case_roster(
            spec, directory / f"{spec.title}.xlsx", seed=seed, report_path=report,
            base_date=base_date,
        ))
        made.append(write_case_assumptions(spec, directory / f"{spec.title}_기초율.xlsx"))
        made.append(report)
    return made


def write_case_rosters(
    directory: str | Path, *, seed: int = 20251231,
    base_date: _dt.date | None = None,
) -> list[Path]:
    """세 사례의 명부만 만든다."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    return [
        write_case_roster(
            spec, directory / f"{spec.title}.xlsx", seed=seed, base_date=base_date,
        )
        for spec in CASES
    ]
