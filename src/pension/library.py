"""시스템에 등록해 두고 여러 단체에 재사용하는 자료.

계리 담당자는 한 결산기에 여러 단체를 산출한다. 그때 **금리표는 모든 단체가
똑같이 쓴다** — 결산일이 같으면 우량회사채 수익률도 같기 때문이다. 표준률도
마찬가지로 단체마다 다시 만들 이유가 없다.

그런데 지금까지는 단체마다 파일을 다시 찾아 지정해야 했다. 스무 단체를 산출하면
같은 금리표를 스무 번 고르는 셈이고, 그중 한 번이라도 다른 파일을 집으면 그
단체만 할인율이 달라진다. 눈에 띄지도 않는다.

그래서 **한 번 등록해 두면 이후 모든 산출에서 목록으로 고를 수 있게** 한다.
고른 뒤 값을 고치는 것은 얼마든지 가능하다 — 등록된 것은 출발점이지 확정이
아니다.

저장 위치
---------
사용자 폴더 아래에 둔다(``%APPDATA%\\연금계리산출`` 또는 ``~/.local/share/pension``).
실행 파일 옆에 두면 ``Program Files`` 처럼 쓰기 권한이 없는 곳에 설치했을 때
등록이 통째로 실패한다. ``PENSION_HOME`` 환경변수로 바꿀 수 있다.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .normalize import text

__all__ = [
    "CURVE_KIND",
    "RATES_KIND",
    "ROSTER_KIND",
    "LibraryEntry",
    "entries",
    "find_entry",
    "library_dir",
    "read_settings",
    "register",
    "remove",
    "resolve_default",
    "write_settings",
]

CURVE_KIND: Final = "금리표"
RATES_KIND: Final = "표준률"
ROSTER_KIND: Final = "명부"
"""올렸던 명부. 같은 단체를 다음 결산에 다시 산출할 때 그대로 꺼내 쓴다."""

_KINDS: Final = (CURVE_KIND, RATES_KIND, ROSTER_KIND)
_SETTINGS: Final = "설정.json"

#: 종류별로 받아 두는 확장자. 명부는 예전 ``.xls`` 로 오는 일이 흔하다.
_SUFFIXES: Final[dict[str, tuple[str, ...]]] = {
    CURVE_KIND: (".xlsx", ".xlsm"),
    RATES_KIND: (".xlsx", ".xlsm"),
    ROSTER_KIND: (".xlsx", ".xlsm", ".xls"),
}


def library_dir() -> Path:
    """등록 자료를 두는 폴더. 없으면 만든다."""
    override = os.environ.get("PENSION_HOME")
    if override:
        base = Path(override)
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home())) / "연금계리산출"
    else:
        base = Path.home() / ".local" / "share" / "pension"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _kind_dir(kind: str) -> Path:
    if kind not in _KINDS:
        raise ValueError(f"등록 종류는 {' 또는 '.join(_KINDS)} 여야 합니다: {kind}")
    path = library_dir() / kind
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(slots=True)
class LibraryEntry:
    """등록된 자료 한 건."""

    kind: str
    name: str
    """목록에 보일 이름. 파일명에서 확장자를 뗀 것이 기본이다."""
    path: Path
    registered: _dt.date | None = None

    @property
    def label(self) -> str:
        stamp = f" ({self.registered})" if self.registered else ""
        return f"{self.name}{stamp}"


def entries(kind: str) -> list[LibraryEntry]:
    """등록된 자료 목록. 최근 등록한 것이 앞에 온다."""
    allowed = _SUFFIXES[kind]
    found = [
        LibraryEntry(
            kind=kind,
            name=path.stem,
            path=path,
            registered=_dt.date.fromtimestamp(path.stat().st_mtime),
        )
        for path in _kind_dir(kind).iterdir()
        if path.is_file() and path.suffix.lower() in allowed
    ]
    found.sort(key=lambda e: e.path.stat().st_mtime, reverse=True)
    return found


def find_entry(kind: str, name: str) -> LibraryEntry | None:
    """이름으로 찾는다. 등록 당시 이름과 정확히 같아야 한다."""
    wanted = text(name)
    return next((e for e in entries(kind) if e.name == wanted), None) if wanted else None


def register(kind: str, source: str | Path, *, name: str = "") -> LibraryEntry:
    """파일을 등록 폴더로 **복사** 한다.

    원본을 참조만 하면 담당자가 그 파일을 옮기거나 지웠을 때 이후 산출이 조용히
    실패한다. 복사해 두면 등록 시점의 자료가 그대로 남아, 나중에 "그때 무슨
    금리표를 썼나" 를 되짚을 수 있다.
    """
    folder = _kind_dir(kind)   # 종류를 먼저 확인한다
    source = Path(source)
    if not source.exists():
        raise FileNotFoundError(f"등록할 파일을 찾을 수 없습니다: {source}")

    label = text(name) or source.stem
    # 파일명으로 쓸 수 없는 글자를 걷어낸다.
    for bad in '\\/:*?"<>|':
        label = label.replace(bad, "_")
    if not label:
        raise ValueError("등록 이름이 비어 있습니다")

    # 확장자는 원본 것을 지킨다. 명부는 예전 ``.xls`` 가 흔한데 ``.xlsx`` 로
    # 이름만 바꿔 두면 여는 쪽이 서식을 잘못 짚어 읽지 못한다.
    suffix = source.suffix.lower()
    if suffix not in _SUFFIXES[kind]:
        suffix = _SUFFIXES[kind][0]

    # 같은 이름을 다른 확장자로 다시 등록하면 둘 다 남아 목록에 두 번 뜬다.
    for stale in folder.glob(f"{label}.*"):
        if stale.is_file():
            stale.unlink()

    target = folder / f"{label}{suffix}"
    shutil.copy2(source, target)
    return LibraryEntry(
        kind=kind, name=label, path=target,
        registered=_dt.date.fromtimestamp(target.stat().st_mtime),
    )


def remove(kind: str, name: str) -> bool:
    """등록을 지운다. 없으면 거짓."""
    entry = find_entry(kind, name)
    if entry is None:
        return False
    entry.path.unlink()
    return True


# ── 기본 선택 ────────────────────────────────────────────────────

def _settings_path() -> Path:
    return library_dir() / _SETTINGS


def read_settings() -> dict[str, str]:
    """기본으로 쓸 등록 이름 등. 파일이 깨졌으면 빈 설정으로 본다."""
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {str(k): str(v) for k, v in loaded.items()} if isinstance(loaded, dict) else {}


def write_settings(values: dict[str, str]) -> Path:
    path = _settings_path()
    path.write_text(
        json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def resolve_default(kind: str) -> LibraryEntry | None:
    """기본으로 지정된 자료. 지정이 없으면 가장 최근에 등록한 것.

    담당자가 결산기마다 금리표를 새로 등록하므로, 지정을 따로 안 해도 최신
    것이 잡히는 편이 실수가 적다.
    """
    settings = read_settings()
    chosen = find_entry(kind, settings.get(kind, ""))
    if chosen is not None:
        return chosen
    found = entries(kind)
    return found[0] if found else None
