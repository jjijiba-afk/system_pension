"""탭들이 나눠 갖는 것.

탭 일곱 개가 같은 산출 결과를 본다. 탭마다 결과를 따로 들고 있으면 산출을 다시
돌렸을 때 어떤 탭은 새 숫자를, 어떤 탭은 옛 숫자를 보여 준다 — 화면 두 개를
나란히 놓고 왜 다른지 찾게 되는 종류의 사고다. 그래서 **결과는 여기 한 벌만**
두고, 바뀌면 탭들에게 알린다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["Hub", "Loaded"]


@dataclass(slots=True)
class Loaded:
    """지금 화면이 보고 있는 산출 한 회차."""

    run: Any
    """:class:`pension.pipeline.PensionRun`."""
    options: Any = None
    """돌릴 때 쓴 :class:`pension.pipeline.RunOptions`."""
    output: Path | None = None
    """결과 엑셀 경로. 저장된 산출을 불러온 회차는 없을 수 있다."""
    name: str = ""
    """저장된 산출에서 불러왔으면 그 이름."""

    @property
    def client(self) -> str:
        from .. import clients

        return clients.current()


class Hub:
    """상태 한 벌과, 바뀌었을 때 부를 사람들."""

    def __init__(self) -> None:
        self.loaded: Loaded | None = None
        self._watchers: dict[str, list[Callable[[], None]]] = {}
        self.notes: list[str] = []
        """탭이 서로에게 남기는 짧은 안내. 상태줄이 읽어 간다."""

    # ── 알림 ────────────────────────────────────────────────────
    def watch(self, topic: str, callback: Callable[[], None]) -> None:
        self._watchers.setdefault(topic, []).append(callback)

    def announce(self, topic: str) -> None:
        """``topic`` 을 지켜보는 탭들을 부른다.

        한 탭이 터져도 나머지는 갱신돼야 한다. 안 그러면 산출은 끝났는데
        분석 탭 하나가 실패해서 보고서·내역까지 옛 숫자로 남는다.
        """
        for callback in list(self._watchers.get(topic, ())):
            try:
                callback()
            except Exception as exc:  # pragma: no cover - 화면 갱신 실패는 치명적이지 않다
                self.notes.append(f"{topic} 갱신 실패: {exc}")

    # ── 산출 결과 ───────────────────────────────────────────────
    def publish(self, loaded: Loaded | None) -> None:
        self.loaded = loaded
        self.announce("run")

    @property
    def run(self) -> Any:
        return self.loaded.run if self.loaded else None

    def require_run(self) -> Any:
        run = self.run
        if run is None:
            raise LookupError(
                "먼저 [산출] 탭에서 산출을 실행하거나 [산출 내역] 에서 불러오세요."
            )
        return run

    # ── 단체 ────────────────────────────────────────────────────
    def client(self) -> str:
        from .. import clients

        return clients.current()

    def select_client(self, name: str) -> str:
        from .. import clients

        chosen = clients.select(name)
        self.announce("client")
        return chosen
