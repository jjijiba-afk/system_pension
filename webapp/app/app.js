"use strict";
/* 연금계리 산출 웹앱.
 *
 * 계산은 전부 Pyodide 위의 pension 패키지가 한다. 이 파일은 표를 그리고,
 * 입력을 state(문자열 dict)로 모아 pension.webui.api 에 JSON 으로 넘길 뿐이다.
 * 화면 규칙(시트 구성·선택지)은 api 의 meta 응답에서 받아온다 — 파이썬과
 * 자바스크립트에 같은 목록을 두 번 적으면 반드시 어긋난다.
 *
 * 등록 자료(금리표·표준률)는 /pension-home 을 IndexedDB(IDBFS)에 마운트해
 * 새로고침해도 남는다. 명부·기초율 업로드는 메모리 파일시스템(/work)에만 쓴다.
 */

const $ = (id) => document.getElementById(id);
const status = (msg) => { $("status").textContent = msg; };
const EDITOR_STORE = "pension.editor.state.v1";

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}

let pyodide = null;
let pyApi = null;
let META = null;

// ── 파이썬 경계 ──────────────────────────────────────────────────
function py(op, args = {}) {
  const response = JSON.parse(pyApi(JSON.stringify({ op, ...args })));
  if (!response.ok) throw new Error(response.error);
  return response;
}

async function intoFS(file, path) {
  pyodide.FS.writeFile(path, new Uint8Array(await file.arrayBuffer()));
  return path;
}

const persistHome = () => new Promise((done) => pyodide.FS.syncfs(false, done));

const XLSX_MIME =
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

function download(path, filename, mime = XLSX_MIME) {
  const payload = pyodide.FS.readFile(path);
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([payload], { type: mime }));
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 30000);
}

// ── 부팅 ────────────────────────────────────────────────────────
async function boot() {
  try {
    status("엔진을 준비하는 중… (1/3 런타임)");
    pyodide = await loadPyodide({ indexURL: new URL("pyodide/", location.href).href });

    // 등록 자료 폴더를 IndexedDB 에 물린다. 새로고침해도 남아야 한다.
    pyodide.FS.mkdirTree("/pension-home");
    pyodide.FS.mount(pyodide.FS.filesystems.IDBFS, {}, "/pension-home");
    await new Promise((done) => pyodide.FS.syncfs(true, done));

    status("엔진을 준비하는 중… (2/3 계산 모듈)");
    pyodide.FS.mkdirTree("/wheels");
    pyodide.FS.mkdirTree("/work");
    for (const wheel of WHEELS) {
      const payload = await (await fetch("wheels/" + wheel)).arrayBuffer();
      pyodide.FS.writeFile("/wheels/" + wheel, new Uint8Array(payload));
    }
    // 순수 파이썬 휠은 압축을 풀어 놓기만 하면 된다. micropip 없이도 확정적이다.
    await pyodide.runPythonAsync(`
import os, sys, zipfile
from pathlib import Path
site = next(p for p in sys.path if 'site-packages' in p)
for wheel in Path('/wheels').glob('*.whl'):
    zipfile.ZipFile(wheel).extractall(site)
os.environ['PENSION_HOME'] = '/pension-home'
from pension.webui import api
`);
    status("엔진을 준비하는 중… (3/3 화면 구성)");
    pyApi = pyodide.globals.get("api");
    META = py("meta");

    buildEditor();
    const saved = localStorage.getItem(EDITOR_STORE);
    renderState(saved ? JSON.parse(saved) : py("state_new").state);
    refreshLibrary();
    refreshRuns();

    status("준비 완료. 명부를 고르고 기초율을 정한 뒤 [산출 실행]을 누르세요.");
    $("run").disabled = false;
  } catch (error) {
    status("엔진을 준비하지 못했습니다: " + error);
  }
}
boot();

// ── 큰 탭 ────────────────────────────────────────────────────────
const PAGES = [["tab-calc", "page-calc"], ["tab-edit", "page-edit"],
               ["tab-lib", "page-lib"], ["tab-runs", "page-runs"],
               ["tab-gen", "page-gen"]];
for (const [tab, page] of PAGES) {
  $(tab).addEventListener("click", () => {
    // 탭을 옮기기 전에 편집 중이던 가정을 먼저 확정 저장한다. 디바운스만
    // 믿으면 마지막 몇 초의 입력이 사라진다 — 아이패드는 배경으로 밀린 페이지를
    // 통째로 버리기도 한다.
    flushEditor();
    for (const [t, p] of PAGES) {
      $(t).classList.toggle("on", t === tab);
      $(p).classList.toggle("on", p === page);
    }
  });
}

// 앱이 배경으로 가거나 닫힐 때도 저장한다.
for (const event of ["pagehide", "beforeunload"]) {
  window.addEventListener(event, flushEditor);
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") flushEditor();
});

// ═════════ 산출가정 편집기 ═══════════════════════════════════════
// 화면이 곧 자료다: DOM 의 입력 값을 collectState() 로 모으고,
// renderState() 로 되그린다. 직군이 바뀌면 state 를 고쳐 되그린다.

let groups = [];            // 현재 직군 묶음
let gridBodies = {};        // 시트명 → {spec, keySelect, tbody, table}
let payoutBody = null;      // 지급규정 tbody — 행마다 위젯 참조를 붙인다
let mapData = [];           // 직군 매핑 행 [{source, kind, normalized, active, retired, target, suggest}]
const MIN_ROWS = 8;

/** (명부직군, 임직원구분) 짝을 Map 키로. 이름에 무엇이 들어와도 겹치지 않게 JSON 으로. */
const pairKey = (source, kind) => JSON.stringify([source || "", kind || ""]);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children) {
    node.append(child);
  }
  return node;
}

function makeSelect(options, value) {
  const box = el("select");
  for (const option of options) box.append(el("option", {}, option));
  if (value !== undefined) box.value = value;
  return box;
}

function buildEditor() {
  const tabs = $("ed-subtabs");
  const pages = $("ed-subpages");
  tabs.innerHTML = "";
  pages.innerHTML = "";

  const addTab = (name, builder) => {
    const page = el("div", { class: "subpage" });
    builder(page);
    pages.append(page);
    const button = el("button", {
      onclick: () => {
        [...tabs.children].forEach((b) => b.classList.toggle("on", b === button));
        [...pages.children].forEach((p) => p.classList.toggle("on", p === page));
      },
    }, name);
    tabs.append(button);
    return button;
  };

  // 규정이 늘 때마다 탭을 붙였더니 열세 개가 되어 아이패드에서 가로로 밀어야
  // 했다. 이제 네 묶음으로 접고, 한 묶음 안의 표는 세로로 쌓아 펼쳐 본다.
  // 어떤 표가 어느 묶음에 들어가는지는 파이썬(EDITOR_GROUPS)이 정한다.
  const specs = Object.fromEntries(META.sheets.map((s) => [s.sheet, s]));
  for (const group of META.editor_groups) {
    addTab(group.name, (page) => {
      if (group.note) page.append(el("div", { class: "hint" }, group.note));
      group.sections.forEach((section, index) =>
        buildSection(page, section, specs, index === 0));
    });
  }
  tabs.firstChild.classList.add("on");
  pages.firstChild.classList.add("on");

  $("ed-grade").replaceChildren(...META.grades.map((g) => el("option", {}, g)));
  $("ed-grade").value = "AA0";

  // 입력이 바뀌면 잠시 뒤 브라우저에 임시 저장한다. 탭을 닫아도 살아 있도록.
  // 칸을 빠져나가거나(change) 목록을 고르면 기다리지 않고 바로 저장한다.
  $("page-edit").addEventListener("input", scheduleEditorSave);
  $("page-edit").addEventListener("change", flushEditor);
  $("page-edit").addEventListener("focusout", flushEditor);
}

/** 패널 이름 → 그리는 함수. 파이썬이 이름만 넘기고 그리기는 여기서 한다. */
const SECTION_PANELS = {
  map: (page) => buildMapTab(page),
  payout: (page) => buildPayoutTab(page),
  cause: (page) => buildCauseTab(page),
};

let saveTimer = null;

function scheduleEditorSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveEditorLocal, 800);
}

/** 예약된 저장을 기다리지 않고 지금 저장한다. */
function flushEditor() {
  clearTimeout(saveTimer);
  saveEditorLocal();
}

function saveEditorLocal() {
  if (!META || !payoutBody) return;   // 화면이 아직 만들어지기 전
  try {
    localStorage.setItem(EDITOR_STORE, JSON.stringify(collectState()));
    syncEditorHint();
  } catch { /* 저장 공간 부족은 치명적이지 않다 */ }
}

// ── 표 입력 탭 ──
/** 묶음 안의 접이식 구획 하나. 표는 시트 이름으로, 나머지는 패널 이름으로 찾는다. */
function buildSection(page, section, specs, first) {
  const body = el("div", { class: "section-body" });
  // 접힌 구획도 채워졌는지는 보여야 한다. 제목 옆 숫자가 그 몫이다 —
  // 없으면 무엇을 아직 안 넣었는지 하나씩 열어 봐야 안다.
  const count = el("span", { class: "count" });
  const attrs = {
    class: "section",
    // 시험(e2e)이 구획을 이름으로 집을 수 있게 표시를 남긴다. 제목은 문구가
    // 바뀌지만 시트·패널 이름은 자료 구조와 함께 움직인다.
    "data-section": section.sheet || section.panel || "",
  };
  if (first) attrs.open = "";
  page.append(el("details", attrs,
                 el("summary", {}, section.title, count), body));
  if (section.sheet) {
    const spec = specs[section.sheet];
    if (spec) {
      buildGridTab(body, spec);
      gridBodies[spec.sheet].countEl = count;
    }
    return;
  }
  const build = SECTION_PANELS[section.panel];
  if (build) build(body);
}

function buildGridTab(page, spec) {
  const toolbar = el("div", { class: "toolbar" });
  let keySelect = null;
  if (spec.key_choices.length) {
    keySelect = makeSelect(spec.key_choices, spec.key);
    keySelect.addEventListener("change", () => relabelGrid(spec.sheet));
    toolbar.append(el("span", { style: "font-weight:600" }, "조회 기준"), keySelect);
  }
  toolbar.append(
    el("button", { class: "small", type: "button", onclick: () => addGridRow(spec.sheet) }, "행 추가"),
    el("button", { class: "small", type: "button", onclick: () => compactGrid(spec.sheet) }, "빈 행 정리"),
  );
  if (spec.allow_extra) {
    toolbar.append(el("button", { class: "small", type: "button",
                                  onclick: () => addGridColumn(spec.sheet) }, "항목 추가"));
  }
  if (spec.column_panel === "benefit") {
    toolbar.append(el("button", { class: "small", type: "button",
                                  onclick: previewFormulas }, "수식 미리보기"));
  }
  if (spec.column_panel === "longterm") {
    toolbar.append(el("button", { class: "small", type: "button",
                                  onclick: () => toggleDetailRows(spec.sheet) },
                      "세부 설정"));
  }
  const table = el("table", { class: "grid" });
  const tbody = el("tbody");
  table.append(tbody);
  page.append(toolbar, el("div", { class: "hint" }, "· " + spec.note),
              el("div", { class: "scroll-x" }, table));
  if (spec.extra_hint) page.append(el("div", { class: "hint" }, "· " + spec.extra_hint));
  if (spec.column_panel === "benefit") page.append(...benefitGuide());
  if (spec.column_panel === "longterm") page.append(...longtermGuide());
  gridBodies[spec.sheet] = { spec, keySelect, tbody, extra: [], columnWidgets: {} };
}

function benefitGuide() {
  const vars = Object.entries(META.formula_variables)
    .map(([k, v]) => `${k}=${v}`).join(" · ");
  return [
    el("div", { class: "hint", style: "white-space:pre-line" },
       "누적  표의 값이 그 근속연수의 누적 배수입니다. (10년 → 10.0)\n" +
       "누진  표의 값이 그 구간에서만 적용할 연 배수입니다. " +
       "0년 1.0 / 5년 1.5 / 10년 2.0 이면 근속 12년 = 5×1.0 + 5×1.5 + 2×2.0 = 16.5\n" +
       "수식  방식을 '수식' 으로 두고 그 아래 칸에 직접 씁니다."),
    el("div", { class: "hint" }, "변수  " + vars),
    el("div", { class: "hint" }, "함수  " + META.formula_functions.join(" ")),
    el("div", { class: "hint" },
       '예시  =IF(t<10, t*1.0, 10 + (t-10)*2.0)     =IF(제도="DB", t*1.5, t)     =MIN(t, 30)'),
  ];
}

function longtermGuide() {
  return [
    el("div", { class: "hint", style: "white-space:pre-line" },
       "휴가        표 값 = 지급일수 → 일 기본급 × 일수 (임금상승률 반영)\n" +
       "평균임금    표 값 = 배수 → 30일 평균임금 × 배수 (임금상승률 반영)\n" +
       "현물        표 값 = 정액(원) → 평가시점 시세로 환산해 넣고 현물 상승률로 올림\n" +
       "현금        표 값 = 정액(원) → 규정 금액이 고정이므로 올리지 않음"),
    el("div", { class: "hint", style: "white-space:pre-line" },
       "근속도달    재직 중 그 근속에 닿는 해에 줍니다.\n" +
       "퇴직시      나갈 때 줍니다. 중도퇴직자도 받습니다.\n" +
       "정년시      정년퇴직자만 받습니다.\n" +
       "누적        켜면 도달한 지급 시점의 값을 모두 더합니다 (소멸기한 없는 휴가).\n" +
       "반복 주기   마지막 지급 이후 되풀이하는 주기 (건강검진 2년마다 → 2).\n" +
       "지급일      창립기념일처럼 날짜가 와야 줄 때 (10-01). '근속도달' 에만 씁니다."),
  ];
}

/** 그 표의 열 이름들 — 직군 다음에 따로 만든 열. 파이썬 grid_columns() 와 같다. */
function gridColumns(sheet) {
  const grid = gridBodies[sheet];
  if (grid.spec.fixed.length) return [...grid.spec.fixed];
  const extra = (grid.extra || []).filter((n) => n && !groups.includes(n));
  return [...groups, ...new Set(extra)];
}

function addGridColumn(sheet) {
  const name = prompt("항목 이름을 정하세요. (예: 사망가산, 금, 특별상여)", "");
  if (name === null) return;
  const clean = name.trim();
  if (!clean) return;
  if (gridColumns(sheet).includes(clean)) { alert("이미 있는 이름입니다."); return; }
  const state = collectState();
  state.grids[sheet].extra = [...(state.grids[sheet].extra || []), clean];
  renderState(state);
  saveEditorLocal();
}

function removeGridColumn(sheet, name) {
  if (!confirm(`'${name}' 열을 지웁니다. 그 열의 값도 함께 사라집니다.`)) return;
  const state = collectState();
  state.grids[sheet].extra = (state.grids[sheet].extra || []).filter((n) => n !== name);
  renderState(state);
  saveEditorLocal();
}

function renderGrid(sheet, key, rows, extra, columnValues) {
  const grid = gridBodies[sheet];
  if (grid.keySelect) grid.keySelect.value = key || grid.spec.key;
  grid.extra = [...(extra || [])];
  const columns = gridColumns(sheet);
  const keyLabel = grid.keySelect ? grid.keySelect.value : grid.spec.key;

  const head = el("tr", {}, el("th", {}, keyLabel));
  for (const name of columns) {
    const cell = el("th", {}, name);
    // 따로 만든 열만 지울 수 있다. 직군 열은 [직군별 규정] 에서 다룬다.
    if (!groups.includes(name)) {
      cell.append(el("button", { class: "link", type: "button", title: "열 삭제",
                                 onclick: () => removeGridColumn(sheet, name) }, "×"));
    }
    head.append(cell);
  }
  grid.tbody.replaceChildren(head);

  // 열 머리 아래에 그 열을 **어떻게 읽을지** 를 붙인다. 표만 떼어 놓고 보면
  // 값이 누적 배수인지 휴가 일수인지 알 수 없어 늘 다른 탭과 번갈아 봐야 했다.
  grid.columnWidgets = {};
  const panel = COLUMN_PANELS[grid.spec.column_panel];
  if (panel) panel(grid, columns, columnValues || {});

  const count = Math.max(rows.length + 2, MIN_ROWS);
  for (let r = 0; r < count; r += 1) {
    // 값 줄에만 표시를 남긴다 — 열 머리 패널도 <tr> 이라 그냥 세면 섞인다.
    grid.tbody.append(el("tr", { "data-row": "" },
      ...[keyLabel, ...columns].map((_, c) =>
        el("td", {}, el("input", { type: "text", value: (rows[r] || [])[c] || "" })))));
  }
}

function relabelGrid(sheet) {
  const grid = gridBodies[sheet];
  grid.tbody.querySelector("th").textContent = grid.keySelect.value;
}

function gridRows(sheet) {
  const rows = [];
  for (const tr of gridBodies[sheet].tbody.querySelectorAll("tr[data-row]")) {
    const values = [...tr.querySelectorAll("input")].map((i) => i.value.trim());
    if (values.some(Boolean)) rows.push(values);
  }
  syncDetailRows(gridBodies[sheet]);
  const badge = gridBodies[sheet].countEl;
  if (badge) badge.textContent = rows.length ? `${rows.length}줄` : "비어 있음";
  return rows;
}

/** 접힌 구획의 제목 옆 숫자를 지금 값으로 맞춘다. */
function refreshSectionCounts() {
  for (const sheet of Object.keys(gridBodies)) gridRows(sheet);
}

function addGridRow(sheet) {
  const grid = gridBodies[sheet];
  const width = grid.tbody.firstChild.children.length;
  grid.tbody.append(el("tr", { "data-row": "" },
    ...Array.from({ length: width }, () => el("td", {}, el("input", { type: "text" })))));
}

function compactGrid(sheet) {
  const grid = gridBodies[sheet];
  renderGrid(sheet, grid.keySelect ? grid.keySelect.value : grid.spec.key, gridRows(sheet));
}

// ── 지급규정 탭 ──
function buildPayoutTab(page) {
  const toolbar = el("div", { class: "toolbar" },
    el("button", { class: "small", type: "button", onclick: loadGeneralInfo },
       "명부 일반사항에서 규정 읽어오기"),
    el("span", { class: "hint", id: "payout-evidence" }));
  const table = el("table", { class: "grid" });
  payoutBody = el("tbody");
  table.append(payoutBody);
  page.append(
    el("div", { class: "hint" },
       "자료요청서 '1)일반사항' 6번(퇴직금 지급규정)을 여기에 옮깁니다. " +
       "Base-up·승급률·퇴직률·사망률을 '미반영'으로 두면 그 직군에서 해당 요율을 0으로 봅니다 " +
       "— 임원을 정년까지 근무한다고 보는 경우 등."),
    toolbar, el("div", { class: "scroll-x" }, table));
}

function renderPayout(payout) {
  const headers = ["직군", "산출 제외", "가입자격(년)", "정년(직원)", "정년(임원)",
    "가산연령", "근속 산정", "단수 처리", "지급액 반올림",
    "Base-up", "승급률", "퇴직률", "사망률"];
  payoutBody.replaceChildren(el("tr", {}, ...headers.map((h) => el("th", {}, h))));
  for (const group of groups) {
    const item = payout[group] || {};
    const row = {
      excluded: el("input", { type: "checkbox" }),
      min_service: el("input", { type: "text", value: item.min_service ?? "1" }),
      nra: el("input", { type: "text", value: item.nra ?? "60" }),
      executive_nra: el("input", { type: "text", value: item.executive_nra ?? "60" }),
      add_age: el("input", { type: "text", value: item.add_age ?? "2" }),
      basis: makeSelect(META.service_bases, item.basis || META.service_bases[0]),
      fraction: makeSelect(META.fraction_modes, item.fraction || META.fraction_modes[0]),
      unit: makeSelect(META.rounding_units, item.unit || "없음"),
      base_up: makeSelect(META.apply_choices, item.base_up || "반영"),
      promotion: makeSelect(META.apply_choices, item.promotion || "반영"),
      withdrawal: makeSelect(META.apply_choices, item.withdrawal || "반영"),
      mortality: makeSelect(META.apply_choices, item.mortality || "반영"),
    };
    row.excluded.checked = Boolean(item.excluded);
    const tr = el("tr", {},
      el("td", { class: "name" }, group),
      ...Object.values(row).map((widget) => el("td", {}, widget)));
    tr.dataset.group = group;
    tr.widgets = row;
    payoutBody.append(tr);
  }
}

function payoutValues() {
  const result = {};
  for (const tr of [...payoutBody.children].slice(1)) {
    const w = tr.widgets;
    result[tr.dataset.group] = {
      excluded: w.excluded.checked, min_service: w.min_service.value.trim(),
      nra: w.nra.value.trim(), executive_nra: w.executive_nra.value.trim(),
      add_age: w.add_age.value.trim(), basis: w.basis.value,
      fraction: w.fraction.value, unit: w.unit.value,
      base_up: w.base_up.value, promotion: w.promotion.value,
      withdrawal: w.withdrawal.value, mortality: w.mortality.value,
    };
  }
  return result;
}

async function loadGeneralInfo() {
  try {
    const path = await rosterIntoFS();
    const info = py("general_info", { path });
    const state = collectState();
    for (const group of groups) {
      Object.assign(state.payout[group] ||= {}, info.values);
    }
    renderState(state);
    const lines = Object.entries(info.evidence).map(([k, v]) => `${k}: ${v}`);
    if (info.unread.length) lines.push("확인 필요: " + info.unread.join(", "));
    $("payout-evidence").textContent = lines.join("  |  ") || "읽어낸 항목이 없습니다.";
    if (info.unread.length) {
      alert("규정에서 읽지 못한 항목이 있습니다. 직접 채워 주세요.\n\n· "
            + info.unread.join("\n· "));
    }
  } catch (error) {
    alert("일반사항을 읽지 못했습니다.\n\n" + error.message);
  }
}

// ── 열 머리 패널 ──
// 표 값이 무엇을 뜻하는지는 열마다 다르다. 그것을 다른 탭에 떼어 두었더니
// 표를 보면서 늘 번갈아 봐야 했다. 열 머리 바로 아래에 붙인다.

/** 패널 한 줄. 첫 칸은 이름표, 나머지는 열마다 하나씩.
 *
 * ``detail`` 로 표시한 줄은 [세부 설정] 을 눌러야 보인다. 대부분의 규정은
 * 위 두세 줄만 쓰는데 여덟 줄을 늘 펼쳐 두면 정작 값을 넣을 표가 밀린다.
 */
function panelRow(label, columns, make, detail = false) {
  const tr = el("tr", { class: detail ? "panel detail" : "panel" },
                el("td", { class: "name" }, label));
  for (const name of columns) tr.append(el("td", {}, make(name)));
  return tr;
}

/** 세부 줄에 값이 하나라도 있으면 저절로 펼친다 — 숨은 채로 걸려 있으면 안 된다. */
function syncDetailRows(grid) {
  const filled = [...grid.tbody.querySelectorAll("tr.detail")].some((tr) =>
    [...tr.querySelectorAll("input, select")].some(
      (w) => (w.type === "checkbox" ? w.checked : w.value.trim())));
  grid.tbody.classList.toggle("show-detail", filled || grid.showDetail === true);
}

function toggleDetailRows(sheet) {
  const grid = gridBodies[sheet];
  grid.showDetail = !grid.tbody.classList.contains("show-detail");
  syncDetailRows(grid);
}

/** 지급률 — 누적·누진·수식과 그 수식. */
function benefitColumnPanel(grid, columns, values) {
  const widgets = grid.columnWidgets;
  const states = {};
  const syncAll = () => {
    for (const name of columns) {
      const w = widgets[name];
      const isFormula = w.mode.value === "수식";
      w.formula.disabled = !isFormula;
      const mark = states[name];
      if (!isFormula) { mark.textContent = ""; mark.className = "hint"; continue; }
      const source = w.formula.value.trim();
      if (!source) { mark.textContent = "수식 필요"; mark.className = "bad-text"; continue; }
      try {
        const check = py("formula_check", { source });
        mark.textContent = check.error ? "✕ " + check.error : "✓";
        mark.className = check.error ? "bad-text" : "ok-text";
      } catch { /* 부팅 전이면 그냥 둔다 */ }
    }
  };

  grid.tbody.append(panelRow("방식", columns, (name) => {
    const item = values[name] || {};
    const mode = makeSelect(META.benefit_modes, item.mode || META.benefit_modes[0]);
    mode.addEventListener("change", syncAll);
    (widgets[name] ||= {}).mode = mode;
    return mode;
  }));
  grid.tbody.append(panelRow("수식", columns, (name) => {
    const item = values[name] || {};
    const box = el("input", { type: "text", value: item.formula || "",
                              style: "text-align:left" });
    box.addEventListener("input", syncAll);
    widgets[name].formula = box;
    // 확인 표시는 수식 칸에 붙인다. 줄을 따로 두면 표 방식일 때 통째로 빈다.
    const mark = el("span", { class: "hint" });
    states[name] = mark;
    return el("div", { class: "with-mark" }, box, mark);
  }));
  syncAll();
}

function benefitSheet() {
  return META.sheets.find((s) => s.column_panel === "benefit").sheet;
}

function ruleValues() {
  const result = {};
  const grid = gridBodies[benefitSheet()];
  for (const [name, w] of Object.entries(grid.columnWidgets)) {
    result[name] = { mode: w.mode.value, formula: w.formula.value.trim() };
  }
  return result;
}

function previewFormulas() {
  const formulas = {};
  for (const [group, item] of Object.entries(ruleValues())) {
    if (item.mode === "수식" && item.formula) formulas[group] = item.formula;
  }
  if (!Object.keys(formulas).length) {
    alert("수식 방식으로 지정된 규정이 없습니다.");
    return;
  }
  try {
    const { preview } = py("formula_preview", { formulas });
    const table = el("table", { class: "data" },
      el("tr", {}, ...preview.columns.map((c) => el("th", {}, c))),
      ...preview.rows.map((row) => el("tr", {},
        ...row.map((v, i) => el("td", { class: i ? "num" : "" }, v)))));
    $("preview-body").replaceChildren(table);
    $("preview-dialog").showModal();
  } catch (error) {
    alert(error.message);
  }
}

// ── 퇴직사유 탭 ──
// 자료요청서 6번에 '중도퇴직시 / 사망시 / 정년퇴직시 퇴직금 지급률' 이 따로
// 있고, 실제로 다르게 적어 오는 회사가 많다. 비워 두면 사유를 가리지 않는다.
let causeBody = null;
const CAUSE_MIN_ROWS = 4;

function buildCauseTab(page) {
  const guide =
    "중도퇴직·사망·정년퇴직의 지급이 다를 때만 채웁니다. 비우면 사유를 가리지 않습니다.\n" +
    "대체 지급률 규정  그 사유일 때 기본 규정 대신 쓸 '지급률' 탭의 열 이름 " +
    "(예: 정년퇴직만 대표이사 3배)\n" +
    "가산 규정        기본 급여에 더할 배수를 내는 규정 " +
    "(예: 사망 시 근속 10년 미만 3개월분 / 이상 5개월분)\n" +
    "가산액(원)       정액 가산 (예: 유족지원금 50,000,000)\n" +
    "근속 하한(년)     '사망 시 1년 미만도 1년으로 계산' 처럼 짧은 근속을 끌어올릴 때\n" +
    "가산 귀속        즉시=근속이 늘어도 안 느는 급여라 지금 전액 귀속, " +
    "근속비례=근속에 따라 쌓음\n" +
    "                비우면 사망은 '즉시', 나머지는 '근속비례' 입니다.";
  const table = el("table", { class: "grid" });
  causeBody = el("tbody");
  table.append(causeBody);
  page.append(
    el("div", { class: "hint", style: "white-space:pre-line" }, guide),
    el("div", { class: "scroll-x" }, table),
    el("div", { class: "toolbar" },
       el("button", { class: "small", type: "button",
                      onclick: () => { causeBody.append(causeRow([])); } },
          "줄 추가")));
}

function causeRow(values) {
  // 지급률 표의 **모든 열** 을 고를 수 있어야 한다. 직군만 고를 수 있으면
  // '사망 시 기본급 3개월분 가산' 같은 별도 지급률을 가리킬 수가 없다.
  const scales = gridColumns(benefitSheet());
  const rule = makeSelect(["", ...scales], values[0] || "");
  const cause = makeSelect(["", ...META.exit_causes], values[1] || "");
  const alt = makeSelect(["", ...scales], values[2] || "");
  const extraRule = makeSelect(["", ...scales], values[3] || "");
  const amount = el("input", { type: "text", value: values[4] || "" });
  const floor = el("input", { type: "text", value: values[5] || "" });
  const basis = makeSelect(["", ...META.attributions], values[6] || "");
  const tr = el("tr", {}, ...[rule, cause, alt, extraRule, amount, floor, basis]
    .map((w) => el("td", {}, w)));
  tr.widgets = { rule, cause, alt, extraRule, amount, floor, basis };
  return tr;
}

function renderCauses(rows) {
  causeBody.replaceChildren(el("tr", {},
    ...META.exit_cause_headers.map((h) => el("th", {}, h))));
  const filled = rows.length ? rows : [];
  for (const row of filled) causeBody.append(causeRow(row));
  for (let i = filled.length; i < CAUSE_MIN_ROWS; i += 1) {
    causeBody.append(causeRow([]));
  }
}

function causeValues() {
  const result = [];
  for (const tr of [...causeBody.children].slice(1)) {
    const w = tr.widgets;
    const row = [w.rule.value, w.cause.value, w.alt.value, w.extraRule.value,
                 w.amount.value.trim(), w.floor.value.trim(), w.basis.value];
    // 규정과 사유만 고르고 값을 안 넣은 줄은 규정이 아니다.
    if (row[0] && row.slice(2).some(Boolean)) result.push(row);
  }
  return result;
}

/** 장기급여 — 표 값의 뜻과 언제 주는지. */
function longtermColumnPanel(grid, columns, values) {
  const widgets = grid.columnWidgets;
  const syncAll = () => {
    for (const name of columns) {
      const w = widgets[name];
      const inKind = w.kind.value === "현물";
      w.escalation.disabled = !inKind;
      if (!inKind) w.escalation.value = "";
      // 창립기념일은 '근속도달' 에만 뜻이 있다.
      const atMilestone = w.timing.value === META.longterm_timings[0];
      w.anniversary.disabled = !atMilestone;
      if (!atMilestone) w.anniversary.value = "";
    }
  };
  const field = (key, build, detail = false) => panelRow(
    LONGTERM_PANEL_LABELS[key], columns, (name) => {
      const widget = build(values[name] || {}, name);
      (widgets[name] ||= {})[key] = widget;
      return widget;
    }, detail);

  // '어느 직군' 은 따로 만든 열이 있을 때만 뜻이 있다. 직군 열은 제 이름이
  // 곧 규정이라, 늘 띄워 두면 고를 것 없는 줄이 하나 는다.
  if (columns.some((name) => !groups.includes(name))) {
    grid.tbody.append(field("rule", (item, name) =>
      makeSelect(groups, groups.includes(name) ? name : (item.rule || groups[0] || ""))));
  } else {
    for (const name of columns) (widgets[name] ||= {}).rule = { value: name };
  }
  grid.tbody.append(field("kind", (item) =>
    makeSelect(META.longterm_types, item.kind || META.longterm_types[0])));
  grid.tbody.append(field("timing", (item) =>
    makeSelect(META.longterm_timings, item.timing || META.longterm_timings[0])));
  grid.tbody.append(field("every", (item) =>
    el("input", { type: "text", value: item.every || "" }), true));
  grid.tbody.append(field("accumulate", (item) => {
    const box = el("input", { type: "checkbox" });
    box.checked = Boolean(item.accumulate);
    return box;
  }, true));
  grid.tbody.append(field("escalation", (item) =>
    el("input", { type: "text", value: item.escalation || "" }), true));
  grid.tbody.append(field("anniversary", (item) =>
    el("input", { type: "text", value: item.anniversary || "", placeholder: "10-01" }),
    true));
  grid.tbody.append(field("note", (item) =>
    el("input", { type: "text", value: item.note || "", style: "text-align:left" }),
    true));

  for (const name of columns) {
    widgets[name].kind.addEventListener("change", syncAll);
    widgets[name].timing.addEventListener("change", syncAll);
    // 직군 열은 제 이름이 곧 규정이라 바꿀 것이 없다.
    if (groups.includes(name) && widgets[name].rule.tagName) {
      widgets[name].rule.disabled = true;
    }
  }
  syncAll();
  syncDetailRows(grid);
}

const LONGTERM_PANEL_LABELS = {
  rule: "어느 직군", kind: "지급유형", timing: "지급시점", every: "반복 주기(년)",
  accumulate: "누적", escalation: "현물 상승률", anniversary: "지급일(월-일)",
  note: "환산 근거",
};

function longtermValues() {
  const result = {};
  const grid = gridBodies[META.sheets.find((s) => s.column_panel === "longterm").sheet];
  for (const [name, w] of Object.entries(grid.columnWidgets)) {
    result[name] = {
      rule: w.rule.value, kind: w.kind.value,
      escalation: w.escalation.value.trim(),
      timing: w.timing.value, every: w.every.value.trim(),
      accumulate: w.accumulate.checked,
      anniversary: w.anniversary.value.trim(),
      note: w.note.value.trim(),
    };
  }
  return result;
}

const COLUMN_PANELS = {
  benefit: benefitColumnPanel,
  longterm: longtermColumnPanel,
};

// ── 직군 매핑 탭 ──
let mapTable = null;
let mapSummary = null;

function buildMapTab(page) {
  const toolbar = el("div", { class: "toolbar" },
    el("button", { class: "small", type: "button", onclick: scanRoster },
       "명부에서 직군 읽어오기"),
    el("button", { class: "small", type: "button", onclick: applySuggestions },
       "제안대로 채우기"));
  mapSummary = el("div", { class: "hint" }, "명부를 읽으면 조합이 나타납니다. " +
    "(명부 파일은 [산출] 탭에서 고른 것을 씁니다)");
  mapTable = el("tbody");
  const table = el("table", { class: "grid" });
  table.append(mapTable);
  page.append(
    el("div", { class: "hint" },
       "명부의 직급·직군을 산출에 쓸 묶음으로 배정합니다. 묶음 이름은 위 " +
       "'직군별 규정' 칸에서 바꾸세요. 같은 '촉탁사원'이라도 회사에 따라 계약직일 " +
       "수도, 임원일 수도 있으니 제안을 그대로 믿지 말고 규정을 확인하세요."),
    toolbar, mapSummary, el("div", { class: "scroll-x" }, table));
}

function renderMap() {
  const headers = ["명부 직군", "임직원구분", "임원 판정", "재직", "퇴직", "→ 변환 직군"];
  mapTable.replaceChildren(el("tr", {}, ...headers.map((h) => el("th", {}, h))));
  for (const row of mapData) {
    if (!groups.includes(row.target)) row.target = row.suggest || groups[0] || "";
    const select = makeSelect(groups, row.target);
    select.addEventListener("change", () => { row.target = select.value; });
    mapTable.append(el("tr", {},
      el("td", { class: "name" }, row.source || "(빈 값)"),
      el("td", { class: "name" }, row.kind || "(빈 값)"),
      el("td", { class: "name" }, row.normalized || ""),
      el("td", { class: "num" }, String(row.active ?? "")),
      el("td", { class: "num" }, String(row.retired ?? "")),
      el("td", {}, select)));
  }
  if (mapData.length) {
    const used = [...new Set(mapData.map((r) => r.target))];
    const people = mapData.reduce((n, r) => n + (r.active || 0) + (r.retired || 0), 0);
    mapSummary.textContent =
      `조합 ${mapData.length}개 · 인원 ${people.toLocaleString()}명 → 묶음 ${used.length}개 (${used.join(", ")})`;
  }
}

async function scanRoster() {
  try {
    const path = await rosterIntoFS();
    const { found } = py("roster_scan", { path, groups });
    const previous = new Map(mapData.map((r) => [pairKey(r.source, r.kind), r.target]));
    mapData = found.map((f) => ({
      source: f.source, kind: f.kind, normalized: f.normalized,
      active: f.active, retired: f.retired, suggest: f.suggest,
      target: previous.get(pairKey(f.source, f.kind)) || f.suggest,
    }));
    renderMap();
    saveEditorLocal();
  } catch (error) {
    alert("명부에서 직군을 읽지 못했습니다.\n\n" + error.message);
  }
}

function applySuggestions() {
  for (const row of mapData) row.target = row.suggest || row.target;
  renderMap();
}

// ── state 모으기 / 되그리기 ──
function collectState() {
  const grids = {};
  for (const spec of META.sheets) {
    const grid = gridBodies[spec.sheet];
    grids[spec.sheet] = {
      key: grid.keySelect ? grid.keySelect.value : spec.key,
      rows: gridRows(spec.sheet),
      extra: [...(grid.extra || [])],
    };
  }
  return {
    job_groups: [...groups],
    grids,
    payout: payoutValues(),
    benefit_rules: ruleValues(),
    longterm_rules: longtermValues(),
    exit_causes: causeValues(),
    mapping: mapData.map((r) => [r.source, r.kind, r.target]),
  };
}

//: 열 머리 패널이 읽을 값. 시트마다 state 의 어느 자리에서 오는지.
const PANEL_SOURCE = { benefit: "benefit_rules", longterm: "longterm_rules" };

function renderState(state) {
  groups = state.job_groups?.length ? [...state.job_groups] : [...META.default_groups];
  $("ed-groups").value = groups.join(", ");
  for (const spec of META.sheets) {
    const item = state.grids?.[spec.sheet] || {};
    renderGrid(spec.sheet, item.key || spec.key, item.rows || [], item.extra || [],
               state[PANEL_SOURCE[spec.column_panel]] || {});
  }
  renderPayout(state.payout || {});
  renderCauses(state.exit_causes || []);
  const scanned = new Map(mapData.map((r) => [pairKey(r.source, r.kind), r]));
  mapData = (state.mapping || []).map(([source, kind, target]) => {
    const seen = scanned.get(pairKey(source, kind));
    return { source, kind, target,
             normalized: seen?.normalized || "", active: seen?.active || 0,
             retired: seen?.retired || 0, suggest: seen?.suggest || target };
  });
  renderMap();
  refreshSectionCounts();
  syncEditorHint();
}

// 직군을 바꾸면 값은 직군 이름으로 이어받는다. 이름이 사라진 직군의 값은 버린다.
function applyGroups(names) {
  const cleaned = names.map((n) => n.trim()).filter(Boolean);
  if (!cleaned.length) { alert("직군을 하나 이상 입력하세요."); return; }
  if (new Set(cleaned).size !== cleaned.length) {
    alert("같은 직군 이름이 두 번 들어갔습니다."); return;
  }
  const state = collectState();
  const old = state.job_groups;
  for (const spec of META.sheets) {
    if (spec.fixed.length) continue;
    // 값은 **열 이름** 을 따라 옮긴다. 자리로 옮기면 직군을 하나 지웠을 때
    // 그 뒤 열의 값이 통째로 한 칸씩 밀린다.
    const before = gridColumns(spec.sheet);
    const extra = (state.grids[spec.sheet].extra || []).filter(
      (n) => n && !cleaned.includes(n));
    const after = [...cleaned, ...new Set(extra)];
    state.grids[spec.sheet].extra = extra;
    state.grids[spec.sheet].rows = state.grids[spec.sheet].rows.map((row) => {
      const byName = Object.fromEntries(before.map((n, i) => [n, row[i + 1] || ""]));
      return [row[0], ...after.map((n) => byName[n] || "")];
    });
  }
  state.job_groups = cleaned;
  renderState(state);
  saveEditorLocal();
}

$("ed-groups-apply").addEventListener("click", () =>
  applyGroups($("ed-groups").value.split(",")));
$("ed-groups-reset").addEventListener("click", () =>
  applyGroups([...META.default_groups]));
$("ed-groups-roster").addEventListener("click", async () => {
  try {
    const path = await rosterIntoFS();
    const result = py("roster_groups", { path });
    if (!result.groups.length) { alert("Input 시트에서 직군을 찾지 못했습니다."); return; }
    applyGroups(result.groups);
  } catch (error) {
    alert("명부에서 직군을 읽지 못했습니다.\n\n" + error.message);
  }
});

// ── 가정세트 ──
// 지급률·지급규정·직군 매핑까지 통째로 저장해 두고 다음 결산에 그대로 쓴다.
$("ed-preset-save").addEventListener("click", async () => {
  const suggestion = $("ed-preset").value || "";
  const name = prompt("가정세트 이름을 정하세요. (예: A사 퇴직금규정)", suggestion);
  if (name === null || !name.trim()) return;
  try {
    flushEditor();
    const result = py("preset_save", { name: name.trim(), state: collectState() });
    if (!result.saved) {
      alert("저장하기 전에 고쳐 주세요.\n\n· " + result.problems.join("\n· "));
      return;
    }
    await persistHome();
    refreshLibrary();
    $("ed-preset").value = result.name;
    $("ed-status").textContent =
      `가정세트 '${result.name}' 을(를) 저장했습니다. 다른 단체에서도 골라 쓸 수 있습니다.`;
  } catch (error) {
    alert("저장하지 못했습니다.\n\n" + error.message);
  }
});

$("ed-preset-load").addEventListener("click", () => {
  const name = $("ed-preset").value;
  if (!name) { alert("불러올 가정세트가 없습니다. 먼저 [현재 가정 저장] 으로 만드세요."); return; }
  try {
    renderState(py("preset_state", { name }).state);
    flushEditor();
    $("ed-status").textContent = `가정세트 '${name}' 을(를) 불러왔습니다.`;
  } catch (error) {
    alert(error.message);
  }
});

// ── 등록 자료 불러오기 ──
$("ed-rates-load").addEventListener("click", () => {
  try {
    const name = $("ed-rates").value;
    const state = name === "__builtin__"
      ? py("standard_state", { groups }).state
      : py("rates_state", { name }).state;
    // 표준률은 출발점일 뿐이다. 매핑·지급규정은 지금 화면 것을 지킨다.
    const keep = collectState();
    state.mapping = keep.mapping;
    if (state.job_groups.join() === keep.job_groups.join()) {
      state.payout = keep.payout;
    }
    renderState(state);
    saveEditorLocal();
    $("ed-status").textContent =
      (name === "__builtin__" ? "내장 표준률" : `표준률 '${name}'`) +
      " 을(를) 불러왔습니다. 회사에 맞게 고친 뒤 쓰세요.";
  } catch (error) {
    alert(error.message);
  }
});

$("ed-curve-apply").addEventListener("click", () => {
  try {
    const result = py("curve_rows", { name: $("ed-curve").value, grade: $("ed-grade").value });
    const state = collectState();
    state.grids["할인율"] = { key: "연차", rows: result.rows };
    renderState(state);
    saveEditorLocal();
    $("ed-status").textContent =
      `금리표 ${result.label} 곡선(만기 ${result.rows.length}개` +
      (result.base_date ? ` · ${result.base_date} 기준` : "") + ")을 할인율에 넣었습니다.";
  } catch (error) {
    alert(error.message);
  }
});

// ── 예시·불러오기·저장 ──
$("ed-example").addEventListener("click", () => {
  renderState(py("state_example", { groups }).state);
  saveEditorLocal();
  $("ed-status").textContent = "예시 값을 채웠습니다. 회사 규정에 맞게 고쳐 주세요.";
});

$("ed-load").addEventListener("change", async () => {
  const file = $("ed-load").files[0];
  if (!file) return;
  try {
    await intoFS(file, "/work/불러온기초율.xlsx");
    renderState(py("state_read", { path: "/work/불러온기초율.xlsx" }).state);
    saveEditorLocal();
    $("ed-status").textContent = `불러왔습니다: ${file.name}`;
  } catch (error) {
    alert("파일을 읽지 못했습니다.\n\n" + error.message);
  } finally {
    $("ed-load").value = "";
  }
});

$("ed-save").addEventListener("click", () => {
  $("ed-problems").innerHTML = "";
  try {
    const result = py("state_write", { state: collectState(), path: "/work/산출가정.xlsx" });
    if (!result.written) {
      $("ed-problems").append(el("div", { class: "error" },
        "저장하기 전에 고쳐 주세요.\n\n· " + result.problems.join("\n· ")));
      return;
    }
    saveEditorLocal();
    download("/work/산출가정.xlsx", "산출가정.xlsx");
    $("asrc-editor").checked = true;
    $("ed-status").textContent =
      "산출가정.xlsx 를 내려받았습니다. [산출] 탭에도 이 내용이 연결되었습니다.";
  } catch (error) {
    alert("저장하지 못했습니다.\n\n" + error.message);
  }
});

function syncEditorHint() {
  const rows = gridBodies["할인율"] ? gridRows("할인율") : [];
  const hint = $("editor-state-hint");
  if (rows.length) {
    hint.textContent = `— 직군 ${groups.length}개 · 할인율 ${rows.length}행 입력됨`;
    $("asrc-editor").disabled = false;
  } else {
    hint.textContent = "— 아직 입력 없음";
    $("asrc-editor").disabled = true;
    if ($("asrc-editor").checked) $("asrc-file").checked = true;
  }
}

// ═════════ 기본가정 관리 ═════════════════════════════════════════
function refreshLibrary() {
  const { library, backup } = py("library_list");
  renderLibraryList("금리표", library["금리표"], $("lib-curve-list"));
  renderLibraryList("표준률", library["표준률"], $("lib-rates-list"));
  renderLibraryList("명부", library["명부"], $("lib-roster-list"), { pin: false });
  renderLibraryList("가정세트", library["가정세트"], $("lib-preset-list"));

  const preset = $("ed-preset");
  const chosenPreset = preset.value;
  preset.replaceChildren(
    ...library["가정세트"].entries.map((e) => el("option", { value: e.name }, e.name)));
  if (!library["가정세트"].entries.length) {
    preset.replaceChildren(el("option", { value: "" }, "(저장된 가정세트 없음)"));
  } else if (library["가정세트"].entries.some((e) => e.name === chosenPreset)) {
    preset.value = chosenPreset;
  } else if (library["가정세트"].default) {
    preset.value = library["가정세트"].default;
  }

  // 산출 탭의 저장된 명부 목록.
  const savedRoster = $("roster-saved");
  const chosen = savedRoster.value;
  savedRoster.replaceChildren(
    el("option", { value: "" }, "(새 파일 올리기)"),
    ...library["명부"].entries.map((e) => el("option", { value: e.name }, e.name)));
  if (chosen && library["명부"].entries.some((e) => e.name === chosen)) {
    savedRoster.value = chosen;
  }

  $("backup-stamp").textContent = backup
    ? `마지막 내보내기: ${backup}`
    : "아직 한 번도 내보내지 않았습니다.";

  const rates = $("ed-rates");
  rates.replaceChildren(
    el("option", { value: "__builtin__" }, "내장 표준률 (15~70세)"),
    ...library["표준률"].entries.map((e) => el("option", { value: e.name }, e.name)));
  if (library["표준률"].default) rates.value = library["표준률"].default;

  const curve = $("ed-curve");
  curve.replaceChildren(
    ...library["금리표"].entries.map((e) => el("option", { value: e.name }, e.name)));
  if (!library["금리표"].entries.length) {
    curve.replaceChildren(el("option", { value: "" }, "(등록된 금리표 없음)"));
  } else if (library["금리표"].default) {
    curve.value = library["금리표"].default;
  }
}

function renderLibraryList(kind, data, target, options = {}) {
  const withPin = options.pin !== false;
  if (!data.entries.length) {
    target.replaceChildren(el("p", { class: "notice" }, "아직 등록된 것이 없습니다."));
    return;
  }
  const rows = data.entries.map((entry) => {
    const isDefault = withPin && entry.name === data.default;
    const actions = [];
    if (withPin) {
      actions.push(el("button", { class: "small", type: "button", onclick: async () => {
        try {
          py("library_pin", { kind, name: data.pinned === entry.name ? "" : entry.name });
          await persistHome();
          refreshLibrary();
        } catch (error) { alert(error.message); }
      } }, data.pinned === entry.name ? "지정 해제" : "기본 지정"), " ");
    }
    actions.push(el("button", { class: "small", type: "button", onclick: async () => {
      if (!confirm(`${kind} '${entry.name}' 등록을 삭제할까요?`)) return;
      try {
        py("library_remove", { kind, name: entry.name });
        await persistHome();
        refreshLibrary();
      } catch (error) { alert(error.message); }
    } }, "삭제"));
    const cells = [
      el("td", {}, entry.name, " ",
         isDefault ? el("span", { class: "badge" }, data.pinned === entry.name ? "기본(지정)" : "기본(최신)") : ""),
      el("td", {}, entry.registered),
      el("td", {}, ...actions),
    ];
    return el("tr", {}, ...cells);
  });
  target.replaceChildren(el("div", { class: "scroll-x" },
    el("table", { class: "data" },
      el("tr", {}, el("th", {}, "이름"), el("th", {}, "등록일"), el("th", {}, "")),
      ...rows)));
}

async function registerAsset(kind, fileInput, nameInput) {
  const file = fileInput.files[0];
  if (!file) { alert("먼저 파일을 골라 주세요."); return; }
  try {
    await intoFS(file, "/work/등록원본.xlsx");
    const name = nameInput.value.trim() || file.name.replace(/\.(xlsx|xlsm)$/i, "");
    py("library_register", { kind, path: "/work/등록원본.xlsx", name });
    await persistHome();
    refreshLibrary();
    fileInput.value = "";
    nameInput.value = "";
    status(`${kind} '${name}' 을(를) 등록했습니다. 이 기기에 저장되어 계속 쓸 수 있습니다.`);
  } catch (error) {
    alert("등록하지 못했습니다.\n\n" + error.message);
  }
}

$("lib-curve-add").addEventListener("click", () =>
  registerAsset("금리표", $("lib-curve-file"), $("lib-curve-name")));
$("lib-rates-add").addEventListener("click", () =>
  registerAsset("표준률", $("lib-rates-file"), $("lib-rates-name")));
$("lib-roster-add").addEventListener("click", () =>
  registerAsset("명부", $("lib-roster-file"), $("lib-roster-name")));
$("lib-preset-add").addEventListener("click", () =>
  registerAsset("가정세트", $("lib-preset-file"), $("lib-preset-name")));

// ── 저장된 명부 ──
// 목록에서 고르면 그 파일을 그대로 산출에 쓴다. 새 파일을 올리면 그쪽이 이긴다.
$("roster-saved").addEventListener("change", () => {
  const name = $("roster-saved").value;
  if (!name) {
    $("roster-hint").textContent = "Input · 재직자명부 · 퇴직자명부 시트가 들어 있는 통합문서";
    $("general-filled").style.display = "none";
    return;
  }
  $("roster").value = "";
  $("roster-hint").textContent =
    `저장된 명부 '${name}' 를 사용합니다. 새 파일을 고르면 그 파일이 우선합니다.`;
  fillFromGeneralSheet();
});

// 명부를 고르면 '1)일반사항' 에 담당자가 채워 보낸 것을 입력칸에 옮겨 넣는다.
// 산출 엔진은 칸이 비어도 그 표를 알아서 쓰지만, 화면에 보이지 않으면
// 담당자는 아무것도 읽히지 않은 줄 알고 손으로 다시 적는다.
$("roster").addEventListener("change", () => {
  if ($("roster").files[0]) fillFromGeneralSheet();
});

//: 명부에서 채워 넣은 칸들. 사람이 친 값과 갈라 두어야 한다.
//
// 다른 단체의 명부로 바꿨을 때, 앞 단체의 숫자를 '사람이 넣은 값' 으로 보고
// 남겨 두면 남의 회사 자산으로 산출된다. 우리가 채운 칸은 새 명부 값으로
// 바꾸고, 새 명부에 없는 항목이면 지운다.
const autoFilled = new Set();

/** 사람이 손댄 칸은 그 뒤로 명부가 덮어쓰지 않는다. */
for (const id of ["base_date", "period_start", "asset_opening",
                  "asset_contributions", "asset_paid", "asset_closing"]) {
  $(id).addEventListener("input", () => autoFilled.delete(id));
}

async function fillFromGeneralSheet() {
  const note = $("general-filled");
  note.style.display = "none";
  note.className = "hint";
  try {
    const info = py("general_info", { path: await rosterIntoFS() });
    const fields = info.fields || {};
    const kept = [];
    for (const [id, value] of Object.entries(fields)) {
      const box = $(id);
      if (!box) continue;
      if (box.value.trim() && !autoFilled.has(id)) { kept.push(id); continue; }
      box.value = value;
      autoFilled.add(id);
    }
    for (const id of [...autoFilled]) {
      if (!(id in fields)) { $(id).value = ""; autoFilled.delete(id); }
    }
    if (info.grade && $("ed-grade")) $("ed-grade").value = info.grade;

    const lines = [];
    if (info.found?.length) {
      lines.push("명부의 [1)일반사항] 에서 가져왔습니다 — " + info.found.join(" · "));
    }
    if (kept.length) {
      lines.push("직접 입력한 칸은 그대로 두었습니다.");
    }
    if (info.problems?.length) {
      lines.push("⚠ " + info.problems.join(" · "));
      note.className = "warn-box";
    }
    // 채워 넣은 구획은 펼쳐 둔다. 접힌 채로 값만 들어가면 담당자가 확인할
    // 기회 없이 그대로 산출된다 — 회사 표가 틀렸을 때 잡을 수 없다.
    if ("base_date" in fields || "period_start" in fields) $("sec-dates").open = true;
    if ("asset_opening" in fields) $("sec-assets").open = true;

    refreshCalcBadges();
    if (!lines.length) return;
    note.textContent = lines.join("  ");
    note.style.display = "block";
  } catch {
    // 일반사항이 없는 명부(업로드용 변환본 등)가 많다. 조용히 넘어간다.
  }
}

$("roster-save").addEventListener("click", async () => {
  const file = $("roster").files[0];
  if (!file) { alert("먼저 명부 파일을 고르세요."); return; }
  const suggestion = file.name.replace(/\.(xls|xlsx|xlsm)$/i, "");
  const name = prompt("목록에 표시할 이름을 정하세요.", suggestion);
  if (name === null) return;
  try {
    const path = await rosterIntoFS();
    py("library_register", { kind: "명부", path, name: name.trim() || suggestion });
    await persistHome();
    refreshLibrary();
    status(`명부 '${name.trim() || suggestion}' 을(를) 목록에 저장했습니다.`);
  } catch (error) {
    alert("저장하지 못했습니다.\n\n" + error.message);
  }
});

// ═════════ 산출 ══════════════════════════════════════════════════
// 보통 쓰는 것은 명부와 기초율 둘뿐이고, 기준일·옵션·전기·자산은 손대지 않는
// 회차가 많다. 접어 두되 **무엇이 설정돼 있는지는 접힌 채로 보여야** 한다 —
// 안 그러면 전기 채무를 넣어 둔 것을 잊고 다시 넣거나, 넣은 줄 알고 안 넣는다.
function calcBadge(id, value) {
  const box = document.querySelector(`#${id} > summary > .count`);
  if (box) box.textContent = value;
}

function money(raw) {
  const value = parseNumber(raw);
  return value ? value.toLocaleString("ko-KR") + "원" : "";
}

function refreshCalcBadges() {
  const base = $("base_date").value;
  const start = $("period_start").value;
  calcBadge("sec-dates",
    base || start ? [start, base].filter(Boolean).join(" ~ ") : "명부 값 사용");

  const on = [];
  if ($("force").checked) on.push("강행");
  if ($("sensitivity").checked) on.push("민감도");
  if ($("longterm").checked) on.push("장기급여");
  calcBadge("sec-options", on.join(" · ") || "기본");

  const prior = priorLink ? priorLink.name : money($("prior_dbo").value);
  calcBadge("sec-prior", prior || "없음 (최초 평가)");

  const closing = money($("asset_closing").value);
  calcBadge("sec-assets", closing ? "기말 " + closing : "없음");
}

function parseNumber(raw) {
  const token = raw.replaceAll(",", "").trim();
  return token ? parseFloat(token) : 0;
}
function parseRate(raw) {
  const token = raw.replaceAll(",", "").trim();
  if (!token) return 0;
  const value = parseFloat(token.replace("%", ""));
  return token.includes("%") || value > 1 ? value / 100 : value;
}
function fillTable(table, rows, numericFrom) {
  table.innerHTML = "";
  for (const row of rows) {
    const tr = document.createElement("tr");
    row.forEach((value, index) => {
      const cell = document.createElement(index === 0 ? "th" : "td");
      if (index >= numericFrom) cell.className = "num";
      cell.textContent = value;
      tr.appendChild(cell);
    });
    table.appendChild(tr);
  }
}

// 산출 내역에서 불러온 입력. 파일을 새로 고르면 그쪽이 우선한다.
let loadedRun = null;   // {name, roster, assumptions, rosterName}
let lastRun = null;     // 방금 마친 산출 — 저장 버튼이 이것을 보관한다
let priorLink = null;   // 전기로 연결한 저장 산출 {name, values, assumptions}

// 명부를 어디서 가져올지: 방금 올린 파일 > 목록에서 고른 저장 명부 > 불러온 산출 내역.
async function rosterIntoFS() {
  const file = $("roster").files[0];
  if (file) {
    const suffix = file.name.toLowerCase().match(/\.(xls[xm]?)$/);
    return intoFS(file, "/work/명부." + (suffix ? suffix[1] : "xlsx"));
  }
  const saved = $("roster-saved").value;
  if (saved) return py("library_path", { kind: "명부", name: saved }).path;
  if (loadedRun) return loadedRun.roster;
  throw new Error("[산출] 탭에서 명부 파일을 고르거나 저장된 명부를 선택하세요.");
}

function currentRosterName() {
  const file = $("roster").files[0];
  if (file) return file.name;
  return $("roster-saved").value || loadedRun?.rosterName || "";
}

$("form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("errors").innerHTML = "";
  $("result").style.display = "none";
  $("run").disabled = true;
  try {
    status("산출하는 중…");
    const rosterPath = await rosterIntoFS();

    let assumptionsPath = "/work/기초율.xlsx";
    if ($("asrc-editor").checked) {
      const saved = py("state_write", { state: collectState(), path: assumptionsPath });
      if (!saved.written) {
        status("산출가정 입력에 문제가 있습니다. [산출가정 입력] 탭을 확인하세요.");
        $("errors").append(el("div", { class: "error" }, "· " + saved.problems.join("\n· ")));
        return;
      }
    } else if ($("asrc-saved").checked) {
      if (!loadedRun) throw new Error("[산출 내역] 탭에서 먼저 저장된 산출을 불러오세요.");
      assumptionsPath = loadedRun.assumptions;
    } else {
      const file = $("assumptions").files[0];
      if (!file) throw new Error("기초율 파일을 고르거나, 산출가정 입력 화면을 사용하세요.");
      await intoFS(file, assumptionsPath);
    }

    const options = {
      force: $("force").checked, sensitivity: $("sensitivity").checked,
      longterm: $("longterm").checked,
      base_date: $("base_date").value, period_start: $("period_start").value,
      prior_dbo: $("prior_dbo").value, prior_rate: $("prior_rate").value,
      prior_run: $("prior-run").value,
      past_service_cost: $("past_service_cost").value,
      settlement_obligation: $("settlement_obligation").value,
      asset_opening: $("asset_opening").value,
      asset_contributions: $("asset_contributions").value,
      asset_paid: $("asset_paid").value,
      asset_closing: $("asset_closing").value,
      unpaid_benefits: $("unpaid_benefits").value,
    };
    if (options.base_date && options.period_start
        && options.period_start >= options.base_date) {
      throw new Error("산출 시작일은 산출기준일보다 앞서야 합니다.");
    }
    const report = py("run", {
      roster: rosterPath, assumptions: assumptionsPath,
      force: options.force, sensitivity: options.sensitivity,
      longterm: options.longterm,
      base_date: options.base_date, period_start: options.period_start,
      prior_dbo: parseNumber(options.prior_dbo),
      prior_rate: parseRate(options.prior_rate),
      prior_service_cost: priorLink ? priorLink.values.service_cost : 0,
      prior_assumptions: priorLink ? priorLink.assumptions : "",
      past_service_cost: parseNumber(options.past_service_cost),
      settlement_obligation: parseNumber(options.settlement_obligation),
      asset_opening: parseNumber(options.asset_opening),
      asset_contributions: parseNumber(options.asset_contributions),
      asset_paid: parseNumber(options.asset_paid),
      asset_closing: parseNumber(options.asset_closing),
      unpaid_benefits: parseNumber(options.unpaid_benefits),
    });

    if (!report.run) {
      status("명부 검증 오류로 산출을 중단했습니다.");
      for (const message of report.errors) {
        $("errors").append(el("div", { class: "error" }, message));
      }
      if (report.more) {
        $("errors").append(el("p", { class: "notice" }, `… 외 ${report.more}건`));
      }
      return;
    }

    fillTable($("summary"), report.summary, 1);
    fillTable($("groups"),
      [["직군", "인원", "확정급여채무", "당기근무원가"], ...report.groups], 1);
    if (report.rollforward && report.rollforward.length) {
      $("roll-wrap").style.display = "block";
      fillTable($("rollforward"),
        report.rollforward.map(([k, v]) => [k, Math.round(v).toLocaleString("en-US")]), 1);
    } else {
      $("roll-wrap").style.display = "none";
    }
    if (report.assets && report.assets.length) {
      $("assets-wrap").style.display = "block";
      fillTable($("assets"),
        report.assets.map(([k, v]) => [k, Math.round(v).toLocaleString("en-US")]), 1);
    } else {
      $("assets-wrap").style.display = "none";
    }
    $("issues").textContent = report.issues +
      (report.excluded.length ? "\n산출 제외: " + report.excluded.join(" · ") : "");

    lastRun = {
      roster: rosterPath, assumptions: assumptionsPath,
      rosterName: currentRosterName(), report, options,
    };
    if (!$("run-name").value && loadedRun) $("run-name").value = loadedRun.name;

    $("result").style.display = "block";
    status("산출을 마쳤습니다.");
    $("result").scrollIntoView({ behavior: "smooth" });
  } catch (error) {
    status("산출하지 못했습니다: " + (error.message || error));
  } finally {
    $("run").disabled = false;
  }
});

$("dl-result").addEventListener("click", () => download("/work/산출결과.xlsx", "산출결과.xlsx"));
$("dl-members").addEventListener("click", () => download("/work/개인별결과.xlsx", "개인별결과.xlsx"));

// ═════════ 산출 내역 ═════════════════════════════════════════════
// 산출 하나(명부·가정·결과·요약)를 이름 붙여 IndexedDB 에 보관한다.
// "2412 1번단체" 를 저장해 두면 다음 결산 때 전기 입력을 그대로 끌어온다.

$("run-save").addEventListener("click", async () => {
  if (!lastRun) { alert("먼저 산출을 실행하세요."); return; }
  const name = $("run-name").value.trim();
  if (!name) { alert("산출명을 입력하세요. 예: 2412 1번단체"); return; }
  try {
    py("run_save", {
      name, roster: lastRun.roster, assumptions: lastRun.assumptions,
      work: "/work", report: lastRun.report, options: lastRun.options,
      roster_name: lastRun.rosterName,
      saved: new Date().toLocaleString("sv-SE").slice(0, 16),
    });
    await persistHome();
    refreshRuns();
    status(`산출 '${name}' 을(를) 이 기기에 저장했습니다. [산출 내역] 탭에서 볼 수 있습니다.`);
  } catch (error) {
    alert("저장하지 못했습니다.\n\n" + error.message);
  }
});

// ── 전기 산출 연결 ──
// 전기 DBO·할인율·근무원가를 손으로 옮겨 적으면 자릿수를 틀리기 쉽다.
// 저장된 산출을 고르면 숫자와 전기 기초율이 한꺼번에 붙는다.
$("prior-run").addEventListener("change", () => {
  const name = $("prior-run").value;
  if (!name) {
    priorLink = null;
    $("prior_dbo").value = "";
    $("prior_rate").value = "";
    $("prior-hint").textContent =
      "전기 산출을 고르면 확정급여채무·할인율·근무원가가 그대로 들어오고, " +
      "전기 기초율까지 연결되어 증감분석이 경험조정과 가정변경효과를 나눠 계산합니다.";
    return;
  }
  try {
    priorLink = py("run_prior", { name });
    const values = priorLink.values;
    $("prior_dbo").value = Math.round(values.dbo).toLocaleString("en-US");
    $("prior_rate").value = (values.discount_rate * 100).toFixed(3) + "%";
    $("prior-hint").textContent =
      `'${name}' (기준일 ${values.base_date || "?"}) 의 전기값을 연결했습니다. ` +
      "전기 기초율도 함께 넘겨 가정변경효과를 분리합니다.";
  } catch (error) {
    priorLink = null;
    alert(error.message);
  }
});

function refreshRuns() {
  const { runs } = py("run_list");
  const target = $("runs-list");

  // 전기 선택 목록도 같이 새로 고친다.
  const priorBox = $("prior-run");
  const chosen = priorBox.value;
  priorBox.replaceChildren(
    el("option", { value: "" }, "(직접 입력)"),
    ...runs.map((run) => el("option", { value: run.name },
      run.base_date ? `${run.name} — ${run.base_date}` : run.name)));
  if (runs.some((run) => run.name === chosen)) priorBox.value = chosen;

  if (!runs.length) {
    target.replaceChildren(el("p", { class: "notice" },
      "아직 저장된 산출이 없습니다. 산출을 마친 뒤 결과 아래 [이 산출을 기기에 저장] 에서 이름을 붙여 저장하세요."));
    return;
  }
  const header = el("tr", {}, ...["산출명", "저장일", "기준일", "인원", "DBO", ""]
    .map((h) => el("th", {}, h)));
  const rows = runs.map((run) => {
    const show = el("button", { class: "small", type: "button",
      onclick: () => showRun(run.name) }, "결과 보기");
    show.disabled = !run.has_results;
    return el("tr", {},
      el("td", {}, el("b", {}, run.name),
        run.roster_name ? el("div", { class: "hint" }, run.roster_name) : ""),
      el("td", {}, run.saved),
      el("td", {}, run.base_date),
      el("td", { class: "num" }, run.headcount),
      el("td", { class: "num" }, run.dbo),
      el("td", {},
        el("button", { class: "small primary", type: "button",
          onclick: () => restoreRun(run.name) }, "입력 불러오기"), " ",
        show, " ",
        el("button", { class: "small", type: "button",
          onclick: () => deleteRun(run.name) }, "삭제")));
  });
  target.replaceChildren(el("div", { class: "scroll-x" },
    el("table", { class: "data" }, header, ...rows)));
}

function restoreRun(name) {
  try {
    const restored = py("run_restore", { name, work: "/work" });
    loadedRun = {
      name, roster: restored.roster, assumptions: restored.assumptions,
      rosterName: restored.meta.roster_name || "",
    };
    const options = restored.meta.options || {};
    if ("force" in options) $("force").checked = Boolean(options.force);
    if ("sensitivity" in options) $("sensitivity").checked = Boolean(options.sensitivity);
    if ("longterm" in options) $("longterm").checked = Boolean(options.longterm);
    $("base_date").value = options.base_date || "";
    $("period_start").value = options.period_start || "";
    $("prior_dbo").value = options.prior_dbo || "";
    $("prior_rate").value = options.prior_rate || "";
    for (const key of ["past_service_cost", "settlement_obligation",
                       "asset_opening", "asset_contributions",
                       "asset_paid", "asset_closing", "unpaid_benefits"]) {
      $(key).value = options[key] || "";
    }
    $("run-name").value = name;

    $("asrc-saved").disabled = false;
    $("asrc-saved").checked = true;
    $("saved-asrc-hint").textContent = `— '${name}' 의 기초율`;
    $("roster").value = "";
    $("loaded-run-text").textContent =
      `산출 내역 '${name}' 의 입력을 사용 중입니다 (명부` +
      (loadedRun.rosterName ? `: ${loadedRun.rosterName}` : "") +
      " · 기초율). 다른 명부 파일을 고르면 그 파일이 우선합니다.";
    $("loaded-run-banner").style.display = "block";
    $("roster-hint").textContent = "저장된 산출의 명부를 사용합니다. 새 파일을 고르면 대체됩니다.";
    $("tab-calc").click();
    status(`산출 '${name}' 의 입력을 불러왔습니다. [산출 실행]을 누르면 그대로 다시 산출합니다.`);
  } catch (error) {
    alert(error.message);
  }
}

$("loaded-run-clear").addEventListener("click", () => {
  loadedRun = null;
  $("loaded-run-banner").style.display = "none";
  $("roster-hint").textContent = "Input · 재직자명부 · 퇴직자명부 시트가 들어 있는 통합문서";
  $("asrc-saved").disabled = true;
  $("saved-asrc-hint").textContent = "— [산출 내역] 탭에서 불러오면 열립니다";
  if ($("asrc-saved").checked) $("asrc-file").checked = true;
});

function showRun(name) {
  try {
    const { meta } = py("run_restore", { name, work: "/work" });
    const report = meta.report || {};
    $("run-dialog-title").textContent = `${name} — 저장된 산출 결과`;
    fillTable($("run-dialog-summary"), report.summary || [], 1);
    fillTable($("run-dialog-groups"),
      [["직군", "인원", "확정급여채무", "당기근무원가"], ...(report.groups || [])], 1);
    $("run-dl-result").onclick = () => downloadRunFile(name, "산출결과.xlsx");
    $("run-dl-members").onclick = () => downloadRunFile(name, "개인별결과.xlsx");
    $("run-dialog").showModal();
  } catch (error) {
    alert(error.message);
  }
}

function downloadRunFile(name, filename) {
  try {
    const { files } = py("run_results", { name, work: "/work" });
    if (!files[filename]) { alert("저장된 " + filename + " 이(가) 없습니다."); return; }
    download(files[filename], `${name}_${filename}`);
  } catch (error) {
    alert(error.message);
  }
}

async function deleteRun(name) {
  if (!confirm(`산출 내역 '${name}' 을(를) 삭제할까요?\n저장된 명부·가정·결과가 함께 지워집니다.`)) return;
  try {
    py("run_delete", { name });
    await persistHome();
    refreshRuns();
    if (loadedRun && loadedRun.name === name) $("loaded-run-clear").click();
    if (priorLink && priorLink.name === name) {
      $("prior-run").value = "";
      $("prior-run").dispatchEvent(new Event("change"));
    }
  } catch (error) {
    alert(error.message);
  }
}

// ── 보관함 ──
// 브라우저 저장소는 사용자가 방문기록을 지우면 함께 사라진다. 전부를 파일
// 하나로 내보내 iCloud Drive 처럼 기기 밖에 두게 한다.
$("backup-export").addEventListener("click", async () => {
  try {
    status("보관함을 만드는 중…");
    await persistHome();
    const result = py("backup_export", { work: "/work" });
    download(result.path, result.filename, "application/zip");
    refreshLibrary();
    status(`보관함 ${result.filename} (${(result.size / 1e6).toFixed(1)}MB) 을(를) ` +
           "내려받았습니다. 공유 → 파일에 저장 → iCloud Drive 에 두세요.");
  } catch (error) {
    alert("내보내지 못했습니다.\n\n" + error.message);
  }
});

async function importBackup(replace) {
  const file = $("backup-file").files[0];
  if (!file) { alert("먼저 보관함 zip 파일을 고르세요."); return; }
  if (replace && !confirm(
    "지금 이 기기의 등록 자료와 산출 내역을 모두 지우고 보관함 내용으로 바꿉니다.\n계속할까요?")) return;
  try {
    status("보관함을 읽는 중…");
    await intoFS(file, "/work/보관함.zip");
    const result = py("backup_import", { path: "/work/보관함.zip", replace });
    await persistHome();
    refreshLibrary();
    refreshRuns();
    $("backup-file").value = "";
    status(`보관함을 ${replace ? "그대로 되돌렸습니다" : "합쳤습니다"} — ` +
           `산출 내역 ${result.runs.length}건, 금리표 ${result.library["금리표"].entries.length}건, ` +
           `표준률 ${result.library["표준률"].entries.length}건, 명부 ${result.library["명부"].entries.length}건.`);
  } catch (error) {
    alert("가져오지 못했습니다.\n\n" + error.message);
    status("보관함을 가져오지 못했습니다.");
  }
}

$("backup-import").addEventListener("click", () => importBackup(false));
$("backup-replace").addEventListener("click", () => importBackup(true));

$("base-date-clear").addEventListener("click", () => { $("base_date").value = ""; });

// ═════════ 시험용 난수 명부 ══════════════════════════════════════
// 실제 명부 없이 프로그램을 두드려 보거나 화면을 익힐 때 쓴다. 명부마다 짝이
// 되는 기초율과 '무엇이 들어 있는지' 적은 안내문이 함께 나온다.

let generated = null;

$("gen-run").addEventListener("click", () => {
  try {
    status("시험 명부를 만드는 중… (몇 초 걸립니다)");
    const seed = parseInt($("gen-seed").value, 10) || 20251231;
    generated = py("gen_cases", {
      work: "/work", seed, base_date: $("gen-base-date").value,
    });
    renderGenerated();
    $("gen-download").disabled = false;
    status(`시험 명부 ${generated.cases.length}종을 만들었습니다.` +
           (generated.base_date ? ` (기준일 ${generated.base_date})` : ""));
  } catch (error) {
    status("만들지 못했습니다: " + (error.message || error));
    alert(error.message || error);
  }
});

$("gen-download").addEventListener("click", () => {
  if (!generated) return;
  download(generated.path, generated.filename, "application/zip");
});

function renderGenerated() {
  const target = $("gen-cases");
  target.replaceChildren(...generated.cases.map((item) => {
    const box = el("fieldset", {},
      el("legend", {}, item.title),
      el("div", { class: "hint" }, item.summary),
      el("div", { class: "toolbar" },
        el("button", { class: "small primary", type: "button",
          onclick: () => useGenerated(item) }, "이 명부로 산출 준비"),
        el("button", { class: "small", type: "button",
          onclick: () => showReport(item) }, "특이사항 보기"),
        el("button", { class: "small", type: "button",
          onclick: () => registerGenerated(item) }, "목록에 등록")));
    if (item.force) {
      box.append(el("div", { class: "warn-box" },
        "자료 오류를 일부러 심은 명부입니다 — [검증 오류가 있어도 산출 강행] 을 켜야 끝까지 돕니다."));
    }
    return box;
  }));
}

function useGenerated(item) {
  // 생성한 파일을 그대로 산출 입력으로 물린다. 업로드를 거치지 않는다.
  loadedRun = {
    name: item.title, roster: item.roster, assumptions: item.assumptions,
    rosterName: item.title + ".xlsx",
  };
  $("roster").value = "";
  $("roster-saved").value = "";
  $("asrc-saved").disabled = false;
  $("asrc-saved").checked = true;
  $("saved-asrc-hint").textContent = `— ${item.title} 의 짝 기초율`;
  $("force").checked = Boolean(item.force);
  $("base_date").value = "";
  $("period_start").value = "";
  $("loaded-run-text").textContent =
    `시험 명부 '${item.title}' 과(와) 짝 기초율을 사용합니다.` +
    (item.force ? " 자료 오류가 있어 [강행] 을 켜 두었습니다." : "");
  $("loaded-run-banner").style.display = "block";
  $("roster-hint").textContent = "시험 명부를 사용합니다. 새 파일을 고르면 대체됩니다.";
  $("tab-calc").click();
  // 시험 명부에도 '1)일반사항' 이 들어 있다. 올린 파일과 똑같이 채워 준다.
  fillFromGeneralSheet();
  status(`시험 명부 '${item.title}' 을(를) 산출 탭에 넣었습니다. [산출 실행]을 누르세요.`);
}

function showReport(item) {
  $("report-title").textContent = `${item.title} — 특이사항`;
  $("report-body").textContent = item.report;
  $("report-dialog").showModal();
}

async function registerGenerated(item) {
  try {
    py("gen_case_register", { work: "/work", key: item.key });
    await persistHome();
    refreshLibrary();
    status(`'${item.title}' 을(를) [저장된 명부]·[가정세트] 목록에 등록했습니다.`);
  } catch (error) {
    alert(error.message);
  }
}

// 접힌 구획의 제목 옆 요약은 값이 바뀔 때마다 다시 맞춘다.
$("page-calc").addEventListener("input", refreshCalcBadges);
$("page-calc").addEventListener("change", refreshCalcBadges);
refreshCalcBadges();
