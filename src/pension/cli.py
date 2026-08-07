"""명령행 인터페이스.

인자 없이 실행하면 GUI 가 뜬다(EXE 를 더블클릭한 경우). 하위 명령을 주면
배치 실행이 된다::

    pension calc 명부.xlsm 기초율.xlsx -o 산출결과.xlsx
    pension check 명부.xlsm                 # 검증만
    pension template 기초율.xlsx            # 기초율 양식 생성
    pension upload 명부.xlsm -o 업로드.xlsx  # 업로드 명부만 생성
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path

from . import __version__
from .errors import PensionDataError, Severity

__all__ = ["main"]


def force_utf8_output() -> None:
    """표준 출력을 UTF-8 로 맞춘다.

    한국어 윈도우의 명령프롬프트는 기본 코드페이지가 949(CP949)이고, GitHub
    Actions 같은 영문 환경은 1252 다. 어느 쪽이든 파이썬은 그 코드페이지로
    stdout 을 열기 때문에, 한글이 섞인 메시지를 ``print`` 하면
    ``UnicodeEncodeError: 'charmap' codec can't encode characters`` 로 죽는다.
    산출은 다 끝내 놓고 결과를 찍다가 실패하는 셈이라 특히 나쁘다.

    ``errors="replace"`` 를 함께 주어, 콘솔이 정말 표현하지 못하는 글자가 있어도
    프로그램이 멈추지는 않게 한다.

    창 모드로 빌드된 실행 파일(``console=False``)에서는 ``sys.stdout`` 이
    ``None`` 일 수 있으므로 존재 여부를 확인한다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        # 이미 닫혔거나 재설정할 수 없는 스트림이면 그냥 둔다. 출력 인코딩
        # 때문에 산출 자체를 막을 이유는 없다.
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")


def _percent(value: str) -> float:
    token = value.strip().rstrip("%")
    number = float(token)
    return number / 100.0 if value.strip().endswith("%") or number > 1.0 else number


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pension",
        description="연금계리 산출 시스템 — K-IFRS 1019 확정급여채무(PUC)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    calc = sub.add_parser("calc", help="확정급여채무 산출")
    calc.add_argument("roster", type=Path, help="명부 워크북 (.xlsm/.xlsx)")
    calc.add_argument("assumptions", type=Path, help="기초율 워크북")
    calc.add_argument("-o", "--output", type=Path, required=True, help="결과 엑셀 경로")
    calc.add_argument("--no-sensitivity", action="store_true", help="민감도분석 생략")
    calc.add_argument("--no-longterm", action="store_true", help="장기종업원급여 생략")
    calc.add_argument("--force", action="store_true", help="검증 오류가 있어도 산출")
    calc.add_argument(
        "--members", type=Path, metavar="경로",
        help="개인별 결과를 별도 엑셀로도 저장 (생략하면 결과 파일 안 시트로만)",
    )
    calc.add_argument("--prior-dbo", type=float, default=0.0, help="전기말 확정급여채무")
    calc.add_argument("--prior-rate", type=_percent, default=0.0, help="전기말 할인율 (예: 4.5%%)")
    calc.add_argument("--prior-assumptions", type=Path, help="전기 기초율 워크북")

    check = sub.add_parser("check", help="명부 검증만 실행")
    check.add_argument("roster", type=Path)
    check.add_argument("-o", "--output", type=Path, help="검증 결과 CSV 경로")

    template = sub.add_parser("template", help="기초율 양식 생성")
    template.add_argument("output", type=Path)
    template.add_argument("--roster", type=Path, help="직군명을 가져올 명부 워크북")

    upload = sub.add_parser("upload", help="업로드 명부만 생성")
    upload.add_argument("roster", type=Path)
    upload.add_argument("-o", "--output", type=Path, required=True)
    upload.add_argument("--force", action="store_true", help="검증 오류가 있어도 생성")

    members = sub.add_parser("members", help="개인별 산출 결과만 엑셀로 내보내기")
    members.add_argument("roster", type=Path, help="명부 워크북")
    members.add_argument("assumptions", type=Path, help="기초율 워크북")
    members.add_argument("-o", "--output", type=Path, required=True)
    members.add_argument("--force", action="store_true", help="검증 오류가 있어도 산출")

    sub.add_parser("gui", help="GUI 실행")

    editor = sub.add_parser("assumptions", help="산출 가정 입력 화면 실행")
    editor.add_argument(
        "--roster", type=Path, help="직군명을 미리 채워 올 명부 워크북"
    )
    editor.add_argument(
        "--open", type=Path, dest="existing", help="열어 둘 기초율 파일"
    )
    return parser


def _print_issue_list(issues: list, limit: int = 30) -> None:
    """이슈 목록을 오류 먼저, 최대 ``limit`` 건까지 출력한다."""
    errors = [i for i in issues if i.severity is Severity.ERROR]
    warnings = [i for i in issues if i.severity is Severity.WARNING]
    for issue in (errors + warnings)[:limit]:
        print(f"  {issue}")
    total = len(errors) + len(warnings)
    if total > limit:
        print(f"  … 외 {total - limit}건")
    print(f"\n오류 {len(errors)}건 / 경고 {len(warnings)}건")


def _print_issues(log, limit: int = 30) -> None:
    _print_issue_list(list(log), limit)


def _cmd_calc(args: argparse.Namespace) -> int:
    from .pipeline import PriorPeriod, RunOptions, run_valuation
    from .report import write_report
    from .sensitivity import DEFAULT_SHOCKS

    options = RunOptions(
        roster_path=args.roster,
        assumptions_path=args.assumptions,
        output_path=args.output,
        include_sensitivity=not args.no_sensitivity,
        include_longterm=not args.no_longterm,
        shocks=DEFAULT_SHOCKS,
        allow_errors=args.force,
        prior=PriorPeriod(
            dbo=args.prior_dbo,
            discount_rate=args.prior_rate,
            assumptions_path=str(args.prior_assumptions) if args.prior_assumptions else "",
        ),
    )

    def progress(message: str, fraction: float) -> None:
        print(f"[{fraction * 100:3.0f}%] {message}")

    try:
        run = run_valuation(options, progress)
    except PensionDataError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        _print_issue_list(exc.issues)
        print("\n--force 를 주면 오류를 남긴 채 산출할 수 있습니다.", file=sys.stderr)
        return 2

    write_report(run, args.output)

    v = run.valuation
    print("\n── 산출 결과 " + "─" * 44)
    print(f"  산출기준일     {run.config.base_date}")
    print(f"  적용 할인율    {run.assumptions.discount.level_rate:.3%}")
    print(f"  산출대상 인원  {v.headcount:,}명")
    print(f"  확정급여채무   {v.dbo:>18,.0f} 원")
    print(f"  당기근무원가   {v.service_cost:>18,.0f} 원")
    print(f"  이자원가(차기) {v.interest_cost:>18,.0f} 원")
    print(f"  듀레이션       {v.duration:>18,.1f} 년")
    if run.longterm is not None:
        print(f"  장기급여채무   {run.longterm.dbo:>18,.0f} 원")
    if run.rollforward is not None:
        print(f"  보험수리적손익 {run.rollforward.actuarial_gain_loss:>18,.0f} 원")
    print(f"  명부 검증      오류 {len(run.issues.errors)}건 / 경고 {len(run.issues.warnings)}건")
    print(f"\n결과 파일: {args.output}")
    if args.members:
        from .members import write_member_export

        write_member_export(run, args.members)
        print(f"개인별 결과: {args.members}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    import csv

    import openpyxl

    from .config import read_config
    from .errors import IssueLog
    from .readers import read_roster
    from .validation import validate_roster

    wb = openpyxl.load_workbook(args.roster, data_only=True)
    try:
        config = read_config(wb)
        log = IssueLog()
        roster = read_roster(wb, config, log)
    finally:
        wb.close()
    validate_roster(roster, config, log)

    print(f"산출기준일 {config.base_date} · 재직 {len(roster.active):,}명 / 퇴직 {len(roster.retired):,}명\n")
    _print_issues(log)

    if args.output:
        with open(args.output, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(["심각도", "시트", "행", "열", "순번", "사번", "코드", "내용", "값"])
            for issue in log:
                writer.writerow([
                    issue.severity.value, issue.sheet, issue.row, issue.column,
                    issue.seq, issue.employee_id, issue.code, issue.message, issue.value,
                ])
        print(f"\n검증 결과: {args.output}")

    return 1 if log.has_errors() else 0


def _job_groups_from_roster(roster: Path | None) -> list[str]:
    """명부 ``Input`` 시트가 참조하는 규정명. 읽지 못하면 빈 목록."""
    if not roster or not roster.exists():
        return []

    import openpyxl

    from .config import read_config

    try:
        wb = openpyxl.load_workbook(roster, data_only=True)
    except (OSError, ValueError, KeyError):
        return []
    try:
        return read_config(wb).referenced_rule_names()
    except (ValueError, KeyError):
        return []
    finally:
        wb.close()


def _cmd_template(args: argparse.Namespace) -> int:
    from .assumptions import write_template

    groups = _job_groups_from_roster(args.roster)
    path = write_template(args.output, job_groups=groups)
    print(f"기초율 양식을 만들었습니다: {path}")
    if groups:
        print(f"  규정명 열: {', '.join(groups)}")
    return 0


def _cmd_upload(args: argparse.Namespace) -> int:
    import openpyxl

    from .config import read_config
    from .errors import IssueLog
    from .readers import read_roster
    from .upload import ACTIVE_UPLOAD_HEADERS, RETIRED_UPLOAD_HEADERS, build_upload
    from .validation import validate_roster

    wb = openpyxl.load_workbook(args.roster, data_only=True)
    try:
        config = read_config(wb)
        log = IssueLog()
        roster = read_roster(wb, config, log)
    finally:
        wb.close()
    validate_roster(roster, config, log)

    if log.has_errors() and not args.force:
        print(f"검증 오류 {len(log.errors)}건으로 중단했습니다.\n", file=sys.stderr)
        _print_issues(log)
        return 2

    active, retired = build_upload(roster, config)
    out = openpyxl.Workbook()
    del out["Sheet"]
    for title, headers, rows in (
        ("UpLoad_Jae", ACTIVE_UPLOAD_HEADERS, active),
        ("UpLoad_Toi", RETIRED_UPLOAD_HEADERS, retired),
    ):
        ws = out.create_sheet(title)
        ws.append(list(headers))
        for row in rows:
            ws.append(row)
        ws.freeze_panes = "A2"
    out.save(args.output)

    print(f"재직자 {len(active):,}명 / 퇴직자 {len(retired):,}명 → {args.output}")
    if log.warnings:
        print(f"경고 {len(log.warnings)}건이 있습니다. 'pension check' 로 확인하세요.")
    return 0


def _cmd_members(args: argparse.Namespace) -> int:
    """산출한 뒤 개인별 결과만 별도 엑셀로 내보낸다."""
    from .members import build_member_rows, write_member_export
    from .pipeline import RunOptions, run_valuation

    options = RunOptions(
        roster_path=args.roster,
        assumptions_path=args.assumptions,
        output_path=args.output,
        include_sensitivity=False,
        include_longterm=True,
        allow_errors=args.force,
    )

    def progress(message: str, fraction: float) -> None:
        print(f"[{fraction * 100:3.0f}%] {message}")

    try:
        run = run_valuation(options, progress)
    except PensionDataError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        _print_issue_list(exc.issues)
        print("\n--force 를 주면 오류를 남긴 채 산출할 수 있습니다.", file=sys.stderr)
        return 2

    write_member_export(run, args.output)
    rows = build_member_rows(run)
    included = [r for r in rows if not r.excluded_reason]
    print(f"\n개인별 결과 {len(rows):,}명 (산출대상 {len(included):,}명)")
    print(f"  확정급여채무 합계 {sum(r.dbo for r in rows):>18,.0f} 원")
    print(f"  채무 합계       {sum(r.total_dbo for r in rows):>18,.0f} 원")
    print(f"\n파일: {args.output}")
    return 0


def _cmd_assumptions(args: argparse.Namespace) -> int:
    """산출 가정 입력 화면을 띄운다."""
    import tkinter as tk

    from .editor import AssumptionsEditor

    groups = None
    if args.roster:
        groups = _job_groups_from_roster(args.roster) or None

    root = tk.Tk()
    root.withdraw()
    window = AssumptionsEditor(root, job_groups=groups, on_close=lambda _e: root.destroy())
    if args.existing:
        try:
            window.load_workbook(args.existing)
            window.path = args.existing
            window.status.configure(text=f"불러왔습니다: {args.existing.name}")
        except (OSError, ValueError) as exc:
            print(f"기초율 파일을 읽지 못했습니다: {exc}")
    root.mainloop()
    return 0


def _cmd_gui(_args: argparse.Namespace) -> int:
    from .gui import main as gui_main

    return gui_main()


def main(argv: list[str] | None = None) -> int:
    """진입점. 인자가 없으면 GUI 를 띄운다."""
    force_utf8_output()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return _cmd_gui(argparse.Namespace())

    parser = _build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "calc": _cmd_calc,
        "check": _cmd_check,
        "template": _cmd_template,
        "assumptions": _cmd_assumptions,
        "members": _cmd_members,
        "upload": _cmd_upload,
        "gui": _cmd_gui,
    }
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    try:
        return handler(args)
    except FileNotFoundError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
