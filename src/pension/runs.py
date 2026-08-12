"""저장된 산출 내역.

한 단체 폴더 안에 산출 한 건이 폴더 하나로 앉는다::

    <보관폴더>/산출내역/<단체>/<산출명>/명부.xlsx
                                      /기초율.xlsx
                                      /산출결과.xlsx      (있으면)
                                      /개인별결과.xlsx    (있으면)
                                      /meta.json         ← 이 폴더가 산출이라는 표시

이 모양을 아는 곳은 여기 하나다. 웹앱(:mod:`pension.webui`)과 PC 본 화면이
같은 폴더를 읽고 쓰는데, 각자 경로를 조립하면 한쪽에서 저장한 것을 다른 쪽이
못 찾는다 — 아이패드에서 저장하고 PC 에서 여는 것이 이 프로그램의 쓰임새라
그건 곧 자료를 잃는 것과 같다.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import shutil
from pathlib import Path
from typing import Any, Final

from . import clients
from .normalize import text

__all__ = [
    "META", "ROSTER_SUFFIXES", "delete", "folder_of", "meta_of", "prior_link",
    "read_meta", "restore", "roster_of", "safe_name", "save", "saved_names",
    "SCHEMA", "schema_gap",
    "summaries",
]

META: Final = "meta.json"

SCHEMA: Final = 2
"""저장본 세대. **저장본이 뜻하는 바가 달라질 때 올린다.**

명부 서식이나 엔진 규칙이 바뀌면, 옛 저장본과 새 산출을 맞대었을 때 나오는
차이가 자료가 달라져서인지 프로그램이 달라져서인지 가릴 수 없다. 전기 대비
검증이 이 저장본을 쓰므로 그 차이가 그대로 결산 판단으로 넘어간다.

세대를 적어 두면 적어도 **물어볼 수는 있다** — 다르면 화면이 알려 준다.
2: 명부에서 임직원구분·가산/차감 근속연수를 빼고 DB비율·잔여계약기간을 더한 판.
"""
ROSTER_SUFFIXES: Final = (".xlsx", ".xlsm", ".xls")
_RESULTS: Final = ("산출결과.xlsx", "개인별결과.xlsx")


def safe_name(name: object) -> str:
    """산출명을 폴더 이름으로. 경로 문자만 걷어내고 나머지는 그대로 둔다."""
    cleaned = re.sub(r'[\\/:*?"<>|]', " ", text(name)).strip()
    if not cleaned:
        raise ValueError("산출명을 입력하세요 (예: 2412 1번단체)")
    return cleaned


def _engine_version() -> str:
    from . import __version__

    return __version__


def schema_gap(meta: dict[str, Any] | None) -> str:
    """저장본 세대가 지금과 다르면 그 사연. 같으면 빈 문자열.

    세대가 없는 저장본은 세대를 적기 시작하기 **전** 에 저장된 것이다.
    """
    if not meta:
        return ""
    found = meta.get("schema")
    if found == SCHEMA:
        return ""
    if found is None:
        return ("이 저장본에는 세대 표시가 없습니다. 명부 서식이 바뀌기 전에 "
                "저장된 것이라, 지금 명부와 맞대면 자료가 달라진 것인지 서식이 "
                "달라진 것인지 가릴 수 없습니다")
    return (f"저장본 세대가 {found} 이고 지금은 {SCHEMA} 입니다. 그 사이에 명부 "
            "서식이나 산출 규칙이 바뀌었으므로, 차이가 자료 때문인지 프로그램 "
            "때문인지 가릴 수 없습니다")


def read_meta(folder: Path) -> dict[str, Any] | None:
    try:
        return json.loads((folder / META).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def roster_of(folder: Path) -> Path | None:
    """저장본에 들어 있는 명부. ``.xls`` 로 받은 명부는 그 서식 그대로 남는다."""
    for suffix in ROSTER_SUFFIXES:
        candidate = folder / f"명부{suffix}"
        if candidate.exists():
            return candidate
    return None


def folder_of(name: object, client: object = "") -> Path:
    """저장된 산출의 폴더. 없으면 :class:`ValueError`."""
    folder = clients.folder(client) / safe_name(name)
    if not folder.is_dir() or read_meta(folder) is None:
        raise ValueError(f"저장된 산출 '{text(name)}' 이(가) 없습니다")
    return folder


def meta_of(name: object, client: object = "") -> dict[str, Any]:
    return read_meta(folder_of(name, client)) or {}


def save(name: object, roster: Path | str, assumptions: Path | str, *,
         results: dict[str, Path | str] | None = None,
         report: dict[str, Any] | None = None,
         options: dict[str, Any] | None = None,
         roster_name: str = "",
         saved: str = "",
         client: object = "") -> Path:
    """산출 한 건을 이름 붙여 보관한다. 같은 이름이 있으면 덮어쓴다."""
    clean = safe_name(name)
    roster = Path(roster)
    assumptions = Path(assumptions)
    if not roster.exists() or not assumptions.exists():
        raise ValueError("저장할 명부·기초율이 없습니다. 먼저 산출을 실행하세요")

    folder = clients.folder(client) / clean
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)

    shutil.copy2(roster, folder / f"명부{roster.suffix.lower()}")
    shutil.copy2(assumptions, folder / "기초율.xlsx")
    for result_name, source in (results or {}).items():
        source = Path(source)
        if source.exists():
            shutil.copy2(source, folder / result_name)

    payload = {
        "schema": SCHEMA,
        "engine": _engine_version(),
        "name": clean,
        "saved": text(saved) or _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "roster_name": text(roster_name) or roster.name,
        "report": report or {},
        "options": options or {},
    }
    (folder / META).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    return folder


def summaries(client: object = "") -> list[dict[str, Any]]:
    """한 단체의 산출 목록. 최근 저장한 것이 앞에 온다."""
    found = []
    for folder in clients.folder(client).iterdir():
        if not folder.is_dir():
            continue
        meta = read_meta(folder)
        if meta is None:
            continue
        summary = dict(meta.get("report", {}).get("summary", []))
        found.append({
            "name": meta.get("name", folder.name),
            "saved": meta.get("saved", ""),
            "roster_name": meta.get("roster_name", ""),
            "base_date": summary.get("산출기준일", ""),
            "headcount": summary.get("산출대상 인원", ""),
            "dbo": summary.get("확정급여채무 (DBO)", ""),
            "has_results": (folder / _RESULTS[0]).exists(),
        })
    found.sort(key=lambda run: run["saved"], reverse=True)
    return found


def saved_names(client: object = "") -> list[str]:
    return [run["name"] for run in summaries(client)]


def delete(name: object, client: object = "") -> None:
    shutil.rmtree(folder_of(name, client))


def restore(name: object, client: object = "") -> dict[str, Any]:
    """저장본의 입력 파일 경로. 원본을 그대로 가리키므로 **고치면 안 된다**."""
    folder = folder_of(name, client)
    roster = roster_of(folder)
    if roster is None:
        raise ValueError("저장본에 명부가 없습니다")
    return {
        "meta": read_meta(folder) or {},
        "folder": folder,
        "roster": roster,
        "assumptions": folder / "기초율.xlsx",
        "results": {result: folder / result for result in _RESULTS
                    if (folder / result).exists()},
    }


def _as_number(token: object) -> float:
    """'20,000,000,000 원' · '4.170% (수익률곡선기법…)' 처럼 서식이 붙은 값에서 숫자만."""
    match = re.search(r"-?[\d,]+(?:\.\d+)?", str(token))
    if match is None:
        return 0.0
    value = float(match.group().replace(",", ""))
    return value / 100.0 if "%" in str(token) else value


def values_of(meta: dict[str, Any]) -> dict[str, Any]:
    """저장할 때 남긴 원래 숫자. 그 이전 판으로 저장돼 없으면 요약에서 되짚는다."""
    report = meta.get("report", {})
    values = report.get("values") or {}
    if values:
        return dict(values)
    summary = dict(report.get("summary", []))
    return {
        "base_date": summary.get("산출기준일", ""),
        "dbo": _as_number(summary.get("확정급여채무 (DBO)", 0)),
        "service_cost": _as_number(summary.get("당기근무원가", 0)),
        "discount_rate": _as_number(summary.get("적용 할인율", 0)),
    }


def prior_link(name: object, client: object = "") -> dict[str, Any]:
    """저장된 산출을 **전기** 로 끌어온다 — 증감분석의 출발점.

    숫자만이 아니라 전기 기초율 파일과 전기 명부까지 함께 준다. 기초율이 있어야
    보험수리적손익이 경험조정과 가정변경효과로 나뉘고, 명부가 있어야 산출 전에
    맞대어 볼 수 있다.
    """
    folder = folder_of(name, client)
    meta = read_meta(folder) or {}
    values = values_of(meta)
    assumptions = folder / "기초율.xlsx"
    return {
        "name": meta.get("name", folder.name),
        "values": values,
        "dbo": float(values.get("dbo") or 0.0),
        "service_cost": float(values.get("service_cost") or 0.0),
        "rate": float(values.get("discount_rate") or 0.0),
        "longterm_dbo": float(values.get("longterm_dbo") or 0.0),
        "base_date": values.get("base_date", ""),
        "assumptions": assumptions if assumptions.exists() else None,
        "roster": roster_of(folder),
    }


def read_roster_only(path: Path | str):
    """명부만 읽는다. 검증은 하지 않는다 — 두 명부를 맞대어 볼 때 쓴다."""
    from .config import read_config
    from .errors import IssueLog
    from .readers import read_roster
    from .workbook import open_workbook

    book = open_workbook(path)
    try:
        return read_roster(book, read_config(book), IssueLog())
    finally:
        book.close()
