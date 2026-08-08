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

function download(path, filename) {
  const payload = pyodide.FS.readFile(path);
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([payload], {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
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
               ["tab-lib", "page-lib"], ["tab-runs", "page-runs"]];
for (const [tab, page] of PAGES) {
  $(tab).addEventListener("click", () => {
    for (const [t, p] of PAGES) {
      $(t).classList.toggle("on", t === tab);
      $(p).classList.toggle("on", p === page);
    }
  });
}

// ═════════ 산출가정 편집기 ═══════════════════════════════════════
// 화면이 곧 자료다: DOM 의 입력 값을 collectState() 로 모으고,
// renderState() 로 되그린다. 직군이 바뀌면 state 를 고쳐 되그린다.

let groups = [];            // 현재 직군 묶음
let gridBodies = {};        // 시트명 → {spec, keySelect, tbody, table}
let payoutBody = null;      // 지급규정 tbody — 행마다 위젯 참조를 붙인다
let ruleBody = null;        // 지급률 규정
let ltBody = null;          // 장기급여 유형
let mapData = [];           // 직군 매핑 행 [{source, kind, normalized, active, retired, target, suggest}]
const MIN_ROWS = 8;

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

  addTab("직군 매핑", buildMapTab).classList.add("on");
  pages.firstChild.classList.add("on");
  for (const spec of META.sheets) addTab(spec.tab, (page) => buildGridTab(page, spec));
  addTab("지급규정", buildPayoutTab);
  addTab("지급률 규정", buildRuleTab);
  addTab("장기급여 유형", buildLongtermTab);

  $("ed-grade").replaceChildren(...META.grades.map((g) => el("option", {}, g)));
  $("ed-grade").value = "AA0";

  // 입력이 바뀌면 잠시 뒤 브라우저에 임시 저장한다. 탭을 닫아도 살아 있도록.
  let timer = null;
  $("page-edit").addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(saveEditorLocal, 1500);
  });
}

function saveEditorLocal() {
  try {
    localStorage.setItem(EDITOR_STORE, JSON.stringify(collectState()));
    syncEditorHint();
  } catch { /* 저장 공간 부족은 치명적이지 않다 */ }
}

// ── 표 입력 탭 ──
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
  const table = el("table", { class: "grid" });
  const tbody = el("tbody");
  table.append(tbody);
  page.append(toolbar, el("div", { class: "hint" }, "· " + spec.note),
              el("div", { class: "scroll-x" }, table));
  gridBodies[spec.sheet] = { spec, keySelect, tbody };
}

function gridHeaders(spec, keyLabel) {
  return [keyLabel, ...(spec.fixed.length ? spec.fixed : groups)];
}

function renderGrid(sheet, key, rows) {
  const grid = gridBodies[sheet];
  if (grid.keySelect) grid.keySelect.value = key || grid.spec.key;
  const headers = gridHeaders(grid.spec, grid.keySelect ? grid.keySelect.value : grid.spec.key);
  grid.tbody.replaceChildren(
    el("tr", {}, ...headers.map((h) => el("th", {}, h))));
  const count = Math.max(rows.length + 2, MIN_ROWS);
  for (let r = 0; r < count; r += 1) {
    grid.tbody.append(el("tr", {}, ...headers.map((_, c) =>
      el("td", {}, el("input", { type: "text", value: (rows[r] || [])[c] || "" })))));
  }
}

function relabelGrid(sheet) {
  const grid = gridBodies[sheet];
  grid.tbody.querySelector("th").textContent = grid.keySelect.value;
}

function gridRows(sheet) {
  const rows = [];
  for (const tr of [...gridBodies[sheet].tbody.children].slice(1)) {
    const values = [...tr.querySelectorAll("input")].map((i) => i.value.trim());
    if (values.some(Boolean)) rows.push(values);
  }
  return rows;
}

function addGridRow(sheet) {
  const grid = gridBodies[sheet];
  const width = grid.tbody.firstChild.children.length;
  grid.tbody.append(el("tr", {}, ...Array.from({ length: width }, () =>
    el("td", {}, el("input", { type: "text" })))));
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

// ── 지급률 규정 탭 ──
function buildRuleTab(page) {
  const guide =
    "누적  표의 값이 그 근속연수의 누적 배수입니다. (10년 → 10.0)\n" +
    "누진  표의 값이 그 구간에서만 적용할 연 배수입니다. " +
    "0년 1.0 / 5년 1.5 / 10년 2.0 이면 근속 12년 = 5×1.0 + 5×1.5 + 2×2.0 = 16.5\n" +
    "수식  아래 수식으로 직접 계산합니다. 표로 담기 어려운 규정에만 쓰세요.";
  const vars = Object.entries(META.formula_variables)
    .map(([k, v]) => `${k}=${v}`).join(" · ");
  const table = el("table", { class: "grid" });
  ruleBody = el("tbody");
  table.append(ruleBody);
  page.append(
    el("div", { class: "hint", style: "white-space:pre-line" }, guide),
    el("div", { class: "hint" }, "변수  " + vars),
    el("div", { class: "hint" }, "함수  " + META.formula_functions.join(" ")),
    el("div", { class: "hint" },
       '예시  =IF(t<10, t*1.0, 10 + (t-10)*2.0)     =IF(제도="DB", t*1.5, t)     =MIN(t, 30)'),
    el("div", { class: "scroll-x" }, table),
    el("div", { class: "toolbar" },
       el("button", { class: "small", type: "button", onclick: previewFormulas },
          "수식 미리보기")));
}

function renderRules(rules) {
  ruleBody.replaceChildren(el("tr", {},
    ...["규정명(직군)", "방식", "수식", "상태"].map((h) => el("th", {}, h))));
  for (const group of groups) {
    const item = rules[group] || {};
    const mode = makeSelect(META.benefit_modes, item.mode || META.benefit_modes[0]);
    const formula = el("input", { type: "text", value: item.formula || "",
                                  style: "width:280px;text-align:left" });
    const state = el("span", { class: "hint" });
    const sync = () => {
      const isFormula = mode.value === "수식";
      formula.disabled = !isFormula;
      if (!isFormula) { state.textContent = ""; return; }
      const source = formula.value.trim();
      if (!source) { state.textContent = "수식 필요"; state.className = "bad-text"; return; }
      try {
        const check = py("formula_check", { source });
        state.textContent = check.error ? "✕ " + check.error : "✓ 확인됨";
        state.className = check.error ? "bad-text" : "ok-text";
      } catch { /* 부팅 전이면 그냥 둔다 */ }
    };
    mode.addEventListener("change", sync);
    formula.addEventListener("input", sync);
    sync();
    const tr = el("tr", {}, el("td", { class: "name" }, group),
      el("td", {}, mode), el("td", {}, formula), el("td", {}, state));
    tr.dataset.group = group;
    tr.widgets = { mode, formula };
    ruleBody.append(tr);
  }
}

function ruleValues() {
  const result = {};
  for (const tr of [...ruleBody.children].slice(1)) {
    result[tr.dataset.group] = {
      mode: tr.widgets.mode.value, formula: tr.widgets.formula.value.trim(),
    };
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

// ── 장기급여 유형 탭 ──
function buildLongtermTab(page) {
  const guide =
    "휴가        '장기급여' 탭의 값 = 지급일수 → 일 기본급 × 일수 (임금상승률 반영)\n" +
    "평균임금    값 = 배수 → 30일 평균임금 × 배수 (임금상승률 반영)\n" +
    "현물        값 = 정액(원) → 평가시점 시세로 환산해 넣고 현물 상승률로 올림\n" +
    "현금        값 = 정액(원) → 규정 금액이 고정이므로 올리지 않음";
  const table = el("table", { class: "grid" });
  ltBody = el("tbody");
  table.append(ltBody);
  page.append(
    el("div", { class: "hint", style: "white-space:pre-line" }, guide),
    el("div", { class: "scroll-x" }, table),
    el("div", { class: "hint" },
       "현물 상승률은 '현물' 유형에만 씁니다. 환산 근거에는 " +
       "'현물 포상 @ 2026-12-31 시세' 처럼 남겨 두세요."));
}

function renderLongterm(rules) {
  ltBody.replaceChildren(el("tr", {},
    ...["규정명(직군)", "지급유형", "현물 상승률", "환산 근거"].map((h) => el("th", {}, h))));
  for (const group of groups) {
    const item = rules[group] || {};
    const kind = makeSelect(META.longterm_types, item.kind || META.longterm_types[0]);
    const escalation = el("input", { type: "text", value: item.escalation || "" });
    const note = el("input", { type: "text", value: item.note || "",
                               style: "width:240px;text-align:left" });
    const sync = () => {
      const inKind = kind.value === "현물";
      escalation.disabled = !inKind;
      if (!inKind) escalation.value = "";
    };
    kind.addEventListener("change", sync);
    sync();
    if (item.escalation && kind.value === "현물") escalation.value = item.escalation;
    const tr = el("tr", {}, el("td", { class: "name" }, group),
      el("td", {}, kind), el("td", {}, escalation), el("td", {}, note));
    tr.dataset.group = group;
    tr.widgets = { kind, escalation, note };
    ltBody.append(tr);
  }
}

function longtermValues() {
  const result = {};
  for (const tr of [...ltBody.children].slice(1)) {
    result[tr.dataset.group] = {
      kind: tr.widgets.kind.value,
      escalation: tr.widgets.escalation.value.trim(),
      note: tr.widgets.note.value.trim(),
    };
  }
  return result;
}

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
    const previous = new Map(mapData.map((r) => [r.source + " " + r.kind, r.target]));
    mapData = found.map((f) => ({
      source: f.source, kind: f.kind, normalized: f.normalized,
      active: f.active, retired: f.retired, suggest: f.suggest,
      target: previous.get(f.source + " " + f.kind) || f.suggest,
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
    };
  }
  return {
    job_groups: [...groups],
    grids,
    payout: payoutValues(),
    benefit_rules: ruleValues(),
    longterm_rules: longtermValues(),
    mapping: mapData.map((r) => [r.source, r.kind, r.target]),
  };
}

function renderState(state) {
  groups = state.job_groups?.length ? [...state.job_groups] : [...META.default_groups];
  $("ed-groups").value = groups.join(", ");
  for (const spec of META.sheets) {
    const item = state.grids?.[spec.sheet] || {};
    renderGrid(spec.sheet, item.key || spec.key, item.rows || []);
  }
  renderPayout(state.payout || {});
  renderRules(state.benefit_rules || {});
  renderLongterm(state.longterm_rules || {});
  const scanned = new Map(mapData.map((r) => [r.source + " " + r.kind, r]));
  mapData = (state.mapping || []).map(([source, kind, target]) => {
    const seen = scanned.get(source + " " + kind);
    return { source, kind, target,
             normalized: seen?.normalized || "", active: seen?.active || 0,
             retired: seen?.retired || 0, suggest: seen?.suggest || target };
  });
  renderMap();
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
    state.grids[spec.sheet].rows = state.grids[spec.sheet].rows.map((row) => {
      const byGroup = Object.fromEntries(old.map((g, i) => [g, row[i + 1] || ""]));
      return [row[0], ...cleaned.map((g) => byGroup[g] || "")];
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
  const { library } = py("library_list");
  renderLibraryList("금리표", library["금리표"], $("lib-curve-list"));
  renderLibraryList("표준률", library["표준률"], $("lib-rates-list"));

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

function renderLibraryList(kind, data, target) {
  if (!data.entries.length) {
    target.replaceChildren(el("p", { class: "notice" }, "아직 등록된 것이 없습니다."));
    return;
  }
  const rows = data.entries.map((entry) => {
    const isDefault = entry.name === data.default;
    const cells = [
      el("td", {}, entry.name, " ",
         isDefault ? el("span", { class: "badge" }, data.pinned === entry.name ? "기본(지정)" : "기본(최신)") : ""),
      el("td", {}, entry.registered),
      el("td", {},
        el("button", { class: "small", type: "button", onclick: async () => {
          try {
            py("library_pin", { kind, name: data.pinned === entry.name ? "" : entry.name });
            await persistHome();
            refreshLibrary();
          } catch (error) { alert(error.message); }
        } }, data.pinned === entry.name ? "지정 해제" : "기본 지정"),
        " ",
        el("button", { class: "small", type: "button", onclick: async () => {
          if (!confirm(`${kind} '${entry.name}' 등록을 삭제할까요?`)) return;
          try {
            py("library_remove", { kind, name: entry.name });
            await persistHome();
            refreshLibrary();
          } catch (error) { alert(error.message); }
        } }, "삭제")),
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

// ═════════ 산출 ══════════════════════════════════════════════════
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

async function rosterIntoFS() {
  const file = $("roster").files[0];
  if (file) {
    const suffix = file.name.toLowerCase().match(/\.(xls[xm]?)$/);
    return intoFS(file, "/work/명부." + (suffix ? suffix[1] : "xlsx"));
  }
  if (loadedRun) return loadedRun.roster;
  throw new Error("[산출] 탭에서 명부 파일을 먼저 골라 주세요.");
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
      prior_dbo: $("prior_dbo").value, prior_rate: $("prior_rate").value,
    };
    const report = py("run", {
      roster: rosterPath, assumptions: assumptionsPath,
      force: options.force, sensitivity: options.sensitivity,
      longterm: options.longterm,
      prior_dbo: parseNumber(options.prior_dbo),
      prior_rate: parseRate(options.prior_rate),
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
    $("issues").textContent = report.issues +
      (report.excluded.length ? "\n산출 제외: " + report.excluded.join(" · ") : "");

    const rosterFile = $("roster").files[0];
    lastRun = {
      roster: rosterPath, assumptions: assumptionsPath,
      rosterName: rosterFile ? rosterFile.name : (loadedRun?.rosterName || ""),
      report, options,
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

function refreshRuns() {
  const { runs } = py("run_list");
  const target = $("runs-list");
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
    $("prior_dbo").value = options.prior_dbo || "";
    $("prior_rate").value = options.prior_rate || "";
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
  } catch (error) {
    alert(error.message);
  }
}
