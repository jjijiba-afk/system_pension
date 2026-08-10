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
    임원 세법한도 프로즌, 연봉제 전환 누진 보전, 프로즌 DC전환자, DC전환 후
    퇴직, 명예퇴직, 사망 정액 가산금 같은 것들이며 비고란에 무엇인지 적어 둔다.
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

__all__ = ["CASES", "CaseSpec", "write_case_roster", "write_case_rosters"]

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
            "· 전입자·가산근속·개별 지급배수가 섞여 있습니다.",
        ),
        flags={
            "dc_share": 0.14, "settlement_share": 0.10, "wage_peak_share": 0.35,
            "over_nra_share": 0.05, "transfer_in_share": 0.05,
            "multiple_share": 0.05,
            "added_service_share": 0.06, "longterm_share": 0.55,
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
            "  임원 세법한도 프로즌(같은 사번 두 줄) · 연봉제 전환 누진 보전 ·",
            "  프로즌 DC전환자 · DC전환 후 퇴직(재직·퇴직 사번 중복) · 퇴직예정자 ·",
            "  명예퇴직 예정자 · 정년 시 기본급 추가지급 · 가산근속 · 사망 정액 가산금 ·",
            "  명예퇴직 위로금 · 임금 단위 혼재 · 장기급여 대상 표기 혼재.",
            "· 평균임금 체크금액을 1,000,000원으로 두어 그 미만인 사람도 걸립니다.",
        ),
        flags={
            "dirty": True, "practice": True, "dc_share": 0.06,
            "settlement_share": 0.05, "longterm_share": 0.35,
            "added_service_share": 0.04, "multiple_share": 0.03,
        },
    ),
)


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
        "gender": "남" if rng.random() < (0.78 if executive else 0.62) else "여",
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

    if rng.random() < flags.get("added_service_share", 0):
        if rng.random() < 0.6:
            row["added_service_years"] = rng.choice((0.5, 1, 1.5, 2))
        else:
            row["deducted_service_years"] = rng.choice((0.5, 1))

    if rng.random() < flags.get("multiple_share", 0):
        row["payout_multiple"] = rng.choice((1.5, 2.0, 2.5))

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
        ("added_service_years", lambda r: True,
         lambda r: rng.choice((0.5, 1, 2)), "added_service_share"),
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
        "gender": "남" if rng.random() < 0.62 else "여",
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
    ("임원 세법한도 프로즌",
     "같은 사번이 두 줄. 2019년 이전은 3배수·이후는 2배수로 지급구간이 갈린다."),
    ("연봉제 전환 누진 보전",
     "호봉제 시절 근속분의 누진 배수를 보전한다. 근속은 이어지고 배수만 갈린다."),
    ("프로즌 DC전환자",
     "DC 로 전환했지만 전환 전 과거분은 퇴직금으로 남아 있다."),
    ("DC전환 후 퇴직 — 사번 중복",
     "같은 사번이 재직자명부와 퇴직자명부에 함께 있다."),
    ("퇴직예정자",
     "기준일 뒤 퇴사가 확정돼 비고에 적혀 왔다. 기준일 현재는 재직자다."),
    ("명예퇴직 예정자",
     "명예퇴직 산정용 임금이 따로 적혀 있다."),
    ("정년퇴직 시 기본급 추가지급",
     "정년으로 나가는 사람에게 기본급을 얹어 준다(전장직원 예우)."),
    ("가산근속(법정제)",
     "군경력·휴직 보전으로 근속을 더해 준다."),
    ("사망 추가지급",
     "재직 중 사망하면 정액 가산금 5,000만원을 얹는다."),
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

    # ── 임원 세법한도 프로즌 — 같은 사번을 두 줄로 나눈다 ──────────
    exec_row = take(actives, employee_type=_TYPE_EXEC)
    hire = date_of(exec_row, "hire_date")
    split = _dt.date(2019, 12, 31)
    if hire < split < base:
        wage = float(exec_row["monthly_wage"])
        exec_row["period_end"] = split.isoformat()
        exec_row["payout_multiple"] = 3.0
        exec_row["note"] = "19.12.31 이전 기간만 3배수 (세법한도 프로즌)"
        later = dict(exec_row)
        later["period_start"] = (split + _dt.timedelta(days=1)).isoformat()
        later.pop("period_end", None)
        later["payout_multiple"] = 2.0
        later["monthly_wage"] = int(wage * 1.35 / 1_000) * 1_000
        later["note"] = "20.1.1 이후 기간만 2배수"
        actives.insert(actives.index(exec_row) + 1, later)
        planted["임원 세법한도 프로즌"] = exec_row["employee_id"]

    # ── 연봉제 전환 누진 보전 ────────────────────────────────────
    row = take(actives, employee_type=_TYPE_STAFF)
    served = (base - date_of(row, "hire_date")).days / 365.25
    if served > 8:
        row["progressive_service"] = round(served * 0.45, 1)
        row["progressive_rate"] = rng.choice((1.2, 1.3, 1.5))
        row["note"] = "연봉제 전환 이전 누진 보전 + 이후 법정제"
        planted["연봉제 전환 누진 보전"] = row["employee_id"]

    # ── 프로즌 DC전환자 (과거분은 퇴직금으로 남음) ───────────────
    frozen = take(actives, employee_type=_TYPE_STAFF)
    frozen["plan"] = "DC"
    moved = base - _dt.timedelta(days=rng.randint(400, 2_000))
    if moved > date_of(frozen, "hire_date"):
        frozen["settlement_date"] = moved.isoformat()
        frozen["settlement_amount"] = int(
            float(frozen["monthly_wage"]) * rng.uniform(2, 7) / 1_000) * 1_000
    frozen["note"] = "DC 전환(프로즌). 전환 전 과거분은 퇴직금 지급"
    planted["프로즌 DC전환자"] = frozen["employee_id"]

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

    # ── 가산근속(법정제) ─────────────────────────────────────────
    added = take(actives)
    added["added_service_years"] = rng.choice((1, 1.5, 2))
    added["note"] = "군경력 가산근속"
    planted["가산근속(법정제)"] = added["employee_id"]

    # ── 사망 추가지급 · 명예퇴직 위로금 ──────────────────────────
    if len(retirees) >= 2:
        dead, honor = rng.sample(retirees, 2)
        dead["reason"] = "2"
        dead["other_payment"] = 50_000_000
        dead["note"] = "재직 중 사망 — 정액 가산금 5,000만원 별도"
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

    for row in sample(0.03):                       # 성별 표기 혼재
        row["gender"] = rng.choice(("M", "F", "1", "2", "남자", ""))

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
        ("가산 근속연수 있음", count(actives, "added_service_years")),
        ("차감 근속연수 있음", count(actives, "deducted_service_years")),
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
            "  · 성별 표기 혼재 (M / F / 1 / 2 / 남자 / 빈 값)",
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
        "[1)일반사항 — 담당자가 채워 보낸 것으로 두었습니다]",
        "  이 명부를 올리면 아래 항목이 화면에 저절로 들어갑니다. 다시 적을 필요가 없습니다.",
        "  · 2번 대상 회계기간 — 산출기준일과 시작일",
        "  · 4번 할인율 회사채 신용등급",
        "  · 5-1) 퇴직급여추계액 변동내역 — 퇴직자명부의 지급액과 맞춰 두었습니다",
        "  · 5-2) 사외적립자산 변동내역 — 검산줄(기초+유입−유출=기말)이 0 원으로 맞습니다",
        "  · 5-3) 사외적립자산 세부내역 — 문단 142 공시용 분류별 공정가치",
        "  · 7-2) 기중 장기근속 지급액",
        "",
        "[같이 만든 기초율]",
        f"  {spec.title}_기초율.xlsx — 이 명부의 직군 매핑이 들어 있습니다.",
        "  기초율의 '지급규정' 시트가 명부 Input 의 직군 규칙보다 우선합니다.",
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


def _write_general_sheet(
    ws, spec: CaseSpec, actives: list[dict[str, Any]],
    retirees: list[dict[str, Any]], base: _dt.date, rng: random.Random, st,
) -> None:
    """``1)일반사항`` 을 자료요청서 서식대로 채운다.

    담당자가 실제로 채워 보내는 항목(회계기간·신용등급·퇴직급여 변동내역·
    사외적립자산)을 시험 명부에도 넣어 둔다. 이 값들은 산출 화면이 그대로
    읽어 채우므로, 없으면 '명부만 올려도 되는가' 를 시험할 수 없다.
    """
    ws.cell(1, 2, f"시험용 자료요청서 — {spec.key}").font = st["title_font"]

    period_start = base.replace(year=base.year - 1) + _dt.timedelta(days=1)

    ws.cell(22, 2, "2.")
    ws.cell(22, 3, "대상 회계기간").font = st["title_font"]
    ws.cell(23, 3, "기시")
    ws.cell(23, 4, "기말")
    ws.cell(24, 3, period_start)
    ws.cell(24, 4, base)

    ws.cell(49, 2, "4.")
    ws.cell(49, 3, "할인율 회사채 신용등급").font = st["title_font"]
    ws.cell(53, 3, spec.flags.get("credit_grade", "AA0"))

    # ── 5-1) 퇴직급여추계액 변동내역 ────────────────────────────
    paid = _obligation_totals(retirees)
    settlement = sum(
        float(row.get("settlement_amount") or 0) for row in actives
    )
    transfer_in = sum(float(row.get("transfer_in_amount") or 0) for row in actives)

    ws.cell(60, 2, "5.")
    ws.cell(60, 3, "퇴직급여").font = st["title_font"]
    ws.cell(65, 3, "1) 퇴직급여추계액 변동내역 (발생기준 작성)").font = st["title_font"]
    ws.cell(66, 3, "구분").font = st["head_font"]
    ws.cell(66, 3).fill = st["head_fill"]
    ws.cell(66, 5, "추계액").font = st["head_font"]
    ws.cell(66, 5).fill = st["head_fill"]

    obligation = (
        ("(+)증가", "계열사 전입", transfer_in),
        ("", "합병", 0.0),
        ("(-)감소", "퇴직금 지급액", paid["benefits_paid"]),
        ("", "중간정산금", settlement),
        ("", "DC전환", paid["dc_converted"]),
        ("", "퇴직위로금", paid["other_paid"]),
        ("", "계열사 전출", paid["transfer_out"]),
        ("", "사업처분/분할", paid["disposal"]),
    )
    for offset, (group, label, amount) in enumerate(obligation):
        row = 67 + offset
        if group:
            ws.cell(row, 3, group)
        ws.cell(row, 4, label)
        ws.cell(row, 5, round(amount))

    # ── 5-2) 사외적립자산 변동내역 ──────────────────────────────
    # 검산줄이 맞아떨어지도록 **기말을 역산** 한다. 기초와 유출입을 임의로
    # 넣고 기말도 임의로 넣으면 표가 스스로 안 맞아, 산출이 그 차이를
    # 경고로 뱉는다 — 시험 자료가 시스템을 거짓으로 고발하는 셈이다.
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

    ws.cell(76, 3, "2) 사외적립자산 변동내역 (현금기준 작성)").font = st["title_font"]
    for col, title in ((3, "구분"), (5, "DB퇴직연금,\n퇴직보험"),
                       (6, "국민연금전환금"), (7, "합계")):
        cell = ws.cell(77, col, title)
        cell.font = st["head_font"]
        cell.fill = st["head_fill"]

    ws.cell(78, 3, period_start)
    ws.cell(78, 5, opening - national_pension)
    if national_pension:
        ws.cell(78, 6, national_pension)
    ws.cell(78, 7, opening)

    assets = (
        ("(+)증가", "부담금납입", contributions),
        ("", "이자수익", actual_return),
        ("", "계열사 전입", 0.0),
        ("", "합병", 0.0),
        ("(-)감소", "퇴직금", fund_paid),
        ("", "중간정산금", 0.0),
        ("", "DC전환", 0.0),
        ("", "계열사 전출", 0.0),
        ("", "사업처분/분할", 0.0),
        ("", "운용관리수수료", management_fee),
        ("", "자산관리수수료", custody_fee),
    )
    for offset, (group, label, amount) in enumerate(assets):
        row = 79 + offset
        if group:
            ws.cell(row, 3, group)
        ws.cell(row, 4, label)
        ws.cell(row, 5, round(amount))
        ws.cell(row, 7, round(amount))

    ws.cell(90, 3, base)
    ws.cell(90, 5, round(closing - national_pension))
    if national_pension:
        ws.cell(90, 6, national_pension)
    ws.cell(90, 7, round(closing))
    ws.cell(91, 3, "검증")

    # ── 5-3) 사외적립자산 세부내역 ──────────────────────────────
    ws.cell(93, 3, "3) 사외적립자산 세부내역").font = st["title_font"]
    ws.cell(95, 3, "구분").font = st["head_font"]
    ws.cell(95, 3).fill = st["head_fill"]
    ws.cell(95, 5, "금액").font = st["head_font"]
    ws.cell(95, 5).fill = st["head_fill"]
    cash = round(closing * 0.8, -3)
    breakdown = (("⑴ 현금 및 현금등가물", cash), ("⑶ 채무상품", round(closing) - cash))
    for offset, (label, amount) in enumerate(breakdown):
        ws.cell(96 + offset, 3, label)
        ws.cell(96 + offset, 5, amount)
    ws.cell(96 + len(breakdown), 3, "합계")
    ws.cell(96 + len(breakdown), 5, sum(a for _, a in breakdown))

    # ── 7-2) 기중 장기근속 지급액 ───────────────────────────────
    longterm_paid = sum(float(row.get("longterm_payment") or 0) for row in retirees)
    ws.cell(123, 2, "7.")
    ws.cell(123, 3, "기타장기종업원급여").font = st["title_font"]
    ws.cell(133, 3, "2) 기중 장기근속 지급액").font = st["title_font"]
    ws.cell(135, 3, "(-)감소")
    ws.cell(135, 4, "장기근속 지급액")
    ws.cell(135, 5, round(longterm_paid))
    ws.cell(136, 3, "(+)증가")
    ws.cell(136, 4, "장기근속 받은금액")
    ws.cell(136, 5, 0)

    for col in (2, 3, 4, 5, 6, 7):
        ws.column_dimensions[ws.cell(1, col).column_letter].width = 22 if col == 4 else 16


def write_case_roster(
    spec: CaseSpec, path: str | Path, *, seed: int = 20251231,
    report_path: str | Path | None = None,
    base_date: _dt.date | None = None,
) -> Path:
    """사례 하나를 명부 통합문서로 쓴다.

    :param report_path: 주면 그 자리에 특이사항 안내문(.txt)도 쓴다.
    :param base_date: 명부의 산출기준일. 생략하면 :data:`BASE_DATE`.
        연령·근속·퇴사일이 모두 이 날짜를 기준으로 만들어진다.
    """
    import openpyxl

    from .layout import ACTIVE_HEADER_ALIASES, RETIRED_HEADER_ALIASES
    from .readers import (
        ACTIVE_COLUMNS,
        ACTIVE_FIRST_ROW,
        ACTIVE_SHEET,
        RETIRED_COLUMNS,
        RETIRED_FIRST_ROW,
        RETIRED_SHEET,
    )
    from .samples import _style

    base = base_date or BASE_DATE
    rng = random.Random(f"{seed}:{spec.key}:{base}")
    actives = [_make_active(rng, spec, i + 1, base) for i in range(spec.active)]
    retirees = [_make_retired(rng, spec, i + 1, base) for i in range(spec.retired)]
    _ensure_special_cases(rng, spec, actives, base)
    # 특이사항을 먼저 심고 그 위에 자료 오류를 뿌린다. 순서를 바꾸면 오류가
    # 특이사항 줄을 덮어써 무엇을 보려던 자료인지 알 수 없게 된다.
    planted: dict[str, str] = {}
    if spec.flags.get("practice"):
        planted = _add_practice_cases(rng, actives, retirees, base)
    if spec.flags.get("dirty"):
        _spoil_active(rng, actives, base)
        _spoil_retired(rng, retirees)

    path = Path(path)
    st = _style()
    wb = openpyxl.Workbook()

    def sheet(name, columns, aliases, first_row, rows):
        ws = wb.create_sheet(name)
        header_row = first_row - 2
        ws.cell(header_row, 2, "순번").font = st["head_font"]
        ws.cell(header_row, 2).fill = st["head_fill"]

        # 고정 서식에 없는 항목(누진 보전·지급구간 등)은 회사가 오른쪽에 열을
        # 덧붙여 보낸다. 그 모양 그대로 만들어야 머리글로 열을 찾아내는
        # 경로까지 시험 자료가 짚고 간다.
        placed = dict(columns)
        used = [key for record in rows for key in record]
        extras = [key for key in dict.fromkeys(used) if key not in placed]
        next_index = max(col.index for col in columns.values()) + 1
        extra_index = {}
        for offset, key in enumerate(extras):
            extra_index[key] = next_index + offset

        for key, col in columns.items():
            label = aliases.get(key, (col.label,))[0]
            cell = ws.cell(header_row, col.index, label)
            cell.font = st["head_font"]
            cell.fill = st["head_fill"]
            cell.alignment = st["center"]
            ws.column_dimensions[cell.column_letter].width = max(10, min(22, len(label) + 4))
        for key, index in extra_index.items():
            label = aliases.get(key, (key,))[0]
            cell = ws.cell(header_row, index, label)
            cell.font = st["head_font"]
            cell.fill = st["head_fill"]
            cell.alignment = st["center"]
            ws.column_dimensions[cell.column_letter].width = max(12, min(22, len(label) + 4))

        for offset, record in enumerate(rows):
            row = first_row + offset
            ws.cell(row, 2, offset + 1)
            for key, value in record.items():
                column = columns.get(key)
                index = column.index if column is not None else extra_index.get(key)
                if index is None or value == "":
                    continue
                ws.cell(row, index, value)
        ws.freeze_panes = ws.cell(first_row, 3)

    sheet(ACTIVE_SHEET, ACTIVE_COLUMNS, ACTIVE_HEADER_ALIASES, ACTIVE_FIRST_ROW, actives)
    sheet(RETIRED_SHEET, RETIRED_COLUMNS, RETIRED_HEADER_ALIASES, RETIRED_FIRST_ROW, retirees)

    # ── Input 시트 ──────────────────────────────────────────────
    ws = wb.create_sheet("Input", 0)
    ws.cell(1, 2, f"시험용 명부 — {spec.key}").font = st["title_font"]
    ws.cell(2, 2, spec.summary).font = st["note_font"]
    ws.cell(3, 2, "산출기준일")
    ws.cell(3, 3, base.isoformat())
    ws.cell(5, 2, "평균임금 체크금액")
    ws.cell(5, 3, spec.wage_check)

    # 직군 규칙표의 자리는 정해져 있다 — 머리글 11행, 값 12행부터. 설명을
    # 위쪽에 늘어놓다 이 자리를 밀면 규칙을 통째로 못 읽어 전원이
    # '직군을 찾지 못함' 오류가 난다.
    ws.cell(10, 2, "직군 규칙").font = st["title_font"]
    head = 11
    headers = ("명부직군", "변환직군명", "퇴직급여 정년연령", "장기급여 정년연령",
               "정년초과 가산연령")
    for col, title in enumerate(headers, start=2):
        cell = ws.cell(head, col, title)
        cell.font = st["head_font"]
        cell.fill = st["head_fill"]
        cell.alignment = st["center"]
        ws.column_dimensions[cell.column_letter].width = 18

    # 직군 규칙은 명부에 실제로 쓰인 표기로 적는다. '자료불량' 사례는 일부러
    # 한 직군을 빼서 '직군을 찾지 못함' 오류가 나게 둔다.
    from .jobgroup import suggest_group

    used = [name for name, _ in spec.groups if name]
    if spec.flags.get("dirty"):
        used = used[:-1]
    for offset, name in enumerate(used):
        mapped = suggest_group(name, _TYPE_EXEC if _is_executive(name) else _TYPE_STAFF)
        nra = 65 if _is_executive(name) else 60
        for col, value in enumerate((name, mapped, nra, nra, 2), start=2):
            ws.cell(head + 1 + offset, col, value)

    # 설명은 규칙표를 훑는 구간(12~36행) 아래에 적는다. 그 안에 두면 설명
    # 문장이 직군 규칙 한 줄로 읽힌다.
    for offset, note in enumerate(spec.notes):
        ws.cell(40 + offset, 2, note).font = st["note_font"]

    # 자료요청서 앞장. 회계기간·신용등급·사외적립자산을 담당자가 채워 보낸
    # 것처럼 넣어 두면, 명부 하나만 올려도 산출 화면이 그대로 읽어 채운다.
    _write_general_sheet(
        wb.create_sheet("1)일반사항", 1), spec, actives, retirees, base, rng, st
    )

    del wb["Sheet"]
    wb.save(path)

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

    mapping = _case_mapping(spec)
    groups = list(dict.fromkeys(row[2] for row in mapping))

    path = Path(path)
    # 표준률(15~70세)로 한 벌 채운 뒤, 이 사례의 직군 매핑을 얹는다.
    write_standard_assumptions(path, job_groups=tuple(groups))
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
