"""아이패드 웹앱(브라우저) 이 부르는 JSON API.

웹앱은 Pyodide 로 이 패키지를 브라우저 안에서 그대로 돌린다. 자바스크립트와
파이썬 사이 경계는 얇을수록 좋다 — 그래서 진입점을 :func:`api` 하나로 두고,
요청·응답을 JSON 문자열 하나씩으로 주고받는다. 화면 로직은 전부 여기(=엔진
쪽)에 있으므로 PC 에서 pytest 로 검증할 수 있고, 브라우저는 표를 그리는 일만
한다.

응답은 항상 ``{"ok": true, …}`` 또는 ``{"ok": false, "error": …}`` 다.
파일은 자바스크립트가 Pyodide 가상 파일시스템에 먼저 써 두고 경로만 넘긴다.

등록 자료(금리표·표준률)는 :mod:`pension.library` 를 그대로 쓴다. 브라우저에선
``PENSION_HOME`` 을 IndexedDB 에 마운트한 폴더로 잡아 두면 새로고침해도 남는다.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Final

from . import assumption_form as form
from . import clients
from .actuarial import FRACTION_MODES, SERVICE_BASES
from .assumptions import (
    ATTRIBUTIONS,
    BENEFIT_MODES,
    EXIT_CAUSES,
    LONGTERM_TIMINGS,
    LONGTERM_TYPES,
)
from .formula import FUNCTIONS, VARIABLES
from .jobgroup import DEFAULT_GROUPS, scan_roster, suggest_group
from .library import (
    CURVE_KIND,
    PRESET_KIND,
    RATES_KIND,
    ROSTER_KIND,
    entries,
    find_entry,
    library_dir,
    read_settings,
    register,
    remove,
    resolve_default,
    write_settings,
)
from .normalize import text
from .rostergen import CASES
from .yieldcurve import INVESTMENT_GRADES, pick_curve, read_yield_curves

__all__ = ["api"]


def api(payload: str) -> str:
    """웹앱 요청 하나를 처리한다. ``{"op": …, …}`` → 응답 JSON."""
    try:
        request = json.loads(payload)
        op = text(request.get("op"))
        handler = _OPS.get(op)
        if handler is None:
            raise ValueError(f"모르는 요청입니다: {op or '(빈 op)'}")
        result = handler(request)
        return json.dumps({"ok": True, **result}, ensure_ascii=False)
    except Exception as exc:  # 경계가 한 곳이므로 여기서만 잡는다
        return json.dumps(
            {"ok": False, "error": f"{exc}" or type(exc).__name__}, ensure_ascii=False
        )


# ── 화면 서식 ────────────────────────────────────────────────────

def _meta(_request: dict) -> dict[str, Any]:
    """화면을 그리는 데 필요한 서식·선택지 일체. 하드코딩을 JS 에 두지 않는다."""
    return {
        "sheets": [
            {
                "sheet": spec["sheet"], "tab": spec["tab"], "key": spec["key"],
                "fixed": list(spec["fixed"]), "note": spec["note"],
                "key_choices": list(spec["key_choices"]),
                "column_panel": spec.get("column_panel", ""),
                "allow_extra": bool(spec.get("allow_extra")),
                "extra_hint": spec.get("extra_hint", ""),
            }
            for spec in form.FORM_SHEETS
        ],
        "editor_groups": [
            {
                "name": group["name"], "note": group["note"],
                "sections": [dict(section) for section in group["sections"]],
            }
            for group in form.EDITOR_GROUPS
        ],
        "default_groups": list(DEFAULT_GROUPS),
        "apply_choices": list(form.APPLY_CHOICES),
        "rounding_units": list(form.ROUNDING_UNITS),
        "service_bases": list(SERVICE_BASES),
        "fraction_modes": list(FRACTION_MODES),
        "benefit_modes": list(BENEFIT_MODES),
        "longterm_types": list(LONGTERM_TYPES),
        "exit_causes": list(EXIT_CAUSES),
        "attributions": list(ATTRIBUTIONS),
        "exit_cause_headers": list(form.EXIT_CAUSE_HEADERS),
        "longterm_timings": list(LONGTERM_TIMINGS),
        "grades": [*INVESTMENT_GRADES, "국고채"],
        "kinds": {"curve": CURVE_KIND, "rates": RATES_KIND,
                  "roster": ROSTER_KIND, "preset": PRESET_KIND},
        "cases": [
            {"key": spec.key, "title": spec.title, "summary": spec.summary,
             "active": spec.active, "retired": spec.retired}
            for spec in CASES
        ],
        "formula_variables": dict(VARIABLES),
        "formula_functions": sorted(FUNCTIONS),
    }


# ── 산출가정 state ───────────────────────────────────────────────

def _state_new(request: dict) -> dict[str, Any]:
    return {"state": form.empty_state(request.get("groups"))}


def _state_example(request: dict) -> dict[str, Any]:
    return {"state": form.example_state(request.get("groups"))}


def _state_check(request: dict) -> dict[str, Any]:
    return {"problems": form.state_problems(request["state"])}


def _state_write(request: dict) -> dict[str, Any]:
    """state 를 기초율 워크북으로 쓴다. 문제가 있으면 문제 목록을 돌려준다."""
    problems = form.state_problems(request["state"])
    if problems:
        return {"problems": problems, "written": False}
    form.write_state(request["state"], request["path"])
    return {"problems": [], "written": True}


def _state_read(request: dict) -> dict[str, Any]:
    return {"state": form.read_state(request["path"])}


def _standard_state(request: dict) -> dict[str, Any]:
    """내장 표준률로 채운 state. 등록해 둔 표준률이 없어도 출발점은 있어야 한다."""
    from .samples import write_standard_assumptions

    groups = [text(g) for g in request.get("groups", []) if text(g)]
    with tempfile.TemporaryDirectory() as tmp:
        path = write_standard_assumptions(
            Path(tmp) / "표준.xlsx", job_groups=tuple(groups or DEFAULT_GROUPS)
        )
        return {"state": form.read_state(path)}


# ── 수식 ─────────────────────────────────────────────────────────

def _formula_check(request: dict) -> dict[str, Any]:
    return {"error": form.check_formula(request.get("source", ""))}


def _formula_preview(request: dict) -> dict[str, Any]:
    formulas = {
        text(group): text(source)
        for group, source in request.get("formulas", {}).items()
        if text(source)
    }
    if not formulas:
        raise ValueError("수식 방식으로 지정된 규정이 없습니다")
    return {"preview": form.formula_preview(formulas)}


# ── 등록 자료(기본가정) ──────────────────────────────────────────

def _library_list(_request: dict) -> dict[str, Any]:
    settings = read_settings()
    result: dict[str, Any] = {}
    for kind in (CURVE_KIND, RATES_KIND, ROSTER_KIND, PRESET_KIND):
        default = resolve_default(kind)
        result[kind] = {
            "entries": [
                {
                    "name": e.name,
                    "registered": str(e.registered or ""),
                    "suffix": e.path.suffix.lower(),
                    "path": str(e.path),
                }
                for e in entries(kind)
            ],
            "default": default.name if default else "",
            "pinned": settings.get(kind, ""),
        }
    return {"library": result, "backup": settings.get(_BACKUP_STAMP, "")}


def _library_register(request: dict) -> dict[str, Any]:
    kind = text(request.get("kind"))
    entry = register(kind, request["path"], name=request.get("name", ""))
    return {"registered": entry.name, **_library_list({})}


def _library_remove(request: dict) -> dict[str, Any]:
    kind = text(request.get("kind"))
    name = text(request.get("name"))
    if not remove(kind, name):
        raise ValueError(f"등록된 {kind} '{name}' 이(가) 없습니다")
    settings = read_settings()
    if settings.get(kind) == name:
        settings.pop(kind)
        write_settings(settings)
    return _library_list({})


def _library_pin(request: dict) -> dict[str, Any]:
    """기본 지정. 빈 이름이면 지정 해제(최신 등록이 기본이 된다)."""
    kind = text(request.get("kind"))
    name = text(request.get("name"))
    if name and find_entry(kind, name) is None:
        raise ValueError(f"등록된 {kind} '{name}' 이(가) 없습니다")
    settings = read_settings()
    if name:
        settings[kind] = name
    else:
        settings.pop(kind, None)
    write_settings(settings)
    return _library_list({})


def _library_path(request: dict) -> dict[str, Any]:
    """등록된 자료의 실제 경로. 저장해 둔 명부를 산출에 그대로 넘길 때 쓴다."""
    kind = text(request.get("kind"))
    entry = find_entry(kind, text(request.get("name")))
    if entry is None:
        raise ValueError(f"등록된 {kind} '{request.get('name')}' 이(가) 없습니다")
    return {"path": str(entry.path), "name": entry.name}


def _registered_path(kind: str, name: str) -> Path:
    entry = find_entry(kind, name) if name else resolve_default(kind)
    if entry is None:
        raise ValueError(
            f"등록된 {kind} 이(가) 없습니다. [기본가정 관리] 에서 먼저 등록하세요"
        )
    return entry.path


def _curve_grades(request: dict) -> dict[str, Any]:
    """등록된 금리표 파일에 실제로 들어 있는 등급 목록."""
    path = _registered_path(CURVE_KIND, text(request.get("name")))
    return {"grades": [c.label for c in read_yield_curves(path)]}


def _curve_rows(request: dict) -> dict[str, Any]:
    """등록된 금리표에서 할인율 곡선을 뽑아 그리드 행으로 준다."""
    name = text(request.get("name"))
    path = _registered_path(CURVE_KIND, name)
    curve = pick_curve(read_yield_curves(path), text(request.get("grade")))
    if curve is None:
        raise ValueError(f"'{request.get('grade')}' 등급을 찾지 못했습니다")
    return {
        "rows": [[f"{years:g}", f"{rate * 100:.4f}%"] for years, rate in curve.points],
        "label": curve.label,
        "base_date": str(curve.base_date or ""),
    }


def _preset_save(request: dict) -> dict[str, Any]:
    """지금 편집 중인 산출가정 한 벌을 이름 붙여 등록한다.

    지급률·지급규정·직군 매핑까지 통째로 들어간다. 같은 회사를 다음 결산에
    다시 산출하거나 규정이 같은 계열사를 맡을 때 그대로 꺼내 쓴다.
    """
    name = text(request.get("name"))
    if not name:
        raise ValueError("가정세트 이름을 입력하세요 (예: A사 퇴직금규정)")
    problems = form.state_problems(request["state"])
    if problems:
        return {"problems": problems, "saved": False}

    with tempfile.TemporaryDirectory() as tmp:
        path = form.write_state(request["state"], Path(tmp) / f"{name}.xlsx")
        entry = register(PRESET_KIND, path, name=name)
    return {"problems": [], "saved": True, "name": entry.name, **_library_list({})}


def _preset_state(request: dict) -> dict[str, Any]:
    """등록된 가정세트를 편집 화면으로 되돌린다."""
    path = _registered_path(PRESET_KIND, text(request.get("name")))
    return {"state": form.read_state(path)}


def _rates_state(request: dict) -> dict[str, Any]:
    """등록된 표준률 워크북을 편집 화면 state 로 불러온다."""
    path = _registered_path(RATES_KIND, text(request.get("name")))
    return {"state": form.read_state(path)}


# ── 명부 연동 ────────────────────────────────────────────────────

def _roster_scan(request: dict) -> dict[str, Any]:
    """명부에서 (직군, 임직원구분) 조합을 훑는다. '직군 매핑' 탭용."""
    from .workbook import open_workbook

    groups = [text(g) for g in request.get("groups", []) if text(g)]
    book = open_workbook(request["path"])
    try:
        found = scan_roster(book)
    finally:
        book.close()
    return {
        "found": [
            {
                "source": g.source_name, "kind": g.employee_type,
                "normalized": g.normalized_type,
                "active": g.active, "retired": g.retired,
                "suggest": suggest_group(
                    g.source_name, g.employee_type, groups or DEFAULT_GROUPS
                ),
            }
            for g in found
        ]
    }


def _roster_groups(request: dict) -> dict[str, Any]:
    """명부 ``Input`` 시트의 변환 직군명. '명부에서 직군 불러오기' 용."""
    from .config import read_config
    from .workbook import open_workbook

    book = open_workbook(request["path"])
    try:
        config = read_config(book)
    finally:
        book.close()
    names = [r.mapped_name for r in config.job_group_rules if r.mapped_name]
    return {"groups": list(dict.fromkeys(names))}


def _general_info(request: dict) -> dict[str, Any]:
    """자료요청서 ``1)일반사항`` 에서 화면에 채울 것을 전부 읽는다.

    6번 지급규정 초안은 ``values`` 로(지급규정 탭), 담당자가 이미 채워 보낸
    2·4·5번 표는 ``fields`` 로(산출 탭 입력칸) 나간다. 산출 엔진은 칸이 비면
    이 표를 알아서 쓰지만, **화면에 보이지 않으면 담당자는 아무것도 읽히지
    않은 줄 안다.** 값을 눈에 보이게 넣어 두어야 확인하고 고칠 수 있다.
    """
    from .general_info import read_general_info
    from .workbook import open_workbook

    book = open_workbook(request["path"])
    try:
        info = read_general_info(book)
    finally:
        book.close()

    draft = info.draft
    values: dict[str, str] = {}
    if draft.min_service_years is not None:
        values["min_service"] = f"{draft.min_service_years:g}"
    if draft.staff_nra is not None:
        values["nra"] = str(draft.staff_nra)
    if draft.executive_nra is not None:
        values["executive_nra"] = str(draft.executive_nra)
    if draft.service_basis is not None:
        values["basis"] = draft.service_basis
    if draft.service_fraction is not None:
        values["fraction"] = draft.service_fraction
    if draft.rounding_unit is not None:
        values["unit"] = form.UNIT_LABELS.get(draft.rounding_unit, "없음")

    # ── 산출 탭 입력칸 ──────────────────────────────────────────
    fields: dict[str, str] = {}
    found: list[str] = []
    if info.period_end is not None:
        fields["base_date"] = info.period_end.isoformat()
        found.append(f"산출기준일 {info.period_end}")
    if info.period_start is not None:
        fields["period_start"] = info.period_start.isoformat()
        found.append(f"시작일 {info.period_start}")

    assets = info.assets
    if not assets.is_empty():
        # 자산에서 나간 돈은 전부 뺀다 — 수수료도 자산을 줄인다.
        paid = assets.total_paid - assets.total_received
        fields.update(
            asset_opening=f"{assets.opening:.0f}",
            asset_contributions=f"{assets.contributions:.0f}",
            asset_paid=f"{paid:.0f}",
            asset_closing=f"{assets.closing:.0f}",
        )
        found.append(
            f"사외적립자산 기초 {assets.opening:,.0f}원 → 기말 {assets.closing:,.0f}원"
        )

    problems: list[str] = []
    if not assets.is_empty() and round(assets.difference) != 0:
        problems.append(
            f"사외적립자산 변동내역이 {assets.difference:,.0f}원 맞지 않습니다. "
            "회사가 보내온 표를 확인하세요"
        )

    return {
        "values": values,
        "evidence": dict(draft.evidence),
        "unread": list(draft.unread),
        "fields": fields,
        "found": found,
        "problems": problems,
        "grade": info.credit_grade,
    }


# ── 산출 ─────────────────────────────────────────────────────────

#: 마지막으로 성공한 산출. 보고서·사번 조회가 산출 화면과 같은 숫자를 쓰도록
#: 결과 객체를 그대로 들고 있는다(페이지를 닫으면 사라진다 — 저장은 run_save).
_LAST_RUN: dict[str, Any] = {}


def _run(request: dict) -> dict[str, Any]:
    """산출을 실행하고 화면 요약을 돌려준다. 결과 파일은 ``/work`` 아래에 남는다."""
    from .errors import PensionDataError, Severity
    from .members import write_member_export
    from .pipeline import PlanAssetInput, PriorPeriod, RunOptions, run_valuation
    from .report import write_report

    work = Path(request.get("work", "/work"))
    out = work / "산출결과.xlsx"
    # 전기 기초율까지 주면 증감분석이 경험조정과 가정변경효과를 나눠 낸다.
    prior_assumptions = text(request.get("prior_assumptions"))
    if prior_assumptions and not Path(prior_assumptions).exists():
        prior_assumptions = ""
    try:
        run = run_valuation(RunOptions(
            roster_path=Path(request["roster"]),
            assumptions_path=Path(request["assumptions"]),
            output_path=out,
            base_date=_as_date(request.get("base_date")),
            period_start=_as_date(request.get("period_start")),
            include_sensitivity=bool(request.get("sensitivity", True)),
            include_longterm=bool(request.get("longterm", True)),
            split_remeasurement=bool(request.get("split_remeasurement", False)),
            allow_errors=bool(request.get("force", False)),
            prior=PriorPeriod(
                dbo=float(request.get("prior_dbo") or 0),
                service_cost=float(request.get("prior_service_cost") or 0),
                discount_rate=float(request.get("prior_rate") or 0),
                assumptions_path=prior_assumptions,
                past_service_cost=float(request.get("past_service_cost") or 0),
                settlement_obligation=float(request.get("settlement_obligation") or 0),
                longterm_dbo=float(request.get("prior_longterm_dbo") or 0),
            ),
            plan_assets=PlanAssetInput(
                opening_fair_value=float(request.get("asset_opening") or 0),
                closing_fair_value=float(request.get("asset_closing") or 0),
                contributions=float(request.get("asset_contributions") or 0),
                benefits_paid=float(request.get("asset_paid") or 0),
                unpaid_benefits=float(request.get("unpaid_benefits") or 0),
                asset_ceiling=(float(request["asset_ceiling"])
                               if text(request.get("asset_ceiling")) else None),
                expected_contributions=float(
                    request.get("expected_contributions") or 0),
            ),
        ))
    except PensionDataError as exc:
        errors = [str(i) for i in exc.issues if i.severity is Severity.ERROR]
        return {"run": False, "errors": errors[:40], "more": max(0, len(errors) - 40)}

    write_report(run, out)
    write_member_export(run, work / "개인별결과.xlsx")

    _LAST_RUN.clear()
    _LAST_RUN.update({"run": run, "request": dict(request)})

    valuation = run.valuation
    if run.assumptions.discount.flat is None:
        single_rate = valuation.single_discount_rate()
        rate = f"{single_rate:.3%} (수익률곡선기법 단일할인율)"
    else:
        single_rate = run.assumptions.discount.level_rate
        rate = f"{single_rate:.3%}"
    summary = [
        ("산출기준일", str(run.config.base_date)),
        ("적용 할인율", rate),
        ("산출대상 인원", f"{valuation.headcount:,}명"),
        ("확정급여채무 (DBO)", f"{valuation.dbo:,.0f} 원"),
        ("당기근무원가", f"{valuation.service_cost:,.0f} 원"),
        ("이자원가 (차기)", f"{valuation.interest_cost:,.0f} 원"),
        ("듀레이션", f"{valuation.duration:.1f} 년"),
    ]
    if run.longterm is not None:
        summary.append(("장기급여채무", f"{run.longterm.dbo:,.0f} 원"))
    if run.rollforward is not None:
        roll = run.rollforward
        if roll.past_service_cost:
            summary.append(("과거근무원가(제도개정)", f"{roll.past_service_cost:,.0f} 원"))
        if roll.settlement_gain:
            summary.append(("정산손익", f"{roll.settlement_gain:,.0f} 원"))
        summary.append(("보험수리적손익", f"{roll.actuarial_gain_loss:,.0f} 원"))
        for label, amount in roll.assumption_steps:
            summary.append((f"  └ 가정변경 · {label}", f"{amount:,.0f} 원"))
    if run.plan_assets is not None:
        assets = run.plan_assets
        summary.append(("사외적립자산", f"{assets.closing_fair_value:,.0f} 원"))
        summary.append(("순확정급여부채", f"{assets.net_liability:,.0f} 원"))
        summary.append(("적립비율", f"{assets.funded_ratio:.1%}"))
        if assets.ceiling_effect:
            summary.append(("자산인식상한 차감액", f"{assets.ceiling_effect:,.0f} 원"))
    if run.longterm_rollforward is not None:
        summary.append(("장기급여 재측정(당기손익)",
                        f"{run.longterm_rollforward.remeasurement:,.0f} 원"))

    groups = [
        [name, f"{count:,}", f"{dbo:,.0f}", f"{sc:,.0f}"]
        for name, (count, dbo, sc) in valuation.by_job_group().items()
    ]
    excluded = [
        f"{count:,}명 — {reason}"
        for reason, count in valuation.exclusion_summary().items()
    ]
    issues = (
        f"오류 {len(run.issues.errors)}건 / 경고 {len(run.issues.warnings)}건 — "
        "상세는 결과 파일의 검증리포트 시트"
    )
    return {
        "run": True, "summary": summary, "groups": groups,
        "issues": issues, "excluded": excluded,
        # 화면에 보이는 요약은 사람이 읽을 서식이라 다시 숫자로 되돌리기 어렵다.
        # 다음 결산에서 전기값으로 끌어 쓸 수 있게 원래 숫자를 함께 남긴다.
        "values": {
            "base_date": str(run.config.base_date),
            "dbo": valuation.dbo,
            "service_cost": valuation.service_cost,
            "discount_rate": single_rate,
        },
        "rollforward": (
            [[label, amount] for label, amount in run.rollforward.as_rows()]
            if run.rollforward is not None else []
        ),
        "assets": (
            [[label, amount] for label, amount in run.plan_assets.as_rows()]
            + [[label, amount] for label, amount in run.plan_assets.net_rows()]
            if run.plan_assets is not None else []
        ),
    }


# ── 산출 내역 ────────────────────────────────────────────────────
# 산출 하나(명부 + 기초율 + 결과 + 요약)를 이름 붙여 통째로 보관한다.
# 등록 자료와 같은 PENSION_HOME 아래라, 브라우저에서는 IndexedDB 에 남는다.
# 산출은 **단체 안에** 들어간다(:mod:`pension.clients`). "2412" 처럼 결산기로만
# 이름을 지어도 단체가 갈라져 있어 헷갈리지 않고, 전기 산출 목록에 다른 단체가
# 섞이지 않는다.

_RUN_ROSTER_SUFFIXES = (".xlsx", ".xlsm", ".xls")


def _runs_dir(request: dict | None = None) -> Path:
    """산출을 넣을 폴더 — 요청이 가리키는 단체, 없으면 지금 고른 단체."""
    return clients.folder((request or {}).get("client", ""))


def _safe_run_name(name: str) -> str:
    """산출명을 폴더 이름으로. 경로 문자만 걷어내고 나머지는 그대로 둔다."""
    cleaned = re.sub(r'[\\/:*?"<>|]', " ", text(name)).strip()
    if not cleaned:
        raise ValueError("산출명을 입력하세요 (예: 2412 1번단체)")
    return cleaned


def _run_meta(folder: Path) -> dict[str, Any] | None:
    try:
        return json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _saved_roster(folder: Path) -> Path | None:
    for suffix in _RUN_ROSTER_SUFFIXES:
        candidate = folder / f"명부{suffix}"
        if candidate.exists():
            return candidate
    return None


def _run_save(request: dict) -> dict[str, Any]:
    """방금 마친 산출을 이름 붙여 보관한다. 같은 이름이 있으면 덮어쓴다."""
    name = _safe_run_name(request.get("name", ""))
    roster = Path(request["roster"])
    assumptions = Path(request["assumptions"])
    if not roster.exists() or not assumptions.exists():
        raise ValueError("저장할 명부·기초율이 없습니다. 먼저 산출을 실행하세요")

    folder = _runs_dir(request) / name
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)

    shutil.copy2(roster, folder / f"명부{roster.suffix.lower()}")
    shutil.copy2(assumptions, folder / "기초율.xlsx")
    work = Path(request.get("work", "/work"))
    for result_name in ("산출결과.xlsx", "개인별결과.xlsx"):
        source = work / result_name
        if source.exists():
            shutil.copy2(source, folder / result_name)

    meta = {
        "name": name,
        "saved": text(request.get("saved"))
                 or _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "roster_name": text(request.get("roster_name")) or roster.name,
        "report": request.get("report") or {},
        "options": request.get("options") or {},
    }
    (folder / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return _run_list(request)


def run_summaries(client: str = "") -> list[dict[str, Any]]:
    """한 단체의 산출 목록. 최근 저장한 것이 앞에 온다.

    :mod:`pension.clients` 가 단체별 건수를 셀 때도 이 함수를 쓴다.
    """
    runs = []
    for folder in clients.folder(client).iterdir():
        if not folder.is_dir():
            continue
        meta = _run_meta(folder)
        if meta is None:
            continue
        summary = dict(meta.get("report", {}).get("summary", []))
        runs.append({
            "name": meta.get("name", folder.name),
            "saved": meta.get("saved", ""),
            "roster_name": meta.get("roster_name", ""),
            "base_date": summary.get("산출기준일", ""),
            "headcount": summary.get("산출대상 인원", ""),
            "dbo": summary.get("확정급여채무 (DBO)", ""),
            "has_results": (folder / "산출결과.xlsx").exists(),
        })
    runs.sort(key=lambda r: r["saved"], reverse=True)
    return runs


def _run_list(request: dict) -> dict[str, Any]:
    chosen = text(request.get("client")) or clients.current()
    return {"runs": run_summaries(chosen), **_client_list({})}


def _run_folder(request: dict) -> Path:
    name = _safe_run_name(request.get("name", ""))
    folder = _runs_dir(request) / name
    if not folder.is_dir() or _run_meta(folder) is None:
        raise ValueError(f"저장된 산출 '{name}' 이(가) 없습니다")
    return folder


def _run_restore(request: dict) -> dict[str, Any]:
    """저장된 산출의 입력(명부·기초율)을 작업 폴더로 되가져온다."""
    folder = _run_folder(request)
    meta = _run_meta(folder)
    work = Path(request.get("work", "/work"))
    work.mkdir(parents=True, exist_ok=True)

    roster = _saved_roster(folder)
    if roster is None:
        raise ValueError("저장본에 명부가 없습니다")
    roster_target = work / f"저장명부{roster.suffix}"
    shutil.copy2(roster, roster_target)
    shutil.copy2(folder / "기초율.xlsx", work / "기초율저장본.xlsx")
    return {
        "meta": meta,
        "roster": str(roster_target),
        "assumptions": str(work / "기초율저장본.xlsx"),
    }


def _run_results(request: dict) -> dict[str, Any]:
    """저장된 결과 파일을 내려받을 수 있게 작업 폴더로 꺼낸다."""
    folder = _run_folder(request)
    work = Path(request.get("work", "/work"))
    files = {}
    for result_name in ("산출결과.xlsx", "개인별결과.xlsx"):
        source = folder / result_name
        if source.exists():
            target = work / f"저장_{result_name}"
            shutil.copy2(source, target)
            files[result_name] = str(target)
    if not files:
        raise ValueError("이 산출에는 저장된 결과 파일이 없습니다")
    return {"files": files}


def _run_delete(request: dict) -> dict[str, Any]:
    shutil.rmtree(_run_folder(request))
    return _run_list(request)


# ── 사번 조회 ────────────────────────────────────────────────────
# 전체 산출은 몇백 명의 합계라 "이 사람 채무가 왜 이 금액인가" 에는 답하지
# 못한다. 사번 하나를 같은 명부·기초율로 다시 산출해 연차별 근거까지 돌려준다.


def _member_detail(request: dict) -> dict[str, Any]:
    from .memberdetail import lookup

    if text(request.get("name")):
        # 저장된 산출에서 조회 — 그 산출이 쓴 명부·기초율 그대로.
        folder = _run_folder(request)
        roster = _saved_roster(folder)
        if roster is None:
            raise ValueError("저장본에 명부가 없습니다")
        roster_path, assumptions_path = str(roster), str(folder / "기초율.xlsx")
        meta = _run_meta(folder) or {}
        base_date = _as_date(meta.get("options", {}).get("base_date"))
    else:
        # 방금 산출한 명부·기초율 그대로 — 조회 결과가 산출 화면과 같은 값이 된다.
        last = _LAST_RUN.get("request") or {}
        roster_path = text(request.get("roster")) or text(last.get("roster"))
        assumptions_path = (text(request.get("assumptions"))
                            or text(last.get("assumptions")))
        if not roster_path or not Path(roster_path).exists():
            raise ValueError("먼저 산출을 실행하거나 저장된 산출을 고르세요")
        base_date = _as_date(request.get("base_date") or last.get("base_date"))

    return lookup(
        roster_path, assumptions_path, text(request.get("employee_id")),
        base_date=base_date,
    )


# ── 분석 화면 ────────────────────────────────────────────────────
# 방금 산출한 회차를 그림으로 본다. 산출을 다시 하면 이 값도 함께 바뀐다.


def _dashboard(request: dict) -> dict[str, Any]:
    from .dashboard import build

    if not _LAST_RUN:
        raise ValueError("먼저 산출을 실행하세요. 분석 화면은 방금 산출한 결과를 봅니다")
    return build(_LAST_RUN["run"], text(request.get("employee_id")))


# ── 계리평가 보고서 ──────────────────────────────────────────────
# 표지부터 용어정리까지 갖춘 인쇄용 HTML. 브라우저 인쇄 → PDF 저장으로 뽑는다.


def _report_html(request: dict) -> dict[str, Any]:
    from .webreport import REPORT_KINDS, render_html

    if not _LAST_RUN:
        raise ValueError("먼저 산출을 실행하세요. 보고서는 방금 산출한 결과로 만듭니다")
    run = _LAST_RUN["run"]
    remembered = _LAST_RUN.get("request") or {}

    kind = text(request.get("kind")) or "severance"
    if kind not in REPORT_KINDS:
        raise ValueError(f"보고서 종류는 {' / '.join(REPORT_KINDS)} 입니다")

    page = render_html(
        run, kind=kind, client=text(request.get("client")),
        period_start=_as_date(remembered.get("period_start")),
    )

    work = Path(request.get("work", "/work"))
    work.mkdir(parents=True, exist_ok=True)
    word = "퇴직급여" if kind == "severance" else "장기급여"
    stamp = str(run.config.base_date).replace("-", "")
    target = work / f"계리평가보고서_{word}_{stamp}.html"
    target.write_text(page, encoding="utf-8")
    return {
        "path": str(target), "filename": target.name,
        "longterm_available": run.longterm is not None,
    }


# ── 단체 ─────────────────────────────────────────────────────────
# 앱을 열면 가장 먼저 고르는 것. 산출 내역이 이 아래에 쌓인다.


def _client_list(_request: dict) -> dict[str, Any]:
    return {
        "client": clients.current(),
        "clients": [
            {"name": c.name, "memo": c.memo, "runs": c.runs,
             "last_saved": c.last_saved}
            for c in clients.entries()
        ],
    }


def _client_select(request: dict) -> dict[str, Any]:
    clients.select(request.get("name", ""))
    return _run_list({})


def _client_add(request: dict) -> dict[str, Any]:
    """단체를 만들고 곧바로 그 단체로 옮겨 간다."""
    made = clients.create(request.get("name", ""), text(request.get("memo")))
    clients.select(made.name)
    return _run_list({})


def _client_rename(request: dict) -> dict[str, Any]:
    clients.rename(request.get("name", ""), request.get("new_name", ""))
    return _run_list({})


def _client_remove(request: dict) -> dict[str, Any]:
    clients.remove(request.get("name", ""), force=bool(request.get("force")))
    return _run_list({})


def _as_date(token: object) -> _dt.date | None:
    """``2025-12-31`` 같은 날짜 문자열. 비었거나 못 읽으면 ``None``."""
    value = text(token)
    if not value:
        return None
    from .dates import DateParseError, parse_roster_date

    try:
        # 화면에서 오는 값은 ``2025-12-31`` 이라 두 자리 연도 피벗은 쓰이지 않는다.
        parsed = parse_roster_date(value, _dt.date.today().year)
    except DateParseError:
        parsed = None
    if parsed is None:
        raise ValueError(f"날짜를 읽지 못했습니다: {value} (예: 2025-12-31)")
    return parsed


# ── 시험용 난수 명부 ─────────────────────────────────────────────

def _gen_cases(request: dict) -> dict[str, Any]:
    """난수 명부 세 사례를 만들어 zip 하나로 묶는다.

    명부·짝이 되는 기초율·특이사항 안내문이 한 벌로 나온다. 안내문이 없으면
    난수 명부를 열어 봐도 무엇을 시험하려는 자료인지 알 수 없다.
    """
    from .rostergen import write_case_pack

    seed = int(request.get("seed") or 20251231)
    base_date = _as_date(request.get("base_date"))
    work = Path(request.get("work", "/work"))
    folder = work / "시험명부"
    if folder.exists():
        shutil.rmtree(folder)
    made = write_case_pack(folder, seed=seed, base_date=base_date)

    target = work / f"시험명부_{seed}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in made:
            archive.write(path, path.name)

    return {
        "path": str(target), "filename": target.name,
        "size": target.stat().st_size,
        "files": [path.name for path in made],
        "base_date": str(base_date or ""),
        "cases": [
            {
                "key": spec.key, "title": spec.title, "summary": spec.summary,
                "roster": str(folder / f"{spec.title}.xlsx"),
                "assumptions": str(folder / f"{spec.title}_기초율.xlsx"),
                "report": (folder / f"{spec.title}_특이사항.txt").read_text(
                    encoding="utf-8"
                ),
                "force": bool(spec.flags.get("dirty")),
            }
            for spec in CASES
        ],
    }


def _gen_case_register(request: dict) -> dict[str, Any]:
    """만든 시험 명부를 그대로 [저장된 명부]·[가정세트] 목록에 넣는다."""
    from .rostergen import CASES as _CASES

    work = Path(request.get("work", "/work"))
    folder = work / "시험명부"
    wanted = text(request.get("key"))
    made = []
    for spec in _CASES:
        if wanted and spec.key != wanted:
            continue
        roster = folder / f"{spec.title}.xlsx"
        assumptions = folder / f"{spec.title}_기초율.xlsx"
        if not roster.exists():
            raise ValueError("먼저 [시험 명부 만들기] 를 눌러 주세요")
        register(ROSTER_KIND, roster, name=spec.title)
        register(PRESET_KIND, assumptions, name=f"{spec.title}_기초율")
        made.append(spec.title)
    return {"registered": made, **_library_list({})}


def _as_number(token: object) -> float:
    """'20,143,311,276 원' · '4.170% (수익률곡선기법…)' 처럼 서식이 붙은 값에서 숫자만."""
    match = re.search(r"-?[\d,]+(?:\.\d+)?", str(token))
    if match is None:
        return 0.0
    value = float(match.group().replace(",", ""))
    return value / 100.0 if "%" in str(token) else value


def _run_prior(request: dict) -> dict[str, Any]:
    """저장된 산출을 **전기** 로 끌어온다 — 증감분석의 출발점.

    저장할 때 남긴 원래 숫자를 쓰고, 그 이전 판으로 저장돼 숫자가 없으면
    화면 요약 문자열에서 되짚는다. 전기 기초율 경로도 함께 주므로 경험조정과
    가정변경효과를 나눠 계산할 수 있다.
    """
    folder = _run_folder(request)
    meta = _run_meta(folder) or {}
    report = meta.get("report", {})
    values = report.get("values") or {}
    if not values:
        summary = dict(report.get("summary", []))
        values = {
            "base_date": summary.get("산출기준일", ""),
            "dbo": _as_number(summary.get("확정급여채무 (DBO)", 0)),
            "service_cost": _as_number(summary.get("당기근무원가", 0)),
            "discount_rate": _as_number(summary.get("적용 할인율", 0)),
        }
    return {
        "name": meta.get("name", folder.name),
        "values": values,
        "assumptions": str(folder / "기초율.xlsx"),
    }


# ── 보관함(백업) ─────────────────────────────────────────────────
# 브라우저 저장소는 사용자가 방문기록·웹사이트 데이터를 지우면 함께 사라진다.
# 등록 자료와 산출 내역 전체를 파일 하나로 내보내 iCloud Drive 같은 **기기 밖**
# 에 두게 하고, 그 파일로 어느 기기에서든 되살릴 수 있게 한다.

_BACKUP_STAMP: Final = "마지막백업"
_BACKUP_MARK: Final = "연금계리보관함.json"


def _backup_export(request: dict) -> dict[str, Any]:
    """등록 자료 + 산출 내역 전체를 zip 하나로 묶는다."""
    stamp = text(request.get("stamp")) or _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    settings = read_settings()
    settings[_BACKUP_STAMP] = stamp
    write_settings(settings)

    home = library_dir()
    (home / _BACKUP_MARK).write_text(
        json.dumps({"만든날짜": stamp, "형식": 1}, ensure_ascii=False), encoding="utf-8"
    )

    work = Path(request.get("work", "/work"))
    work.mkdir(parents=True, exist_ok=True)
    target = work / f"연금계리보관함_{stamp[:10].replace('-', '')}.zip"

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(home.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(home).as_posix())

    return {
        "path": str(target), "filename": target.name,
        "size": target.stat().st_size, "stamp": stamp,
    }


def _backup_import(request: dict) -> dict[str, Any]:
    """보관함 파일을 되살린다.

    ``replace`` 면 지금 것을 비우고 통째로 바꾸고, 아니면 같은 이름만 덮어쓰며
    합친다(기본). 다른 기기에서 만든 보관함을 합칠 때 쓰는 쪽이 기본이다.
    """
    source = Path(request["path"])
    if not source.exists():
        raise ValueError("가져올 보관함 파일이 없습니다")

    home = library_dir()
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        if _BACKUP_MARK not in names:
            raise ValueError(
                "이 앱에서 내보낸 보관함 파일이 아닙니다 "
                "(연금계리보관함_YYYYMMDD.zip 을 고르세요)"
            )
        if request.get("replace"):
            for child in home.iterdir():
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        for name in names:
            if name.endswith("/"):
                continue
            # zip 안의 경로가 보관함 밖을 가리키지 않게 막는다.
            target = (home / name).resolve()
            if not target.is_relative_to(home.resolve()):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as payload, target.open("wb") as out:
                shutil.copyfileobj(payload, out)

    result = _library_list({})
    result.update(_run_list({}))
    return result


_OPS = {
    "meta": _meta,
    "state_new": _state_new,
    "state_example": _state_example,
    "state_check": _state_check,
    "state_write": _state_write,
    "state_read": _state_read,
    "standard_state": _standard_state,
    "formula_check": _formula_check,
    "formula_preview": _formula_preview,
    "library_list": _library_list,
    "library_register": _library_register,
    "library_remove": _library_remove,
    "library_pin": _library_pin,
    "curve_grades": _curve_grades,
    "curve_rows": _curve_rows,
    "rates_state": _rates_state,
    "preset_save": _preset_save,
    "preset_state": _preset_state,
    "gen_cases": _gen_cases,
    "gen_case_register": _gen_case_register,
    "roster_scan": _roster_scan,
    "roster_groups": _roster_groups,
    "general_info": _general_info,
    "run": _run,
    "run_save": _run_save,
    "run_list": _run_list,
    "run_restore": _run_restore,
    "run_results": _run_results,
    "run_delete": _run_delete,
    "run_prior": _run_prior,
    "client_list": _client_list,
    "client_select": _client_select,
    "client_add": _client_add,
    "client_rename": _client_rename,
    "client_remove": _client_remove,
    "member_detail": _member_detail,
    "dashboard": _dashboard,
    "report_html": _report_html,
    "library_path": _library_path,
    "backup_export": _backup_export,
    "backup_import": _backup_import,
}
