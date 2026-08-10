"""배포 꾸러미에 개인정보가 실리지 않았는지 확인한다.

빌드가 끝난 폴더를 받아 두 가지를 본다.

* 명부 CSV 같은 자료 파일이 딸려 왔는지
* 실행 파일 **안** 에 명부 원자료가 묻어 있는지 — 기본 명부는 씨앗에서 만들어
  내므로, 원자료가 남아 있다면 어딘가에서 다시 실어 나른 것이다

프로그램을 남에게 건네면 같이 들어 있는 자료도 건네진다. 그래서 사람이
기억해서 확인하는 대신 빌드가 매번 확인한다.

사용::

    python tools/check_release.py dist/연금계리산출

워크플로에 인라인으로 적지 않고 파일로 둔 이유는, 윈도우 잡이 PowerShell 로
도는데 거기에는 bash 의 heredoc 이 없어 파이썬 코드를 끼워 넣을 자리가
마땅치 않기 때문이다. 파일로 두면 시험도 붙는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

# 이 스크립트는 윈도우 러너에서 돈다. 영문 로캘의 콘솔 코드페이지(cp1252)는
# 한글을 표현하지 못해, 아래 메시지를 print 하는 순간 UnicodeEncodeError 로
# 죽는다 — `webapp/build.py` 가 실제로 그렇게 죽었다. 같은 함정이다.
if sys.platform == "win32":  # pragma: no cover - 리눅스 CI 에서는 확인 못 한다
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

#: 실행 파일 안에서 찾을 흔적. 기본 명부 원자료의 파일명 앞부분이다.
#: 바이트로 비교한다 — 실행 파일은 텍스트가 아니라 어떤 인코딩으로도 못 읽는다.
ROSTER_MARK = "기본명부_".encode("utf-8")

#: 딸려 오면 안 되는 자료 파일.
DATA_SUFFIXES = (".csv",)


def problems(pack: Path) -> list[str]:
    """찾은 문제를 모아 돌려준다. 비었으면 깨끗한 것이다."""
    found: list[str] = []

    for path in pack.rglob("*"):
        if path.is_file() and path.suffix.lower() in DATA_SUFFIXES:
            found.append(f"자료 파일이 실렸다: {path.relative_to(pack)}")

    for exe in pack.glob("*.exe"):
        if ROSTER_MARK in exe.read_bytes():
            found.append(f"{exe.name} 안에 명부 원자료가 남아 있다")

    return found


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("사용법: python tools/check_release.py <배포폴더>")
        return 2

    pack = Path(args[0])
    if not pack.is_dir():
        print(f"배포 폴더가 없습니다: {pack}")
        return 2

    found = problems(pack)
    if found:
        for line in found:
            print(f"개인정보 확인 실패 — {line}")
        return 1

    print("확인: 배포 꾸러미에 개인정보 없음")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
