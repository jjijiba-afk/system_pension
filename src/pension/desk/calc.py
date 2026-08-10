"""산출 탭 — 명부와 기초율을 걸고 돌린다.

예전 입력 창이 하던 일에 웹앱에만 있던 것들을 얹었다. 저장해 둔 명부 고르기,
전기 산출 연결, 전기 명부 맞대기, 사외적립자산, 기준일 지정까지 한 화면에서
끝난다.
"""

from __future__ import annotations

import datetime as _dt
import queue
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .. import hidpi
from ..errors import PensionDataError
from . import theme
from .hub import Hub, Loaded
from .widgets import ScrollFrame, Table, labeled, section

__all__ = ["CalcTab"]

_EXCEL = [("엑셀 파일", "*.xlsx *.xlsm *.xls"), ("모든 파일", "*.*")]


def parse_amount(value: str) -> float:
    token = str(value).strip().replace(",", "").replace("원", "")
    if not token:
        return 0.0
    try:
        return float(token)
    except ValueError:
        return 0.0


def parse_rate(value: str) -> float:
    token = str(value).strip().replace(" ", "")
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


def parse_date(value: str) -> _dt.date | None:
    token = str(value).strip()
    if not token:
        return None
    for shape in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return _dt.datetime.strptime(token, shape).date()
        except ValueError:
            continue
    return None


class CalcTab(ttk.Frame):
    """산출 화면."""

    def __init__(self, parent: tk.Misc, hub: Hub) -> None:
        super().__init__(parent)
        self.hub = hub
        self._messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._prior_link: dict[str, Any] | None = None

        self.roster_path = tk.StringVar()
        self.saved_roster = tk.StringVar()
        self.assumptions_path = tk.StringVar()
        self.saved_preset = tk.StringVar()
        self.output_path = tk.StringVar()
        self.base_date = tk.StringVar()
        self.period_start = tk.StringVar()
        self.include_sensitivity = tk.BooleanVar(value=True)
        self.include_longterm = tk.BooleanVar(value=True)
        self.split_remeasurement = tk.BooleanVar(value=False)
        self.export_members = tk.BooleanVar(value=False)
        self.allow_errors = tk.BooleanVar(value=False)
        self.prior_run = tk.StringVar()
        self.prior_dbo = tk.StringVar()
        self.prior_rate = tk.StringVar()
        self.prior_sc = tk.StringVar()
        self.prior_assumptions = tk.StringVar()
        self.prior_longterm = tk.StringVar()
        self.asset_open = tk.StringVar()
        self.asset_close = tk.StringVar()
        self.asset_contrib = tk.StringVar()
        self.asset_expected = tk.StringVar()
        self.status = tk.StringVar(value="명부와 기초율 파일을 지정하세요.")

        self._build()
        hub.watch("client", self._refresh_prior_runs)
        hub.watch("library", self._refresh_library)
        self.after(120, self._drain)

    # ── 화면 ────────────────────────────────────────────────────

    def _build(self) -> None:
        shell = ScrollFrame(self)
        shell.pack(fill="both", expand=True)
        self._shell = shell
        body = shell.body

        self._files(body)
        self._option_box(body)
        self._dates(body)
        self._prior(body)
        self._assets(body)
        self._actions(body)
        self._log(body)
        self._refresh_library()
        self._refresh_prior_runs()

    def _file_row(self, parent: tk.Misc, row: int, label: str, var: tk.StringVar,
                  hint: str, command) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(parent, textvariable=var).grid(
            row=row, column=1, sticky="ew", padx=(10, 8), pady=(6, 0))
        ttk.Button(parent, text="찾아보기", command=command).grid(
            row=row, column=2, sticky="e", pady=(6, 0))
        ttk.Label(parent, text=hint, style="Hint.TLabel").grid(
            row=row + 1, column=1, sticky="w", padx=(10, 0))

    def _files(self, parent: tk.Misc) -> None:
        box = section(parent, "1. 입력 파일")
        box.columnconfigure(1, weight=1)

        self._file_row(box, 0, "명부 파일", self.roster_path,
                       "Input · 재직자명부 · 퇴직자명부 시트가 들어 있는 통합문서",
                       self._pick_roster)
        ttk.Label(box, text="저장된 명부").grid(row=2, column=0, sticky="w", pady=(6, 0))
        line = ttk.Frame(box)
        line.grid(row=2, column=1, sticky="ew", padx=(10, 0), pady=(6, 0))
        self._roster_box = ttk.Combobox(line, textvariable=self.saved_roster,
                                        state="readonly", width=30)
        self._roster_box.pack(side="left")
        self._roster_box.bind("<<ComboboxSelected>>", self._use_saved_roster)
        ttk.Button(line, text="이 파일을 목록에 저장",
                   command=self._save_roster).pack(side="left", padx=(6, 0))

        self._file_row(box, 3, "기초율 파일", self.assumptions_path,
                       "할인율 · 임금상승률 · 승급률 · 퇴직률 · 사망률 · 지급률",
                       self._pick_assumptions)
        ttk.Label(box, text="가정세트").grid(row=5, column=0, sticky="w", pady=(6, 0))
        line = ttk.Frame(box)
        line.grid(row=5, column=1, sticky="ew", padx=(10, 0), pady=(6, 0))
        self._preset_box = ttk.Combobox(line, textvariable=self.saved_preset,
                                        state="readonly", width=30)
        self._preset_box.pack(side="left")
        self._preset_box.bind("<<ComboboxSelected>>", self._use_saved_preset)
        ttk.Label(line, text="[산출가정 입력] 탭에서 만들어 저장한 것",
                  style="Hint.TLabel").pack(side="left", padx=(8, 0))

        self._file_row(box, 6, "결과 저장 위치", self.output_path,
                       "산출요약 · 증감분석 · 민감도 · 개인별산출 · 검증리포트가 담긴 엑셀",
                       self._pick_output)

    def _option_box(self, parent: tk.Misc) -> None:
        """산출 옵션 칸.

        이름에 ``_options`` 를 쓰면 안 된다 — ``tkinter.Misc._options`` 를 가려
        버려서, 위젯을 만들 때 tk 가 옵션을 풀려다 이 함수를 부르고 창이 뜨기도
        전에 죽는다.
        """
        box = section(parent, "2. 산출 옵션")
        for column, (text, var) in enumerate((
            ("민감도분석 포함", self.include_sensitivity),
            ("기타장기종업원급여 별도 산출", self.include_longterm),
            ("검증 오류가 있어도 산출 강행", self.allow_errors),
        )):
            ttk.Checkbutton(box, text=text, variable=var).grid(
                row=0, column=column, sticky="w", padx=(0, 24))
        ttk.Checkbutton(box, text="개인별 결과 별도 파일로 저장",
                        variable=self.export_members).grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Checkbutton(box, text="가정변경효과를 가정별로 쪼개기 (느립니다)",
                        variable=self.split_remeasurement).grid(
            row=1, column=1, columnspan=2, sticky="w", pady=(6, 0))
        labeled(box,
                "※ 오류를 남긴 채 산출한 결과는 검토용입니다. 확정 공시 전에는 명부를 수정하세요.\n"
                "※ 가정별 쪼개기는 명부 전체 산출이 네 번 더 돕니다. 전기 기초율을 준 회차에서만 뜻이 있습니다.")

    def _dates(self, parent: tk.Misc) -> None:
        box = section(parent, "3. 기준일 (비우면 명부의 값을 씁니다)")
        ttk.Label(box, text="산출기준일").grid(row=0, column=0, sticky="w")
        ttk.Entry(box, textvariable=self.base_date, width=16).grid(
            row=0, column=1, sticky="w", padx=(10, 24))
        ttk.Label(box, text="산출 시작일 (직전 결산일)").grid(row=0, column=2, sticky="w")
        ttk.Entry(box, textvariable=self.period_start, width=16).grid(
            row=0, column=3, sticky="w", padx=(10, 0))
        labeled(box,
                "기준일을 넣으면 명부를 고치지 않고 그 날짜로 산출합니다(가결산·시산). "
                "시작일을 넣으면 이자원가를 그 기간으로 환산합니다 — 결산기가 바뀌어 "
                "기간이 1년이 아닌 회차에서 이자원가가 부풀지 않습니다. 예: 2024-12-31")

    def _prior(self, parent: tk.Misc) -> None:
        box = section(parent, "4. 전기 산출 결과 (증감분석용 · 최초 평가면 비워 두세요)")
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="저장된 산출에서").grid(row=0, column=0, sticky="w")
        line = ttk.Frame(box)
        line.grid(row=0, column=1, columnspan=3, sticky="ew", padx=(10, 0))
        self._prior_box = ttk.Combobox(line, textvariable=self.prior_run,
                                       state="readonly", width=28)
        self._prior_box.pack(side="left")
        self._prior_box.bind("<<ComboboxSelected>>", self._use_prior_run)
        self._check_button = ttk.Button(line, text="전기 명부와 맞대어 보기",
                                        command=self._compare_rosters, state="disabled")
        self._check_button.pack(side="left", padx=(6, 0))
        self._prior_note = ttk.Label(box, text="지금 고른 단체의 저장된 산출만 뜹니다.",
                                     style="Hint.TLabel")
        self._prior_note.grid(row=1, column=1, columnspan=3, sticky="w", padx=(10, 0))

        pairs = (
            ("전기말 확정급여채무", self.prior_dbo), ("전기말 할인율 (예: 4.5%)", self.prior_rate),
            ("전기 근무원가", self.prior_sc), ("전기말 장기급여채무", self.prior_longterm),
        )
        for index, (label, var) in enumerate(pairs):
            row, column = divmod(index, 2)
            ttk.Label(box, text=label).grid(row=2 + row, column=column * 2,
                                            sticky="w", pady=(8, 0))
            ttk.Entry(box, textvariable=var, width=20).grid(
                row=2 + row, column=column * 2 + 1, sticky="w", padx=(10, 20), pady=(8, 0))

        ttk.Label(box, text="전기 기초율 파일").grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(box, textvariable=self.prior_assumptions).grid(
            row=4, column=1, columnspan=2, sticky="ew", padx=(10, 8), pady=(8, 0))
        ttk.Button(box, text="찾아보기", command=self._pick_prior).grid(
            row=4, column=3, sticky="w", pady=(8, 0))
        labeled(box, "전기 기초율을 넣으면 보험수리적손익이 경험조정과 가정변경효과로 나뉩니다.")

        self._check_status = ttk.Label(box, text="", style="Hint.TLabel")
        self._check_status.grid(row=6, column=0, columnspan=4, sticky="w", pady=(8, 0))
        self._check_table = Table(
            box, ["구분", "사번", "내용"], widths=[110, 90, 470],
            aligns=["w", "w", "w"], height=6, stretch=2)
        self._check_table.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        self._check_table.grid_remove()

    def _assets(self, parent: tk.Misc) -> None:
        box = section(parent, "5. 사외적립자산 (비우면 명부 '1)일반사항' 에서 읽습니다)")
        pairs = (
            ("기초 공정가치", self.asset_open), ("기말 공정가치", self.asset_close),
            ("당기 부담금 납입액", self.asset_contrib), ("차년도 예상 부담금", self.asset_expected),
        )
        for index, (label, var) in enumerate(pairs):
            row, column = divmod(index, 2)
            ttk.Label(box, text=label).grid(row=row, column=column * 2, sticky="w",
                                            pady=(4, 0))
            ttk.Entry(box, textvariable=var, width=20).grid(
                row=row, column=column * 2 + 1, sticky="w", padx=(10, 24), pady=(4, 0))
        labeled(box, "넣으면 사외적립자산 증감표와 순확정급여부채가 만들어집니다. "
                     "차년도 예상 부담금은 문단 147(b) 공시에 씁니다.")

    def _actions(self, parent: tk.Misc) -> None:
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(6, 8))
        bar.columnconfigure(1, weight=1)
        self.run_button = ttk.Button(bar, text="산출 실행", style="Run.TButton",
                                     command=self.start)
        self.run_button.grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(bar, mode="determinate", maximum=100)
        self.progress.grid(row=0, column=1, sticky="ew", padx=(16, 16))
        self.open_button = ttk.Button(bar, text="결과 폴더 열기", command=self._open_output,
                                      state="disabled")
        self.open_button.grid(row=0, column=2, sticky="e")

        keep = ttk.Frame(parent)
        keep.pack(fill="x", pady=(0, 6))
        ttk.Label(keep, textvariable=self.status, style="Hint.TLabel").pack(
            side="left")

    def _log(self, parent: tk.Misc) -> None:
        box = section(parent, "6. 실행 로그", pady=(4, 4))
        self.log = tk.Text(box, height=14, wrap="word", relief="flat", bg=theme.CARD,
                           font=(theme.pick_font(self, mono=True), 9),
                           padx=hidpi.px(self, 10), pady=hidpi.px(self, 8))
        self.log.pack(side="left", fill="both", expand=True)
        bar = ttk.Scrollbar(box, orient="vertical", command=self.log.yview)
        bar.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=bar.set, state="disabled")
        for tag, colour in (("head", theme.ACCENT), ("ok", theme.GOOD),
                            ("warn", theme.WARN), ("error", theme.BAD),
                            ("muted", theme.MUTED)):
            self.log.tag_configure(tag, foreground=colour)

    # ── 목록 갱신 ───────────────────────────────────────────────

    def _refresh_library(self) -> None:
        from ..library import PRESET_KIND, ROSTER_KIND, entries

        self._roster_box.configure(values=[e.name for e in entries(ROSTER_KIND)])
        self._preset_box.configure(values=[e.name for e in entries(PRESET_KIND)])

    def _refresh_prior_runs(self) -> None:
        from ..runs import saved_names

        names = saved_names()
        self._prior_box.configure(values=names)
        if self.prior_run.get() not in names:
            self.prior_run.set("")
            self._prior_link = None
            self._check_button.configure(state="disabled")

    # ── 파일 고르기 ─────────────────────────────────────────────

    def _pick_roster(self) -> None:
        path = filedialog.askopenfilename(title="명부 파일 선택", filetypes=_EXCEL,
                                          parent=self)
        if not path:
            return
        self.roster_path.set(path)
        self.saved_roster.set("")
        if not self.output_path.get():
            source = Path(path)
            self.output_path.set(str(source.with_name(f"{source.stem}_산출결과.xlsx")))
        self.write("명부 파일: " + path, "muted")

    def _pick_assumptions(self) -> None:
        path = filedialog.askopenfilename(title="기초율 파일 선택", filetypes=_EXCEL,
                                          parent=self)
        if path:
            self.assumptions_path.set(path)
            self.saved_preset.set("")
            self.write("기초율 파일: " + path, "muted")

    def _pick_prior(self) -> None:
        path = filedialog.askopenfilename(title="전기 기초율 파일 선택", filetypes=_EXCEL,
                                          parent=self)
        if path:
            self.prior_assumptions.set(path)

    def _pick_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="결과 저장 위치", defaultextension=".xlsx",
            filetypes=[("엑셀 파일", "*.xlsx")], parent=self)
        if path:
            self.output_path.set(path)

    def _use_saved_roster(self, _event: Any = None) -> None:
        from ..library import ROSTER_KIND, find_entry

        entry = find_entry(ROSTER_KIND, self.saved_roster.get())
        if entry is None:
            return
        self.roster_path.set(str(entry.path))
        if not self.output_path.get():
            self.output_path.set(str(entry.path.with_name(f"{entry.name}_산출결과.xlsx")))
        self.write(f"저장된 명부 '{entry.name}' 을(를) 씁니다.", "muted")

    def _use_saved_preset(self, _event: Any = None) -> None:
        from ..library import PRESET_KIND, find_entry

        entry = find_entry(PRESET_KIND, self.saved_preset.get())
        if entry is None:
            return
        self.assumptions_path.set(str(entry.path))
        self.write(f"가정세트 '{entry.name}' 을(를) 씁니다.", "muted")

    def _save_roster(self) -> None:
        from tkinter import simpledialog

        from ..library import ROSTER_KIND, register

        path = self.roster_path.get().strip()
        if not path or not Path(path).exists():
            messagebox.showinfo("저장된 명부", "먼저 명부 파일을 고르세요.", parent=self)
            return
        name = simpledialog.askstring("명부 저장", "목록에 표시할 이름을 정하세요.",
                                      parent=self, initialvalue=Path(path).stem)
        if not name:
            return
        try:
            entry = register(ROSTER_KIND, path, name=name)
        except Exception as exc:
            messagebox.showerror("저장된 명부", f"저장하지 못했습니다.\n\n{exc}", parent=self)
            return
        self.hub.announce("library")
        self.saved_roster.set(entry.name)
        self.write(f"명부 '{entry.name}' 을(를) 목록에 저장했습니다.", "ok")

    # ── 전기 산출 연결 ──────────────────────────────────────────

    def _use_prior_run(self, _event: Any = None) -> None:
        from ..runs import prior_link

        name = self.prior_run.get()
        if not name:
            self._prior_link = None
            self._check_button.configure(state="disabled")
            return
        try:
            link = prior_link(name)
        except (LookupError, OSError, ValueError) as exc:
            messagebox.showerror("전기 산출", f"불러오지 못했습니다.\n\n{exc}", parent=self)
            return
        self._prior_link = link
        self.prior_dbo.set(f"{link['dbo']:,.0f}")
        self.prior_rate.set(f"{link['rate']:.3%}")
        self.prior_sc.set(f"{link['service_cost']:,.0f}")
        if link.get("longterm_dbo"):
            self.prior_longterm.set(f"{link['longterm_dbo']:,.0f}")
        if link.get("assumptions"):
            self.prior_assumptions.set(str(link["assumptions"]))
        self._check_button.configure(state="normal" if link.get("roster") else "disabled")
        self._prior_note.configure(
            text=f"'{name}' 의 확정급여채무·할인율·근무원가를 채웠습니다."
                 + ("  전기 기초율까지 연결되어 경험조정과 가정변경효과가 나뉩니다."
                    if link.get("assumptions") else
                    "  전기 기초율 파일이 없어 손익은 나뉘지 않습니다."))

    def _compare_rosters(self) -> None:
        """당기 명부를 전기 산출의 명부와 맞대어 본다."""
        from ..priorcheck import compare_rosters
        from ..runs import read_roster_only

        current = self.roster_path.get().strip()
        if not current or not Path(current).exists():
            messagebox.showinfo("맞대어 보기", "먼저 당기 명부를 고르세요.", parent=self)
            return
        if not self._prior_link or not self._prior_link.get("roster"):
            messagebox.showinfo("맞대어 보기", "전기 산출에 명부가 남아 있지 않습니다.",
                                parent=self)
            return

        self._check_status.configure(text="맞대어 보는 중…", style="Hint.TLabel")
        self.update_idletasks()
        try:
            found = compare_rosters(read_roster_only(current),
                                    read_roster_only(self._prior_link["roster"]))
        except Exception as exc:
            self._check_status.configure(text=f"읽지 못했습니다: {exc}", style="Bad.TLabel")
            return

        rows = [f.as_row() for f in found.serious] + [f.as_row() for f in found.notes]
        serious = len(found.serious)
        self._check_table.grid()
        self._check_table.fill(
            rows, tags=lambda index, _row: "bad" if index < serious else "muted")
        self._check_status.configure(
            text=found.summary(),
            style="Bad.TLabel" if serious else "Good.TLabel")
        self.write(f"전기 명부 맞대기 — {found.summary()}", "warn" if serious else "ok")
        for finding in found.serious:
            self.write(f"  {finding.message}", "error")

    # ── 실행 ────────────────────────────────────────────────────

    def collect(self):
        """화면의 입력을 :class:`RunOptions` 로. 빠진 게 있으면 ``None``."""
        from ..pipeline import PlanAssetInput, PriorPeriod, RunOptions

        roster = self.roster_path.get().strip()
        assumptions = self.assumptions_path.get().strip()
        output = self.output_path.get().strip()
        missing = [name for name, value in
                   (("명부 파일", roster), ("기초율 파일", assumptions),
                    ("결과 저장 위치", output)) if not value]
        if missing:
            messagebox.showwarning("산출", "다음 항목을 지정하세요.\n\n• " + "\n• ".join(missing),
                                   parent=self)
            return None

        prior = PriorPeriod(
            dbo=parse_amount(self.prior_dbo.get()),
            service_cost=parse_amount(self.prior_sc.get()),
            discount_rate=parse_rate(self.prior_rate.get()),
            assumptions_path=self.prior_assumptions.get().strip(),
            longterm_dbo=parse_amount(self.prior_longterm.get()),
        )
        assets = PlanAssetInput(
            opening_fair_value=parse_amount(self.asset_open.get()),
            closing_fair_value=parse_amount(self.asset_close.get()),
            contributions=parse_amount(self.asset_contrib.get()),
            expected_contributions=parse_amount(self.asset_expected.get()),
        )
        return RunOptions(
            roster_path=Path(roster),
            assumptions_path=Path(assumptions),
            output_path=Path(output),
            base_date=parse_date(self.base_date.get()),
            period_start=parse_date(self.period_start.get()),
            include_sensitivity=self.include_sensitivity.get(),
            include_longterm=self.include_longterm.get(),
            split_remeasurement=self.split_remeasurement.get(),
            allow_errors=self.allow_errors.get(),
            prior=prior,
            plan_assets=assets,
        )

    def start(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        options = self.collect()
        if options is None:
            return
        self.run_button.configure(state="disabled")
        self.open_button.configure(state="disabled")
        self.progress["value"] = 0
        self.clear_log()
        self.write("산출을 시작합니다.", "head")
        self._worker = threading.Thread(target=self._work, args=(options,), daemon=True)
        self._worker.start()

    def _work(self, options) -> None:
        """작업 스레드. 화면은 건드리지 않고 큐로만 알린다."""
        from ..pipeline import run_valuation
        from ..report import write_report

        def progress(message: str, fraction: float) -> None:
            self._messages.put(("progress", (message, fraction)))

        try:
            run = run_valuation(options, progress)
            write_report(run, options.output_path)
            self._messages.put(("done", (run, options)))
        except PensionDataError as exc:
            self._messages.put(("data_error", exc))
        except Exception as exc:
            self._messages.put(("error", (exc, traceback.format_exc())))

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self._messages.get_nowait()
                getattr(self, f"_on_{kind}")(payload)
        except queue.Empty:
            pass
        self.after(120, self._drain)

    def _on_progress(self, payload) -> None:
        message, fraction = payload
        self.progress["value"] = fraction * 100
        self.status.set(message)
        self.write(f"  · {message}", "muted")

    def _on_done(self, payload) -> None:
        run, options = payload
        self._finish()
        self.progress["value"] = 100
        self.open_button.configure(state="normal")
        self._summarize(run, options.output_path)

        if self.export_members.get():
            from ..members import write_member_export

            target = options.output_path.with_name(
                f"{options.output_path.stem}_개인별{options.output_path.suffix}")
            try:
                write_member_export(run, target)
            except OSError as exc:
                self.write(f"  개인별 결과를 저장하지 못했습니다: {exc}", "error")
            else:
                self.write(f"  개인별 결과: {target}", "ok")

        self.status.set(f"산출 완료 — {options.output_path}")
        self.hub.publish(Loaded(run=run, options=options, output=options.output_path))

    def _on_data_error(self, exc: PensionDataError) -> None:
        self._finish()
        self.status.set("명부 검증 오류로 중단되었습니다.")
        self.write("", "muted")
        self.write(str(exc), "error")
        for issue in exc.issues[:20]:
            self.write(f"  {issue}", "error")
        if len(exc.issues) > 20:
            self.write(f"  … 외 {len(exc.issues) - 20}건", "error")
        self.write("\n명부를 수정한 뒤 다시 실행하세요. 검토 목적이면 "
                   "'검증 오류가 있어도 산출 강행' 을 켜고 다시 실행할 수 있습니다.", "warn")

    def _on_error(self, payload) -> None:
        exc, trace = payload
        self._finish()
        self.status.set("오류가 발생했습니다.")
        self.write(f"오류: {exc}", "error")
        self.write(trace, "muted")

    def _finish(self) -> None:
        self.run_button.configure(state="normal")
        self._worker = None

    def _summarize(self, run, output: Path) -> None:
        v = run.valuation
        self.write("", "muted")
        self.write("── 산출 결과 요약 " + "─" * 40, "head")
        self.write(f"  산출기준일        {run.config.base_date}")
        if run.assumptions.discount.flat is None:
            rate, tail = v.single_discount_rate(), "  (수익률곡선기법 단일할인율)"
        else:
            rate, tail = run.assumptions.discount.level_rate, ""
        self.write(f"  적용 할인율       {rate:.3%}{tail}")
        self.write(f"  산출대상 인원     {v.headcount:,}명")
        self.write(f"  확정급여채무      {v.dbo:>18,.0f} 원", "ok")
        self.write(f"  당기근무원가      {v.service_cost:>18,.0f} 원")
        self.write(f"  이자원가(차기)    {v.interest_cost:>18,.0f} 원")
        self.write(f"  퇴직급여추계액    {v.accrued_benefit:>18,.0f} 원")
        self.write(f"  듀레이션          {v.duration:>18,.1f} 년")
        if run.longterm is not None:
            self.write(f"  장기급여채무      {run.longterm.dbo:>18,.0f} 원")
        if run.rollforward is not None:
            self.write(f"  보험수리적손익    {run.rollforward.actuarial_gain_loss:>18,.0f} 원")
        if run.plan_assets is not None:
            self.write(f"  순확정급여부채    {run.plan_assets.net_liability:>18,.0f} 원")

        errors = len(run.issues.errors)
        warnings = len(run.issues.warnings)
        tag = "error" if errors else ("warn" if warnings else "ok")
        self.write(f"  명부 검증         오류 {errors}건 / 경고 {warnings}건", tag)
        for issue in run.issues.errors[:5]:
            self.write(f"    {issue}", "error")
        for issue in run.issues.warnings[:5]:
            self.write(f"    {issue}", "warn")
        self.write("", "muted")
        self.write(f"결과 파일: {output}", "ok")
        self.write("[분석] · [계리평가 보고서] · [사번 조회] 탭이 이 산출로 채워졌습니다. "
                   "[산출 내역] 탭에서 이름을 붙여 저장해 두면 다음 결산에 전기로 끌어옵니다.",
                   "muted")

    def _open_output(self) -> None:
        from .files import reveal

        target = Path(self.output_path.get())
        if target.parent.exists():
            reveal(target.parent)

    # ── 로그 ────────────────────────────────────────────────────

    def write(self, message: str, tag: str = "") -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # ── 다른 탭이 부르는 것 ─────────────────────────────────────

    def adopt_assumptions(self, path: Path) -> None:
        """가정 입력 탭에서 방금 저장한 파일을 이어받는다."""
        if str(path) != self.assumptions_path.get():
            self.assumptions_path.set(str(path))
            self.saved_preset.set("")
            self.write(f"기초율 파일을 지정했습니다: {path.name}", "ok")

    def adopt_roster(self, path: Path) -> None:
        self.roster_path.set(str(path))
        if not self.output_path.get():
            self.output_path.set(str(path.with_name(f"{path.stem}_산출결과.xlsx")))
        self.write(f"명부를 지정했습니다: {path.name}", "ok")
