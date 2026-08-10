"""단체(고객사)별 산출 내역 관리.

한 사람이 여러 단체를 맡는다. 산출 내역을 한 목록에 통째로 쌓아 두면 전기
산출을 고를 때 남의 회사가 섞인다 — 전기 확정급여채무를 다른 단체 것으로
끌어오면 증감분석이 통째로 틀리는데, 목록에 뜬 이름만 보고는 알아채기
어렵다. 그래서 **단체를 먼저 고르고** 산출 내역은 그 안에 둔다.

금리표·표준률은 결산기가 같으면 어느 단체든 같은 것을 쓰므로 자료실에
공용으로 남긴다. 단체 안에 들어가는 것은 산출 내역뿐이다.

폴더 모양은 이렇다::

    <보관폴더>/산출내역/<단체>/단체.json      ← 이 폴더가 단체라는 표시
                        /<산출명>/meta.json  ← 산출 한 건
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .library import library_dir, read_settings, write_settings
from .normalize import text

__all__ = [
    "DEFAULT_CLIENT", "Client", "create", "current", "entries", "folder",
    "names", "remove", "rename", "root", "safe_name", "select",
]

#: 단체를 하나도 만들지 않은 사람에게 주는 첫 단체.
DEFAULT_CLIENT: Final = "기본 단체"

_CURRENT_KEY: Final = "단체"
_MARK: Final = "단체.json"
_RUN_MARK: Final = "meta.json"


def root() -> Path:
    """산출 내역 전체가 들어가는 폴더."""
    path = library_dir() / "산출내역"
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_name(name: object) -> str:
    """단체명을 폴더 이름으로. 경로 문자만 걷어내고 나머지는 그대로 둔다."""
    cleaned = re.sub(r'[\\/:*?"<>|]', " ", text(name)).strip()
    if not cleaned:
        raise ValueError("단체명을 입력하세요 (예: 1번단체)")
    if cleaned in {".", ".."}:
        raise ValueError(f"쓸 수 없는 단체명입니다: {cleaned}")
    return cleaned


@dataclass(slots=True)
class Client:
    """단체 한 곳."""

    name: str
    path: Path
    memo: str = ""
    runs: int = 0
    """이 단체 안에 저장된 산출 건수."""
    last_saved: str = ""
    """가장 최근 산출을 저장한 시각. 목록을 최근순으로 세우는 데 쓴다."""


def _is_client(path: Path) -> bool:
    return path.is_dir() and (path / _MARK).is_file()


def _is_run(path: Path) -> bool:
    return path.is_dir() and (path / _RUN_MARK).is_file()


def _write_mark(path: Path, memo: str = "") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / _MARK).write_text(
        json.dumps(
            {"이름": path.name, "메모": memo,
             "만든날짜": _dt.date.today().isoformat()},
            ensure_ascii=False, indent=1,
        ),
        encoding="utf-8",
    )


def _memo(path: Path) -> str:
    try:
        return text(json.loads((path / _MARK).read_text(encoding="utf-8")).get("메모"))
    except (OSError, ValueError):
        return ""


def _migrate() -> None:
    """단체를 쓰기 전에 저장한 산출을 기본 단체 안으로 옮긴다.

    예전 판은 ``산출내역/<산출명>`` 에 바로 넣었다. 그 폴더들을 찾아
    ``산출내역/기본 단체/<산출명>`` 으로 내린다. 한 번 옮기고 나면 위층에는
    단체 폴더만 남아 다시 걸리지 않는다.
    """
    base = root()
    legacy = sorted(child for child in base.iterdir() if _is_run(child))
    if not legacy:
        return
    target = base / DEFAULT_CLIENT
    _write_mark(target)
    for old in legacy:
        new = target / old.name
        if new.exists():          # 같은 이름이 이미 있으면 옮기지 않고 둔다.
            continue
        shutil.move(str(old), str(new))


def names() -> list[str]:
    """단체 이름 목록. 하나도 없으면 기본 단체를 만들어 준다."""
    _migrate()
    found = sorted(child.name for child in root().iterdir() if _is_client(child))
    if not found:
        _write_mark(root() / DEFAULT_CLIENT)
        return [DEFAULT_CLIENT]
    return found


def entries() -> list[Client]:
    """단체 목록. 최근에 산출을 저장한 단체가 앞에 온다."""
    from .runs import summaries  # 순환 참조를 피해 늦게 들여온다.

    found = []
    for name in names():
        path = root() / name
        saved = [run.get("saved", "") for run in summaries(name)]
        found.append(Client(
            name=name, path=path, memo=_memo(path),
            runs=len(saved), last_saved=max(saved, default=""),
        ))
    # 최근에 산출한 단체가 위로. 아직 산출이 없는 단체끼리는 이름 차례.
    found.sort(key=lambda c: c.name)
    found.sort(key=lambda c: c.last_saved, reverse=True)
    return found


def folder(name: object = "") -> Path:
    """그 단체의 폴더. 이름을 비우면 지금 고른 단체."""
    chosen = safe_name(name) if text(name) else current()
    path = root() / chosen
    if not _is_client(path):
        _write_mark(path)
    return path


def create(name: object, memo: str = "") -> Client:
    """단체를 새로 만든다. 이미 있으면 그대로 돌려준다."""
    chosen = safe_name(name)
    path = root() / chosen
    if not _is_client(path):
        _write_mark(path, memo)
    return Client(name=chosen, path=path, memo=_memo(path))


def rename(old: object, new: object) -> Client:
    """단체 이름을 바꾼다. 안에 든 산출은 그대로 따라간다."""
    source = root() / safe_name(old)
    target = root() / safe_name(new)
    if not _is_client(source):
        raise ValueError(f"단체 '{safe_name(old)}' 이(가) 없습니다")
    if target == source:
        return Client(name=target.name, path=target, memo=_memo(target))
    if target.exists():
        raise ValueError(f"단체 '{target.name}' 이(가) 이미 있습니다")
    # 옮기고 나면 옛 이름은 목록에 없어 :func:`current` 가 첫 단체로 돌아간다.
    # 보고 있던 단체였는지는 옮기기 **전에** 봐 둬야 한다.
    was_current = current() == source.name
    shutil.move(str(source), str(target))
    _write_mark(target, _memo(target))
    if was_current:
        select(target.name)
    return Client(name=target.name, path=target, memo=_memo(target))


def remove(name: object, *, force: bool = False) -> None:
    """단체를 지운다. 안에 산출이 남아 있으면 ``force`` 를 줘야 지워진다."""
    chosen = safe_name(name)
    path = root() / chosen
    if not _is_client(path):
        raise ValueError(f"단체 '{chosen}' 이(가) 없습니다")
    if len(names()) <= 1:
        raise ValueError("마지막 남은 단체는 지울 수 없습니다")
    kept = sum(1 for child in path.iterdir() if _is_run(child))
    if kept and not force:
        raise ValueError(
            f"'{chosen}' 안에 저장된 산출이 {kept}건 있습니다. "
            "산출까지 함께 지우려면 다시 확인하세요"
        )
    shutil.rmtree(path)
    if current() == chosen:
        select(names()[0])


def current() -> str:
    """지금 고른 단체. 없어졌거나 고른 적이 없으면 첫 단체로 되돌린다."""
    available = names()
    chosen = text(read_settings().get(_CURRENT_KEY))
    if chosen in available:
        return chosen
    return available[0]


def select(name: object) -> str:
    """작업할 단체를 고른다. 없는 이름이면 만들어서 고른다."""
    chosen = create(name).name
    settings = read_settings()
    settings[_CURRENT_KEY] = chosen
    write_settings(settings)
    return chosen
