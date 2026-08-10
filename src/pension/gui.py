"""담당자용 GUI.

파이썬 표준 라이브러리의 tkinter 만 쓴다. 추가 설치 없이 EXE 한 개로 돌아야
하기 때문이다.

화면은 위에서 아래로 읽히도록 배치했다. 파일 세 칸을 채우고 → 옵션을 확인하고
→ [산출 실행] 을 누르면 → 아래 로그에 진행상황과 결과 요약이 쌓인다.
"""

from __future__ import annotations

import contextlib
import queue
import threading
import traceback
from pathlib import Path
from typing import Any

try:  # pragma: no cover - 화면 없는 환경에서 임포트만 해도 죽지 않게 한다.
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "이 기능은 tkinter 가 필요합니다. Windows 공식 파이썬에는 기본 포함되어 있고, "
        "리눅스에서는 python3-tk 패키지를 설치하세요."
    ) from exc

from . import hidpi
from .assumptions import write_template
from .errors import PensionDataError
from .pipeline import PensionRun, PriorPeriod, RunOptions, run_valuation
from .report import write_report

__all__ = ["PensionApp", "main"]

APP_TITLE = "연금계리 산출 시스템"
APP_VERSION = "1.0"

_BG = "#F4F6FA"
_ACCENT = "#1F3864"
_MUTED = "#5B6478"

_EXCEL_TYPES = [("엑셀 파일", "*.xlsx *.xlsm"), ("모든 파일", "*.*")]


class PensionApp(tk.Tk):
    """메인 창."""

    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_TITLE}  v{APP_VERSION}")
        # 배율은 창을 만든 직후에 먹인다. 크기·최소크기는 픽셀이라 함께 키워야
        # 200% 화면에서 절반 크기로 뜨지 않는다.
        self.scale = hidpi.apply(self)
        self.geometry(hidpi.scale_geometry(self, "900x840"))
        self.minsize(hidpi.px(self, 860), hidpi.px(self, 780))
        self.configure(bg=_BG)

        self._messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._app_server: Any = None
        self._app_url: str = ""
        """전체 기능 화면의 주소. 한 번 띄운 뒤에는 같은 주소를 다시 연다."""

        self.roster_path = tk.StringVar()
        self.assumptions_path = tk.StringVar()
        self.output_path = tk.StringVar()
        self.include_sensitivity = tk.BooleanVar(value=True)
        self.include_longterm = tk.BooleanVar(value=True)
        self.export_members = tk.BooleanVar(value=False)
        self.allow_errors = tk.BooleanVar(value=False)
        self.prior_dbo = tk.StringVar()
        self.prior_rate = tk.StringVar()
        self.prior_assumptions = tk.StringVar()
        self.status = tk.StringVar(value="명부와 기초율 파일을 선택하세요.")

        self._build_styles()
        self._build_layout()
        self.after(120, self._drain_messages)

    # ── 화면 구성 ────────────────────────────────────────────────

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        with contextlib.suppress(tk.TclError):
            # clam 이 없는 환경(구형 Tk)에서는 기본 테마를 그대로 쓴다.
            style.theme_use("clam")
        style.configure("TFrame", background=_BG)
        style.configure("TLabelframe", background=_BG, borderwidth=1, relief="solid")
        style.configure(
            "TLabelframe.Label", background=_BG, foreground=_ACCENT,
            font=("Malgun Gothic", 10, "bold"),
        )
        style.configure("TLabel", background=_BG, font=("Malgun Gothic", 9))
        style.configure("Hint.TLabel", background=_BG, foreground=_MUTED,
                        font=("Malgun Gothic", 8))
        style.configure("Title.TLabel", background=_BG, foreground=_ACCENT,
                        font=("Malgun Gothic", 15, "bold"))
        style.configure("TCheckbutton", background=_BG, font=("Malgun Gothic", 9))
        style.configure("TButton", font=("Malgun Gothic", 9), padding=(10, 4))
        style.configure(
            "Run.TButton", font=("Malgun Gothic", 11, "bold"), padding=(20, 9),
            background=_ACCENT, foreground="#FFFFFF", borderwidth=0,
        )
        style.map(
            "Run.TButton",
            background=[("active", "#2E4E7E"), ("disabled", "#A9B2C4")],
            foreground=[("disabled", "#EDEFF4")],
        )
        style.configure(
            "TProgressbar", troughcolor="#DDE3EE", background="#4472C4",
            borderwidth=0, thickness=hidpi.px(self, 16),
        )
        style.configure("TEntry", padding=3)

    def _build_layout(self) -> None:
        root = ttk.Frame(self, padding=(18, 14, 18, 12))
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="K-IFRS 1019호 예측단위적립방식(PUC) — 확정급여채무·근무원가·민감도·증감분석",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        self._build_file_section(root, row=1)
        self._build_option_section(root, row=2)
        self._build_prior_section(root, row=3)
        self._build_action_section(root, row=4)
        ttk.Label(root, textvariable=self.status, style="Hint.TLabel").grid(
            row=5, column=0, sticky="w", pady=(2, 0)
        )
        self._build_log_section(root, row=6)
        root.rowconfigure(6, weight=1)

    def _file_row(self, parent, row: int, label: str, var: tk.StringVar,
                  hint: str, command) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(6, 0))
        entry = ttk.Entry(parent, textvariable=var, width=64)
        entry.grid(row=row, column=1, sticky="ew", padx=(10, 8), pady=(6, 0))
        ttk.Button(parent, text="찾아보기", command=command).grid(
            row=row, column=2, sticky="e", pady=(6, 0)
        )
        ttk.Label(parent, text=hint, style="Hint.TLabel").grid(
            row=row + 1, column=1, sticky="w", padx=(10, 0)
        )

    def _build_file_section(self, parent, row: int) -> None:
        box = ttk.Labelframe(parent, text=" 1. 입력 파일 ", padding=(14, 8, 14, 12))
        box.grid(row=row, column=0, sticky="ew")
        box.columnconfigure(1, weight=1)

        self._file_row(
            box, 0, "명부 파일", self.roster_path,
            "Input · 재직자명부 · 퇴직자명부 시트가 들어 있는 통합문서",
            self._pick_roster,
        )
        self._file_row(
            box, 2, "기초율 파일", self.assumptions_path,
            "할인율 · 임금상승률 · 승급률 · 퇴직률 · 사망률 · 지급률",
            self._pick_assumptions,
        )
        self._file_row(
            box, 4, "결과 저장 위치", self.output_path,
            "산출요약 · 증감분석 · 민감도 · 개인별산출 · 검증리포트가 담긴 엑셀",
            self._pick_output,
        )

        buttons = ttk.Frame(box)
        buttons.grid(row=6, column=1, sticky="w", padx=(10, 0), pady=(10, 0))
        ttk.Button(
            buttons, text="산출 가정 입력", command=self._open_editor
        ).pack(side="left")
        ttk.Button(
            buttons, text="기초율 양식 새로 만들기", command=self._make_template
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            buttons, text="전체 기능 화면 열기", command=self._open_full_app
        ).pack(side="left", padx=(6, 0))
        ttk.Label(
            box,
            text="※ 이 창은 예전 입력 화면입니다. 그냥 프로그램을 실행하면 "
                 "분석 그래프 · 보고서 · 산출 내역까지 있는 본 화면이 바로 뜹니다.",
            style="Hint.TLabel", wraplength=hidpi.px(self, 620), justify="left",
        ).grid(row=7, column=1, sticky="w", padx=(10, 0), pady=(4, 0))

    def _build_option_section(self, parent, row: int) -> None:
        box = ttk.Labelframe(parent, text=" 2. 산출 옵션 ", padding=(14, 8, 14, 10))
        box.grid(row=row, column=0, sticky="ew", pady=(10, 0))

        ttk.Checkbutton(box, text="민감도분석 포함", variable=self.include_sensitivity).grid(
            row=0, column=0, sticky="w", padx=(0, 24)
        )
        ttk.Checkbutton(
            box, text="기타장기종업원급여 별도 산출", variable=self.include_longterm
        ).grid(row=0, column=1, sticky="w", padx=(0, 24))
        ttk.Checkbutton(
            box, text="검증 오류가 있어도 산출 강행", variable=self.allow_errors
        ).grid(row=0, column=2, sticky="w")
        ttk.Checkbutton(
            box, text="개인별 결과 별도 파일로 저장", variable=self.export_members
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(
            box,
            text="※ 오류를 남긴 채 산출한 결과는 검토용입니다. 확정 공시 전에는 명부를 수정하세요.\n"
                 "※ 개인별 결과는 결과 파일 안에도 '개인별산출' 시트로 들어갑니다. "
                 "원가배분용으로 따로 받으려면 위를 체크하세요.",
            style="Hint.TLabel",
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def _build_prior_section(self, parent, row: int) -> None:
        box = ttk.Labelframe(
            parent, text=" 3. 전기 산출 결과 (증감분석용 · 최초 평가면 비워 두세요) ",
            padding=(14, 8, 14, 12),
        )
        box.grid(row=row, column=0, sticky="ew", pady=(10, 0))
        box.columnconfigure(1, weight=1)
        box.columnconfigure(3, weight=1)

        ttk.Label(box, text="전기말 확정급여채무").grid(row=0, column=0, sticky="w")
        ttk.Entry(box, textvariable=self.prior_dbo, width=22).grid(
            row=0, column=1, sticky="w", padx=(10, 20)
        )
        ttk.Label(box, text="전기말 할인율 (예: 4.5%)").grid(row=0, column=2, sticky="w")
        ttk.Entry(box, textvariable=self.prior_rate, width=14).grid(
            row=0, column=3, sticky="w", padx=(10, 0)
        )

        ttk.Label(box, text="전기 기초율 파일").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(box, textvariable=self.prior_assumptions).grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=(10, 8), pady=(8, 0)
        )
        ttk.Button(box, text="찾아보기", command=self._pick_prior).grid(
            row=1, column=3, sticky="w", padx=(10, 0), pady=(8, 0)
        )
        ttk.Label(
            box,
            text="전기 기초율을 넣으면 보험수리적손익이 경험조정과 가정변경효과로 나뉩니다.",
            style="Hint.TLabel",
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))

    def _build_action_section(self, parent, row: int) -> None:
        bar = ttk.Frame(parent)
        bar.grid(row=row, column=0, sticky="ew", pady=(14, 8))
        bar.columnconfigure(1, weight=1)

        self.run_button = ttk.Button(
            bar, text="산출 실행", style="Run.TButton", command=self._start_run
        )
        self.run_button.grid(row=0, column=0, sticky="w")

        self.progress = ttk.Progressbar(bar, mode="determinate", maximum=100)
        self.progress.grid(row=0, column=1, sticky="ew", padx=(16, 16))

        self.open_button = ttk.Button(
            bar, text="결과 폴더 열기", command=self._open_output, state="disabled"
        )
        self.open_button.grid(row=0, column=2, sticky="e")

    def _build_log_section(self, parent, row: int) -> None:
        box = ttk.Labelframe(parent, text=" 4. 실행 로그 ", padding=(10, 8, 10, 10))
        box.grid(row=row, column=0, sticky="nsew", pady=(8, 0))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)

        self.log = tk.Text(
            box, height=12, wrap="word", relief="flat", bg="#FFFFFF",
            font=("Consolas", 9), padx=10, pady=8,
        )
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set, state="disabled")

        self.log.tag_configure("head", foreground=_ACCENT, font=("Consolas", 9, "bold"))
        self.log.tag_configure("ok", foreground="#1E7B34")
        self.log.tag_configure("warn", foreground="#B26B00")
        self.log.tag_configure("error", foreground="#B42318")
        self.log.tag_configure("muted", foreground=_MUTED)

    # ── 파일 선택 ────────────────────────────────────────────────

    def _pick_roster(self) -> None:
        path = filedialog.askopenfilename(title="명부 파일 선택", filetypes=_EXCEL_TYPES)
        if not path:
            return
        self.roster_path.set(path)
        if not self.output_path.get():
            source = Path(path)
            self.output_path.set(str(source.with_name(f"{source.stem}_산출결과.xlsx")))
        self._write("명부 파일: " + path, "muted")

    def _pick_assumptions(self) -> None:
        path = filedialog.askopenfilename(title="기초율 파일 선택", filetypes=_EXCEL_TYPES)
        if path:
            self.assumptions_path.set(path)
            self._write("기초율 파일: " + path, "muted")

    def _pick_prior(self) -> None:
        path = filedialog.askopenfilename(title="전기 기초율 파일 선택", filetypes=_EXCEL_TYPES)
        if path:
            self.prior_assumptions.set(path)

    def _pick_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="결과 저장 위치", defaultextension=".xlsx",
            filetypes=[("엑셀 파일", "*.xlsx")],
        )
        if path:
            self.output_path.set(path)

    def _open_full_app(self) -> None:
        """전체 기능 화면(웹앱)을 이 PC 브라우저로 연다.

        서버는 한 번만 띄우고 이후로는 같은 주소를 다시 연다. 누를 때마다
        새로 띄우면 포트가 하나씩 늘고, 브라우저 저장소가 주소(포트)마다
        따로라 **저장해 둔 산출 내역이 안 보인다.**
        """
        from .localapp import MissingAppError, open_in_browser

        if self._app_url:
            import webbrowser

            webbrowser.open(self._app_url)
            self._write(f"전체 기능 화면: {self._app_url}\n")
            return

        try:
            self._app_server, self._app_url = open_in_browser()
        except MissingAppError as exc:
            messagebox.showerror("전체 기능 화면", str(exc), parent=self)
            return
        except OSError as exc:
            messagebox.showerror(
                "전체 기능 화면",
                f"로컬 서버를 띄우지 못했습니다.\n\n{exc}", parent=self)
            return

        self._write(
            f"전체 기능 화면을 열었습니다: {self._app_url}\n"
            "  분석 그래프 · 계리평가 보고서 · 산출 내역 · 단체 관리가 모두 있습니다.\n"
            "  이 창을 닫으면 화면도 닫힙니다.\n", "ok")

    def _open_editor(self) -> None:
        """산출 가정 입력 창. 명부를 골라 두었으면 직군을 미리 채운다."""
        from .editor import open_editor

        def adopt(editor) -> None:
            """방금 저장한 기초율 파일을 이어받는다.

            저장 직후와 창을 닫을 때 모두 호출된다. 예전에는 닫을 때만 받아서,
            저장해 놓고 창을 계속 쓰다 보면 산출 화면이 비어 있었다.
            """
            if editor.path is not None and self.assumptions_path.get() != str(editor.path):
                self.assumptions_path.set(str(editor.path))
                self._write(f"기초율 파일을 지정했습니다: {editor.path.name}")

        roster = self.roster_path.get()
        roster = Path(roster) if roster and Path(roster).exists() else None

        found, groups = self._scan_roster_groups(roster)

        editor = open_editor(
            self, job_groups=groups or None,
            on_close=adopt, on_saved=adopt, roster_path=roster,
        )

        # 명부를 이미 골랐으면 직군 매핑 표를 미리 채워 둔다. 담당자가 같은
        # 파일을 한 번 더 고르지 않아도 되고, 서로 다른 명부를 집을 일도 없다.
        if found:
            editor._map_tab.set_found(found)
            editor.status.configure(
                text=f"{roster.name} 에서 직군 {len(found)}종을 읽어 배정했습니다."
            )

        # 이미 지정된 기초율 파일이 있으면 그 내용으로 화면을 채운다.
        current = self.assumptions_path.get()
        if current and Path(current).exists():
            try:
                editor.load_workbook(Path(current))
                editor.path = Path(current)
                editor.status.configure(text=f"불러왔습니다: {Path(current).name}")
            except Exception as exc:  # 양식이 다른 파일이면 빈 화면에서 시작한다
                editor.status.configure(text=f"기존 파일을 읽지 못했습니다 ({exc})")

    def _scan_roster_groups(self, roster: Path | None):
        """명부에서 (직군 조합, 쓸 묶음 이름) 을 뽑는다.

        묶음 이름은 명부 ``Input`` 시트에 적힌 것만으로는 모자랄 수 있다. 실제로
        케이스 4 의 Input 에는 '정규직·계약직' 둘뿐인데 명부에는 임원이 있어서,
        그대로 두면 임원이 갈 곳이 없어 정규직으로 떨어졌다. 그래서 **배정에
        필요한 이름을 반드시 포함** 시킨다.
        """
        from .jobgroup import DEFAULT_GROUPS, scan_roster, suggest_mapping

        if roster is None:
            return [], self._job_groups_from_roster()

        try:
            from .workbook import open_workbook

            book = open_workbook(roster)
            try:
                found = scan_roster(book)
            finally:
                book.close()
        except Exception as exc:
            self._write(f"명부에서 직군을 읽지 못했습니다: {exc}")
            return [], self._job_groups_from_roster()

        needed = set(suggest_mapping(found, DEFAULT_GROUPS).values())
        groups = [g for g in DEFAULT_GROUPS if g in needed]
        for name in self._job_groups_from_roster():
            if name not in groups:
                groups.append(name)
        return found, groups

    def _make_template(self) -> None:
        path = filedialog.asksaveasfilename(
            title="기초율 양식 저장", defaultextension=".xlsx",
            initialfile="기초율.xlsx", filetypes=[("엑셀 파일", "*.xlsx")],
        )
        if not path:
            return
        try:
            groups = self._job_groups_from_roster()
            write_template(path, job_groups=groups)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"양식을 만들지 못했습니다.\n\n{exc}")
            return
        self.assumptions_path.set(path)
        self._write(f"기초율 양식을 만들었습니다: {path}", "ok")
        messagebox.showinfo(
            APP_TITLE,
            "기초율 양식을 만들었습니다.\n각 시트를 채운 뒤 산출을 실행하세요.",
        )

    def _job_groups_from_roster(self) -> list[str]:
        """양식의 열 머리글을 명부가 참조하는 규정명으로 채우기 위해 미리 읽어 본다."""
        path = self.roster_path.get().strip()
        if not path or not Path(path).exists():
            return []
        try:
            from .config import read_config
            from .workbook import open_workbook

            wb = open_workbook(path)
            try:
                config = read_config(wb)
            finally:
                wb.close()
            return config.referenced_rule_names()
        except Exception:  # 양식 생성은 실패해도 무방하다.
            return []

    # ── 실행 ─────────────────────────────────────────────────────

    def _collect_options(self) -> RunOptions | None:
        roster = self.roster_path.get().strip()
        assumptions = self.assumptions_path.get().strip()
        output = self.output_path.get().strip()

        missing = [
            name for name, value in
            (("명부 파일", roster), ("기초율 파일", assumptions), ("결과 저장 위치", output))
            if not value
        ]
        if missing:
            messagebox.showwarning(APP_TITLE, "다음 항목을 지정하세요.\n\n• " + "\n• ".join(missing))
            return None

        prior = PriorPeriod(
            dbo=_parse_amount(self.prior_dbo.get()),
            discount_rate=_parse_rate(self.prior_rate.get()),
            assumptions_path=self.prior_assumptions.get().strip(),
        )

        return RunOptions(
            roster_path=Path(roster),
            assumptions_path=Path(assumptions),
            output_path=Path(output),
            include_sensitivity=self.include_sensitivity.get(),
            include_longterm=self.include_longterm.get(),
            allow_errors=self.allow_errors.get(),
            prior=prior,
        )

    def _start_run(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        options = self._collect_options()
        if options is None:
            return

        self.run_button.configure(state="disabled")
        self.open_button.configure(state="disabled")
        self.progress["value"] = 0
        self._clear_log()
        self._write("산출을 시작합니다.", "head")

        self._worker = threading.Thread(target=self._run, args=(options,), daemon=True)
        self._worker.start()

    def _run(self, options: RunOptions) -> None:
        """작업 스레드. UI 는 건드리지 않고 큐로만 알린다."""

        def progress(message: str, fraction: float) -> None:
            self._messages.put(("progress", (message, fraction)))

        try:
            run = run_valuation(options, progress)
            write_report(run, options.output_path)
            self._messages.put(("done", (run, options.output_path)))
        except PensionDataError as exc:
            self._messages.put(("data_error", exc))
        except Exception as exc:
            self._messages.put(("error", (exc, traceback.format_exc())))

    def _drain_messages(self) -> None:
        try:
            while True:
                kind, payload = self._messages.get_nowait()
                handler = getattr(self, f"_on_{kind}", None)
                if handler is not None:
                    handler(payload)
        except queue.Empty:
            pass
        self.after(120, self._drain_messages)

    # ── 결과 처리 ────────────────────────────────────────────────

    def _on_progress(self, payload) -> None:
        message, fraction = payload
        self.progress["value"] = fraction * 100
        self.status.set(message)
        self._write(f"  · {message}", "muted")

    def _on_done(self, payload) -> None:
        run, output = payload
        self._finish()
        self.progress["value"] = 100
        self.open_button.configure(state="normal")
        self._summarize(run, output)

        member_path: Path | None = None
        if self.export_members.get():
            from .members import write_member_export

            member_path = self._member_export_path(output)
            try:
                write_member_export(run, member_path)
            except OSError as exc:
                # 산출은 이미 끝났다. 개인별 파일을 못 썼다고 결과까지 버리지 않는다.
                self._write(f"  개인별 결과를 저장하지 못했습니다: {exc}", "error")
                member_path = None
            else:
                self._write(f"  개인별 결과: {member_path}", "ok")

        self.status.set(f"산출 완료 — {output}")
        extra = f"\n개인별 결과: {member_path}" if member_path else ""
        messagebox.showinfo(APP_TITLE, f"산출을 마쳤습니다.\n\n{output}{extra}")

    def _on_data_error(self, exc: PensionDataError) -> None:
        self._finish()
        self.status.set("명부 검증 오류로 중단되었습니다.")
        self._write("", "muted")
        self._write(str(exc), "error")
        for issue in exc.issues[:20]:
            self._write(f"  {issue}", "error")
        if len(exc.issues) > 20:
            self._write(f"  … 외 {len(exc.issues) - 20}건", "error")
        self._write(
            "\n명부를 수정한 뒤 다시 실행하세요. "
            "검토 목적이면 '검증 오류가 있어도 산출 강행' 을 켜고 다시 실행할 수 있습니다.",
            "warn",
        )
        messagebox.showerror(APP_TITLE, str(exc))

    def _on_error(self, payload) -> None:
        exc, trace = payload
        self._finish()
        self.status.set("오류가 발생했습니다.")
        self._write(f"오류: {exc}", "error")
        self._write(trace, "muted")
        messagebox.showerror(APP_TITLE, f"산출 중 오류가 발생했습니다.\n\n{exc}")

    def _finish(self) -> None:
        self.run_button.configure(state="normal")
        self._worker = None

    def _member_export_path(self, output: Path) -> Path:
        """결과 파일 옆에 둘 개인별 결과 파일 경로."""
        return output.with_name(f"{output.stem}_개인별{output.suffix}")

    def _summarize(self, run: PensionRun, output: Path) -> None:
        v = run.valuation
        self._write("", "muted")
        self._write("── 산출 결과 요약 " + "─" * 40, "head")
        self._write(f"  산출기준일        {run.config.base_date}")
        if run.assumptions.discount.flat is None:
            rate, tail = run.valuation.single_discount_rate(), "  (수익률곡선기법 단일할인율)"
        else:
            rate, tail = run.assumptions.discount.level_rate, ""
        self._write(f"  적용 할인율       {rate:.3%}{tail}")
        self._write(f"  산출대상 인원     {v.headcount:,}명")
        self._write(f"  확정급여채무      {v.dbo:>18,.0f} 원", "ok")
        self._write(f"  당기근무원가      {v.service_cost:>18,.0f} 원")
        self._write(f"  이자원가(차기)    {v.interest_cost:>18,.0f} 원")
        self._write(f"  퇴직급여추계액    {v.accrued_benefit:>18,.0f} 원")
        self._write(f"  듀레이션          {v.duration:>18,.1f} 년")

        if run.longterm is not None:
            self._write(f"  장기급여채무      {run.longterm.dbo:>18,.0f} 원")

        if run.rollforward is not None:
            self._write(
                f"  보험수리적손익    {run.rollforward.actuarial_gain_loss:>18,.0f} 원"
            )

        errors = len(run.issues.errors)
        warnings = len(run.issues.warnings)
        tag = "error" if errors else ("warn" if warnings else "ok")
        self._write(f"  명부 검증         오류 {errors}건 / 경고 {warnings}건", tag)
        for issue in run.issues.errors[:5]:
            self._write(f"    {issue}", "error")
        for issue in run.issues.warnings[:5]:
            self._write(f"    {issue}", "warn")

        self._write("", "muted")
        self._write(f"결과 파일: {output}", "ok")

    def _open_output(self) -> None:
        import subprocess
        import sys

        target = Path(self.output_path.get()).parent
        if not target.exists():
            return
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", str(target)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])

    # ── 로그 ─────────────────────────────────────────────────────

    def _write(self, message: str, tag: str = "") -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")


def _parse_amount(value: str) -> float:
    token = value.strip().replace(",", "").replace("원", "")
    if not token:
        return 0.0
    try:
        return float(token)
    except ValueError:
        return 0.0


def _parse_rate(value: str) -> float:
    token = value.strip().replace(" ", "")
    if not token:
        return 0.0
    percent = token.endswith("%")
    if percent:
        token = token[:-1]
    try:
        number = float(token)
    except ValueError:
        return 0.0
    if percent or number > 1.0:
        return number / 100.0
    return number


def main() -> int:
    """GUI 진입점."""
    # 창을 만들기 **전** 이어야 한다. Tk 가 뜬 뒤에는 윈도우가 이미 '늘려서
    # 그리기' 로 정해 버려서, 나중에 선언해도 흐린 채로 남는다.
    hidpi.declare_dpi_aware()
    app = PensionApp()
    app.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
