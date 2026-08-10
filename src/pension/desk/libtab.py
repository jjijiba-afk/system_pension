"""자료실 탭 — 단체 · 등록 자료 · 시험 명부 · 보관함.

금리표와 표준률은 결산기가 같으면 어느 단체든 같은 것을 쓴다. 단체마다 파일을
다시 찾아 지정하면 스무 단체를 산출할 때 스무 번 고르는 셈이고, 그중 한 번이라도
다른 파일을 집으면 그 단체만 할인율이 달라진다 — 눈에 띄지도 않는다. 그래서 한 번
등록해 두고 목록에서 고른다.
"""

from __future__ import annotations

import datetime as _dt
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from .files import reveal
from .hub import Hub
from .widgets import ScrollFrame, Table, labeled, section

__all__ = ["LibraryTab"]

_EXCEL = [("엑셀 파일", "*.xlsx *.xlsm *.xls"), ("모든 파일", "*.*")]


class LibraryTab(ttk.Frame):
    """자료실 화면."""

    def __init__(self, parent: tk.Misc, hub: Hub) -> None:
        super().__init__(parent)
        self.hub = hub
        self.kind = tk.StringVar()
        self.seed = tk.StringVar(value="20251231")
        self.gen_base = tk.StringVar(value="2025-12-31")

        shell = ScrollFrame(self)
        shell.pack(fill="both", expand=True)
        self._shell = shell
        body = shell.body

        self._clients(body)
        self._assets(body)
        self._generator(body)
        self._backup(body)

        hub.watch("library", self.refresh)
        hub.watch("client", self._fill_clients)
        self.refresh()

    # ── 단체 ────────────────────────────────────────────────────

    def _clients(self, parent: tk.Misc) -> None:
        box = section(parent, "단체")
        self.client_table = Table(box, ["단체", "산출 건수", "최근 저장"],
                                  widths=[240, 90, 150], aligns=["w", "e", "w"], height=6)
        self.client_table.pack(fill="x")
        bar = ttk.Frame(box)
        bar.pack(fill="x", pady=(8, 0))
        for label, command in (("고르기", self.pick_client), ("새 단체", self.add_client),
                               ("이름 바꾸기", self.rename_client), ("삭제", self.remove_client)):
            ttk.Button(bar, text=label, command=command).pack(side="left", padx=(0, 6))
        self.client_status = ttk.Label(box, text="", style="Hint.TLabel")
        self.client_status.pack(anchor="w", pady=(6, 0))
        labeled(box, "산출 내역은 단체 안에 들어갑니다. 전기 산출을 고를 때 남의 회사 것이 "
                     "섞이지 않게 하려는 것입니다. 금리표·표준률은 공용이라 단체와 무관합니다.")

    def _fill_clients(self) -> None:
        from .. import clients

        current = clients.current()
        self.client_table.fill(
            [(entry.name, f"{entry.runs:,}", entry.last_saved) for entry in clients.entries()],
            keys=lambda _i, row: str(row[0]),
            tags=lambda _i, row: "total" if row[0] == current else "")
        self.client_status.configure(text=f"지금 고른 단체 — {current}")

    def pick_client(self) -> None:
        name = self.client_table.selected()
        if not name:
            self.client_status.configure(text="목록에서 단체를 고르세요.", style="Bad.TLabel")
            return
        self.hub.select_client(name)

    def add_client(self) -> None:
        from .. import clients

        name = simpledialog.askstring("새 단체", "단체 이름을 입력하세요.", parent=self)
        if not name:
            return
        try:
            clients.create(name)
        except ValueError as exc:
            messagebox.showerror("단체", str(exc), parent=self)
            return
        self.hub.select_client(name)

    def rename_client(self) -> None:
        from .. import clients

        old = self.client_table.selected()
        if not old:
            return
        new = simpledialog.askstring("이름 바꾸기", "새 이름을 입력하세요.",
                                     parent=self, initialvalue=old)
        if not new or new == old:
            return
        try:
            clients.rename(old, new)
        except ValueError as exc:
            messagebox.showerror("단체", str(exc), parent=self)
            return
        self.hub.announce("client")

    def remove_client(self) -> None:
        from .. import clients

        name = self.client_table.selected()
        if not name:
            return
        if not messagebox.askyesno(
                "단체 삭제", f"'{name}' 을(를) 지웁니다.\n"
                             "안에 저장된 산출이 함께 사라집니다. 계속할까요?", parent=self):
            return
        try:
            clients.remove(name, force=True)
        except ValueError as exc:
            messagebox.showerror("단체", str(exc), parent=self)
            return
        self.hub.announce("client")

    # ── 등록 자료 ───────────────────────────────────────────────

    def _assets(self, parent: tk.Misc) -> None:
        from ..library import CURVE_KIND, PRESET_KIND, RATES_KIND, ROSTER_KIND

        box = section(parent, "시스템 등록 자료")
        bar = ttk.Frame(box)
        bar.pack(fill="x")
        ttk.Label(bar, text="종류").pack(side="left")
        self.kind.set(CURVE_KIND)
        for kind in (CURVE_KIND, RATES_KIND, ROSTER_KIND, PRESET_KIND):
            ttk.Radiobutton(bar, text=kind, value=kind, variable=self.kind,
                            command=self.refresh).pack(side="left", padx=(6, 6))

        self.asset_table = Table(box, ["이름", "등록 시각", "원본 파일", "기본"],
                                 widths=[220, 130, 260, 60],
                                 aligns=["w", "w", "w", "c"], height=8)
        self.asset_table.pack(fill="x", pady=(8, 0))

        bar = ttk.Frame(box)
        bar.pack(fill="x", pady=(8, 0))
        for label, command in (("등록", self.register_asset), ("기본으로", self.pin_asset),
                               ("삭제", self.remove_asset), ("폴더 열기", self.open_folder)):
            ttk.Button(bar, text=label, command=command).pack(side="left", padx=(0, 6))
        self.asset_status = ttk.Label(box, text="", style="Hint.TLabel", justify="left")
        self.asset_status.pack(anchor="w", pady=(6, 0))
        labeled(box, "여기 등록한 명부·가정세트는 [산출] 탭의 목록에서, 표준률·금리표는 "
                     "[산출가정 입력] 탭의 목록에서 바로 골라 쓸 수 있습니다.")

    def refresh(self) -> None:
        from ..library import entries, resolve_default

        self._fill_clients()
        kind = self.kind.get()
        default = resolve_default(kind)
        default_name = default.name if default else ""
        found = entries(kind)
        self.asset_table.fill(
            [(entry.name, str(entry.registered or ""), entry.path.name,
              "●" if entry.name == default_name else "")
             for entry in found],
            keys=lambda _i, row: str(row[0]))
        self.asset_status.configure(
            text=f"{kind} — 등록 {len(found)}건." +
                 (f"  기본: {default_name}" if default_name else "  기본 지정 없음 (가장 최근 등록을 씁니다)"),
            style="Hint.TLabel")

    def register_asset(self) -> None:
        from ..library import register

        kind = self.kind.get()
        path = filedialog.askopenfilename(title=f"{kind} 파일 선택", filetypes=_EXCEL,
                                          parent=self)
        if not path:
            return
        name = simpledialog.askstring("등록 이름", "목록에 표시할 이름을 정하세요.",
                                      parent=self, initialvalue=Path(path).stem)
        if not name:
            return
        try:
            register(kind, path, name=name)
        except Exception as exc:
            messagebox.showerror("등록", f"등록하지 못했습니다.\n\n{exc}", parent=self)
            return
        self.hub.announce("library")

    def pin_asset(self) -> None:
        from ..library import read_settings, write_settings

        name = self.asset_table.selected()
        if not name:
            return
        settings = read_settings()
        settings[self.kind.get()] = name
        write_settings(settings)
        self.hub.announce("library")

    def remove_asset(self) -> None:
        from ..library import remove

        name = self.asset_table.selected()
        if not name:
            return
        if not messagebox.askyesno("삭제", f"등록 자료 '{name}' 을(를) 지웁니다. 계속할까요?",
                                   parent=self):
            return
        try:
            remove(self.kind.get(), name)
        except Exception as exc:
            messagebox.showerror("삭제", str(exc), parent=self)
            return
        self.hub.announce("library")

    def open_folder(self) -> None:
        from ..library import library_dir

        reveal(library_dir())

    # ── 시험 명부 ───────────────────────────────────────────────

    def _generator(self, parent: tk.Misc) -> None:
        from ..rostergen import CASES

        box = section(parent, "시험 명부 만들기 (난수)")
        bar = ttk.Frame(box)
        bar.pack(fill="x")
        ttk.Label(bar, text="난수 씨앗").pack(side="left")
        ttk.Entry(bar, textvariable=self.seed, width=12).pack(side="left", padx=(6, 14))
        ttk.Label(bar, text="산출기준일").pack(side="left")
        ttk.Entry(bar, textvariable=self.gen_base, width=13).pack(side="left", padx=(6, 14))
        ttk.Button(bar, text="만들기", command=self.generate).pack(side="left")
        ttk.Button(bar, text="만든 것을 목록에 등록",
                   command=self.register_cases).pack(side="left", padx=(6, 0))

        self.case_table = Table(box, ["사례", "설명"], widths=[150, 560],
                                aligns=["w", "w"], height=len(CASES), stretch=1)
        self.case_table.pack(fill="x", pady=(8, 0))
        self.case_table.fill([(spec.title, spec.summary) for spec in CASES])
        self.gen_status = ttk.Label(box, text="", style="Hint.TLabel", justify="left")
        self.gen_status.pack(anchor="w", pady=(6, 0))
        labeled(box, "실제 개인정보 대신 쓰는 시험 자료입니다. 같은 씨앗을 넣으면 언제나 같은 "
                     "명부가 나오므로, 문제가 났을 때 그 명부를 그대로 다시 만들 수 있습니다. "
                     "명부·기초율·특이사항 안내문이 한 벌로 나옵니다.")

    def _gen_folder(self) -> Path:
        from ..library import library_dir

        folder = library_dir() / "시험명부"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def generate(self) -> None:
        from ..rostergen import write_case_pack
        from .calc import parse_date

        try:
            seed = int(self.seed.get().strip() or "20251231")
        except ValueError:
            self.gen_status.configure(text="난수 씨앗은 숫자여야 합니다.", style="Bad.TLabel")
            return
        base = parse_date(self.gen_base.get())
        folder = self._gen_folder()
        self.gen_status.configure(text="만드는 중…", style="Hint.TLabel")
        self.update_idletasks()
        try:
            made = write_case_pack(folder, seed=seed, base_date=base)
        except Exception as exc:
            self.gen_status.configure(text=f"만들지 못했습니다: {exc}", style="Bad.TLabel")
            return
        self.gen_status.configure(
            text=f"{len(made)}개 파일을 만들었습니다: {folder}", style="Good.TLabel")
        reveal(folder)

    def register_cases(self) -> None:
        from ..library import PRESET_KIND, ROSTER_KIND, register
        from ..rostergen import CASES

        folder = self._gen_folder()
        made = []
        for spec in CASES:
            roster = folder / f"{spec.title}.xlsx"
            assumptions = folder / f"{spec.title}_기초율.xlsx"
            if not roster.exists():
                self.gen_status.configure(text="먼저 [만들기] 를 누르세요.", style="Bad.TLabel")
                return
            register(ROSTER_KIND, roster, name=spec.title)
            if assumptions.exists():
                register(PRESET_KIND, assumptions, name=f"{spec.title}_기초율")
            made.append(spec.title)
        self.hub.announce("library")
        self.gen_status.configure(
            text="목록에 등록했습니다 — " + " · ".join(made) +
                 ".  [산출] 탭의 [저장된 명부] 에서 바로 고를 수 있습니다.",
            style="Good.TLabel")

    # ── 보관함 ──────────────────────────────────────────────────

    def _backup(self, parent: tk.Misc) -> None:
        box = section(parent, "보관함 — 이 컴퓨터 밖에 두는 사본")
        bar = ttk.Frame(box)
        bar.pack(fill="x")
        ttk.Button(bar, text="보관함 내보내기 (.zip)", command=self.export).pack(side="left")
        ttk.Button(bar, text="보관함에서 되살리기",
                   command=self.restore).pack(side="left", padx=(6, 0))
        self.backup_status = ttk.Label(box, text="", style="Hint.TLabel", justify="left")
        self.backup_status.pack(anchor="w", pady=(6, 0))
        labeled(box, "등록 자료와 산출 내역 전체가 zip 하나로 나옵니다. 컴퓨터를 바꾸거나 "
                     "다시 설치할 때 그 파일 하나면 그대로 돌아옵니다. 회사 공용 폴더나 "
                     "외장 디스크처럼 이 컴퓨터 밖에 두세요.")

    def export(self) -> None:
        import zipfile

        from ..library import library_dir

        home = library_dir()
        stamp = _dt.datetime.now().strftime("%Y%m%d")
        path = filedialog.asksaveasfilename(
            title="보관함 내보내기", defaultextension=".zip",
            initialfile=f"연금계리보관함_{stamp}.zip",
            filetypes=[("압축 파일", "*.zip")], parent=self)
        if not path:
            return
        import json

        (home / "연금계리보관함.json").write_text(
            json.dumps({"만든날짜": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "형식": 1}, ensure_ascii=False), encoding="utf-8")
        target = Path(path)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for item in sorted(home.rglob("*")):
                if item.is_file():
                    archive.write(item, item.relative_to(home).as_posix())
        self.backup_status.configure(
            text=f"{target}  ({target.stat().st_size:,} 바이트)", style="Good.TLabel")

    def restore(self) -> None:
        import zipfile

        from ..library import library_dir

        path = filedialog.askopenfilename(
            title="보관함 파일 선택", filetypes=[("압축 파일", "*.zip")], parent=self)
        if not path:
            return
        home = library_dir()
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                if "연금계리보관함.json" not in names:
                    raise ValueError("이 프로그램에서 내보낸 보관함 파일이 아닙니다")
                for name in names:
                    if name.endswith("/"):
                        continue
                    target = (home / name).resolve()
                    if not str(target).startswith(str(home.resolve())):
                        # zip 안의 경로가 보관함 밖을 가리키면 그 줄은 버린다.
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(name))
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            self.backup_status.configure(text=f"되살리지 못했습니다: {exc}", style="Bad.TLabel")
            return
        self.hub.announce("library")
        self.hub.announce("client")
        self.backup_status.configure(text="보관함에서 되살렸습니다.", style="Good.TLabel")
