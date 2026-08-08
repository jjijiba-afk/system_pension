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

import json
import tempfile
from pathlib import Path
from typing import Any

from . import assumption_form as form
from .actuarial import FRACTION_MODES, SERVICE_BASES
from .assumptions import BENEFIT_MODES, LONGTERM_TYPES
from .formula import FUNCTIONS, VARIABLES
from .jobgroup import DEFAULT_GROUPS, scan_roster, suggest_group
from .library import (
    CURVE_KIND,
    RATES_KIND,
    entries,
    find_entry,
    read_settings,
    register,
    remove,
    resolve_default,
    write_settings,
)
from .normalize import text
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
            }
            for spec in form.FORM_SHEETS
        ],
        "default_groups": list(DEFAULT_GROUPS),
        "apply_choices": list(form.APPLY_CHOICES),
        "rounding_units": list(form.ROUNDING_UNITS),
        "service_bases": list(SERVICE_BASES),
        "fraction_modes": list(FRACTION_MODES),
        "benefit_modes": list(BENEFIT_MODES),
        "longterm_types": list(LONGTERM_TYPES),
        "grades": [*INVESTMENT_GRADES, "국고채"],
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
    for kind in (CURVE_KIND, RATES_KIND):
        default = resolve_default(kind)
        result[kind] = {
            "entries": [
                {"name": e.name, "registered": str(e.registered or "")}
                for e in entries(kind)
            ],
            "default": default.name if default else "",
            "pinned": settings.get(kind, ""),
        }
    return {"library": result}


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
    """자료요청서 '1)일반사항' 6번에서 지급규정 초안을 읽는다."""
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
    return {
        "values": values,
        "evidence": dict(draft.evidence),
        "unread": list(draft.unread),
    }


# ── 산출 ─────────────────────────────────────────────────────────

def _run(request: dict) -> dict[str, Any]:
    """산출을 실행하고 화면 요약을 돌려준다. 결과 파일은 ``/work`` 아래에 남는다."""
    from .errors import PensionDataError, Severity
    from .members import write_member_export
    from .pipeline import PriorPeriod, RunOptions, run_valuation
    from .report import write_report

    work = Path(request.get("work", "/work"))
    out = work / "산출결과.xlsx"
    try:
        run = run_valuation(RunOptions(
            roster_path=Path(request["roster"]),
            assumptions_path=Path(request["assumptions"]),
            output_path=out,
            include_sensitivity=bool(request.get("sensitivity", True)),
            include_longterm=bool(request.get("longterm", True)),
            allow_errors=bool(request.get("force", False)),
            prior=PriorPeriod(
                dbo=float(request.get("prior_dbo") or 0),
                discount_rate=float(request.get("prior_rate") or 0),
            ),
        ))
    except PensionDataError as exc:
        errors = [str(i) for i in exc.issues if i.severity is Severity.ERROR]
        return {"run": False, "errors": errors[:40], "more": max(0, len(errors) - 40)}

    write_report(run, out)
    write_member_export(run, work / "개인별결과.xlsx")

    valuation = run.valuation
    if run.assumptions.discount.flat is None:
        rate = f"{valuation.single_discount_rate():.3%} (수익률곡선기법 단일할인율)"
    else:
        rate = f"{run.assumptions.discount.level_rate:.3%}"
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
        summary.append(
            ("보험수리적손익", f"{run.rollforward.actuarial_gain_loss:,.0f} 원")
        )

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
    }


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
    "roster_scan": _roster_scan,
    "roster_groups": _roster_groups,
    "general_info": _general_info,
    "run": _run,
}
