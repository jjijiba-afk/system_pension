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

// 새 판을 올려도 휴대폰·아이패드가 옛 화면을 계속 띄우는 일이 있었다.
// 두 군데를 막는다.
//
// `updateViaCache: "none"` — sw.js 자체가 브라우저 HTTP 캐시에 갇히면 새 일꾼을
// 아예 찾지 못한다(최대 24시간). 이 파일만은 늘 새로 받게 한다.
//
// `controllerchange` — 새 일꾼이 자리를 넘겨받았다는 뜻이다. 그때 화면을 한 번
// 새로 고쳐야 새 app.js·app.css 가 실제로 돈다. 안 그러면 파일은 바뀌었는데
// 돌고 있는 것은 옛것인, 가장 알아채기 어려운 상태가 된다.
if ("serviceWorker" in navigator) {
  const hadController = Boolean(navigator.serviceWorker.controller);
  let reloading = false;

  navigator.serviceWorker.register("sw.js", { updateViaCache: "none" })
    .then((registration) => {
      registration.update();
      // 앱을 다시 열 때(홈 화면 앱은 이때가 사실상 유일한 기회다)도 확인한다.
      document.addEventListener("visibilitychange", () => {
        if (!document.hidden) registration.update();
      });
      // 새 일꾼이 들어왔는데 자리를 못 넘겨받는 경우가 있다(iOS 에서 겪었다).
      // 그러면 화면은 옛것인 채로 조용히 남는다. 눈에 보이게 알리고 누르면
      // 넘어가게 한다 — 자동으로 되면 이 띠는 뜨지도 않는다.
      registration.addEventListener("updatefound", () => {
        const fresh = registration.installing;
        if (!fresh) return;
        fresh.addEventListener("statechange", () => {
          if (fresh.state === "installed" && navigator.serviceWorker.controller) {
            showUpdateBar();
          }
        });
      });
    })
    .catch(() => {});

  function showUpdateBar() {
    if (document.getElementById("update-bar")) return;
    const bar = document.createElement("div");
    bar.id = "update-bar";
    bar.innerHTML = "새 판이 준비됐습니다. " +
      "<button type=\"button\" id=\"update-now\">지금 새로고침</button>";
    document.body.prepend(bar);
    document.getElementById("update-now").addEventListener("click", () => {
      location.reload();
    });
  }

  navigator.serviceWorker.addEventListener("controllerchange", () => {
    // 처음 설치되는 순간에도 이 사건이 온다. 그때는 새로 고칠 옛 화면이 없다.
    if (!hadController || reloading) return;
    reloading = true;
    location.reload();
  });
}

let pyodide = null;
let pyApi = null;
let META = null;

// ── 파이썬 경계 ──────────────────────────────────────────────────
// ``quiet`` 를 주면 실패를 던지지 않고 ``{ok:false, error}`` 를 그대로 돌려준다.
// 파이썬이 세어 본 뒤 막는 경우(단체 안에 산출이 남았을 때 등)에 그 문장을
// 사람에게 그대로 보여 주고 다시 물어보기 위한 것이다.
function py(op, args = {}, { quiet = false } = {}) {
  const response = JSON.parse(pyApi(JSON.stringify({ op, ...args })));
  if (!response.ok && !quiet) throw new Error(response.error);
  return response;
}

async function intoFS(file, path) {
  pyodide.FS.writeFile(path, new Uint8Array(await file.arrayBuffer()));
  return path;
}

// 메모리에 있는 /pension-home 을 브라우저 저장소로 밀어 넣는다. 단체·산출
// 내역·등록 자료가 새로고침 뒤에도 남는 것은 전적으로 이 호출 덕이다.
//
// **실패를 삼키면 안 된다.** syncfs 는 오류를 콜백 인자로 넘기는데, 그것을
// 그대로 resolve 하면 성공과 구별되지 않는다. 저장소가 막힌 기기(사설 브라우징,
// 공간 부족)에서 화면은 '저장했습니다' 라고 말하고 새로고침하면 전부 사라진다 —
// 그때는 이미 늦다. 한 번이라도 실패하면 그 사실을 계속 띄워 둔다.
let storageBroken = false;

function reportBrokenStorage(error) {
  storageBroken = true;
  const message =
    "⚠ 이 기기에 저장하지 못했습니다. 지금까지의 단체·산출 내역이 새로고침하면 "
    + "사라집니다. 사설 브라우징 창이거나 저장 공간이 모자란 경우입니다 — "
    + "[자료실] 의 [보관함 내보내기] 로 지금 바로 파일을 남겨 두세요.";
  status(message);
  const note = $("storage-note");
  if (note) {
    note.textContent = message;
    note.className = "warn-box";
  }
  console.error("syncfs 실패", error);
}

const persistHome = () => new Promise((done) => {
  pyodide.FS.syncfs(false, (error) => {
    if (error && !storageBroken) reportBrokenStorage(error);
    done(!error);
  });
});

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
// 이 앱의 자료(단체·산출 내역·자료실)는 브라우저 저장소에 있다. 브라우저는
// 저장 공간이 모자라면 **말없이 지운다.** 아이폰·아이패드는 오래 안 연 홈
// 화면 앱의 자료를 정리하기도 한다.
//
// 영구 저장을 요청해 두면 그 대상에서 빠진다. 요청이 받아들여졌는지는 기기와
// 브라우저가 정하므로, 결과를 [자료실] 에 그대로 적어 둔다 — 보장되지 않는데
// 보장된 줄 알고 내보내기를 건너뛰는 것이 제일 나쁘다.
async function askForPersistentStorage() {
  const note = $("storage-note");
  if (!navigator.storage?.persist) {
    note.textContent =
      "이 브라우저는 자료 보관을 보장하지 않습니다. 결산이 끝날 때마다 "
      + "[보관함 내보내기] 로 파일을 남겨 두세요.";
    return;
  }
  try {
    const kept = await navigator.storage.persisted() || await navigator.storage.persist();
    note.textContent = kept
      ? "✓ 이 기기에 영구 저장이 허용되어, 공간이 모자라도 자료가 지워지지 "
        + "않습니다. 그래도 기기를 바꿀 때를 대비해 가끔 내보내 두세요."
      : "⚠ 영구 저장이 허용되지 않았습니다. 브라우저가 공간이 필요하면 이 앱의 "
        + "자료를 지울 수 있습니다 — 결산이 끝날 때마다 꼭 내보내 두세요.";
    note.className = kept ? "hint ok-text" : "hint bad-text";
  } catch {
    note.textContent = "자료 보관 상태를 확인하지 못했습니다. 내보내기로 "
      + "파일을 남겨 두세요.";
  }
}

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
    syncRunPages();

    status("준비 완료. 명부를 고르고 기초율을 정한 뒤 [산출 실행]을 누르세요.");
    $("run").disabled = false;
    // 화면이 다 뜬 뒤에 묻는다. 이것 때문에 부팅이 늦어질 이유가 없다.
    askForPersistentStorage();
  } catch (error) {
    status("엔진을 준비하지 못했습니다: " + error);
  }
}
boot();

// ── 큰 탭 ────────────────────────────────────────────────────────
const PAGES = [["tab-calc", "page-calc"], ["tab-edit", "page-edit"],
               ["tab-dash", "page-dash"], ["tab-report", "page-report"],
               ["tab-member", "page-member"], ["tab-runs", "page-runs"],
               ["tab-lib", "page-lib"]];
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
  // 다섯 덩어리가 줄바꿈 없이 이어 붙어 있어 어디까지가 한 이야기인지 보이지
  // 않았다. **방식 / 사유별 차등 / 수식 쓰는 법** 셋으로 나누고, 수식은 쓸
  // 사람만 펴 보도록 접어 둔다 — 대부분은 표만 채우고 끝난다.
  const vars = Object.entries(META.formula_variables)
    .map(([k, v]) => `${k} = ${v}`).join("\n");

  const ways = el("div", { class: "hint", style: "white-space:pre-line" },
    "누적 — 표의 값이 그 근속연수까지의 누적 배수입니다 (10년 → 10.0).\n" +
    "누진 — 표의 값이 그 구간에서만 적용할 연 배수입니다.\n" +
    "       0년 1.0 / 5년 1.5 / 10년 2.0 이면\n" +
    "       근속 12년 = 5×1.0 + 5×1.5 + 2×2.0 = 16.5\n" +
    "수식 — 방식을 '수식' 으로 두고 아래 칸에 직접 씁니다.");

  const causes = el("div", { class: "hint", style: "white-space:pre-line" },
    "켜면 그 직군이 " + META.exit_causes.join(" · ") + " 세 열로 갈립니다.\n" +
    "사유마다 다른 배수만 채우면 됩니다 — 비운 열은 왼쪽의 기본 규정을 씁니다.\n" +
    "가산액·근속 하한처럼 배수가 아닌 것은 아래 [퇴직사유별 차등] 에 적습니다.");

  const formula = el("details", { class: "section" },
    el("summary", {}, "수식 쓰는 법"),
    el("div", { class: "section-body" },
      el("div", { class: "hint", style: "white-space:pre-line" }, "변수\n" + vars),
      el("div", { class: "hint", style: "white-space:pre-line" },
         "함수\n" + META.formula_functions.join(" ")),
      el("div", { class: "hint", style: "white-space:pre-line" },
         "예시\n=IF(t<10, t*1.0, 10 + (t-10)*2.0)\n" +
         '=IF(제도="DB", t*1.5, t)\n=MIN(t, 30)')));

  return [
    el("div", { class: "hint" }, el("b", {}, "지급률 방식")),
    ways,
    el("div", { class: "hint" }, el("b", {}, "사유별 차등")),
    causes,
    formula,
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
       "자료요청서 [일반사항] 6번(퇴직금 지급규정)을 여기에 옮깁니다. " +
       "Base-up·승급률·퇴직률·사망률을 '미반영'으로 두면 그 직군에서 해당 요율을 0으로 봅니다 " +
       "— 임원을 정년까지 근무한다고 보는 경우 등."),
    toolbar, el("div", { class: "scroll-x" }, table));
}

function renderPayout(payout) {
  // 임원은 그 자체가 직군 줄이라 정년을 직원/임원으로 나눌 이유가 없다.
  // 파일 서식과 엔진은 그대로 두고(옛 파일이 그대로 읽혀야 한다) 화면만
  // 한 칸으로 합친다 — 저장할 때 임원 정년에도 같은 값을 넣는다.
  const headers = ["직군", "산출 제외", "가입자격(년)", "정년",
    "가산연령", "근속 산정", "단수 처리", "지급액 반올림", "할당",
    "Base-up", "승급률", "퇴직률", "사망률"];
  payoutBody.replaceChildren(el("tr", {}, ...headers.map((h) => el("th", {}, h))));
  for (const group of groups) {
    const item = payout[group] || {};
    const row = {
      excluded: el("input", { type: "checkbox" }),
      min_service: el("input", { type: "text", value: item.min_service ?? "1" }),
      nra: el("input", { type: "text", value: item.nra ?? "60" }),
      add_age: el("input", { type: "text", value: item.add_age ?? "2" }),
      basis: makeSelect(META.service_bases, item.basis || META.service_bases[0]),
      fraction: makeSelect(META.fraction_modes, item.fraction || META.fraction_modes[0]),
      unit: makeSelect(META.rounding_units, item.unit || "없음"),
      // 할당(귀속) 방식. 급여식이 기본이고, 참고 시스템과 맞출 때 근속비례.
      allocation: makeSelect(META.allocations, item.allocation || "급여식"),
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
      // 임원 정년은 화면에서 없앴다. 파일에는 같은 값을 넣어 둔다 — 임원이
      // 직군 칸에 '정규직' 으로 적혀 온 명부에서도 이 줄의 정년이 쓰이게.
      nra: w.nra.value.trim(), executive_nra: w.nra.value.trim(),
      add_age: w.add_age.value.trim(), basis: w.basis.value,
      fraction: w.fraction.value, unit: w.unit.value,
      allocation: w.allocation.value,
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

/** 어느 지급률 열이 정년·중도·사망으로 갈려 있는지. renderState 가 채운다. */
let benefitSplit = [];

/** 그 열이 어느 규정의 어느 사유인지 — 갈라 놓은 열의 머리에 붙일 꼬리표. */
function causeOf(name) {
  const at = name.indexOf("·");
  if (at < 0) return null;
  const [rule, cause] = [name.slice(0, at), name.slice(at + 1)];
  return benefitSplit.includes(rule) && META.exit_causes.includes(cause)
    ? { rule, cause } : null;
}

// 자료요청서 6번에는 '중도퇴직시 / 사망시 / 정년퇴직시' 지급률이 세 줄로
// 갈려 있다. 종전에는 열을 손으로 만들고 [퇴직사유] 탭에서 그 이름을 지목해야
// 해서, 세 줄을 그대로 옮기는 길이 눈에 보이지 않았다. 여기 체크 한 번으로
// 세 열이 생기고 연결까지 끝난다 — 파일에 저장되는 모양은 종전과 같다.
function toggleCauseSplit(rule, split) {
  if (!split && !confirm(
    `'${rule}' 의 사유별 열 세 개를 지웁니다. 그 열에 적은 배수도 함께 사라집니다.`
  )) { renderState(collectState()); return; }
  try {
    const result = py("benefit_split",
                      { state: collectState(), rule, merge: !split });
    renderState(result.state);
    saveEditorLocal();
    $("ed-status").textContent = split
      ? `'${rule}' 을(를) ${META.exit_causes.join("·")} 세 열로 갈랐습니다. ` +
        "사유마다 다른 배수만 채우면 됩니다 — 비운 열은 기본 규정을 씁니다."
      : `'${rule}' 의 사유별 열을 접었습니다.`;
  } catch (error) {
    alert(error.message);
  }
}

/** 지급률 — 누적·누진·수식과 그 수식. */
function benefitColumnPanel(grid, columns, values) {
  const widgets = grid.columnWidgets;
  const states = {};

  // 가른 열은 '<규정>·<사유>' 라 머리글이 길다. 무엇의 어느 사유인지가
  // 표를 보는 동안 늘 보여야 하므로 머리 칸에 사유만 크게 붙인다.
  const head = grid.tbody.firstChild;
  columns.forEach((name, index) => {
    const part = causeOf(name);
    if (part) head.children[index + 1].append(el("div", { class: "cause-tag" },
                                                 part.cause));
  });

  grid.tbody.append(panelRow("사유별 차등", columns, (name) => {
    // 가른 열 자신에게는 다시 물을 것이 없다.
    if (causeOf(name)) return el("span", { class: "hint" }, "↑ 갈라 놓음");
    const box = el("input", { type: "checkbox" });
    box.checked = benefitSplit.includes(name);
    box.addEventListener("change", () => toggleCauseSplit(name, box.checked));
    return box;
  }));
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
    "배수만 다르면 위 [지급률] 표에서 '사유별 차등' 을 켜는 편이 빠릅니다 — " +
    "세 열이 생기고 이 표까지 자동으로 채워집니다.\n" +
    "대체 지급률 규정  그 사유일 때 기본 규정 대신 쓸 '지급률' 탭의 열 이름 " +
    "(예: 정년퇴직에만 다른 배수를 걸 때)\n" +
    "가산 규정        기본 급여에 더할 배수를 내는 규정 " +
    "(예: 사망 시 근속 구간마다 개월분을 더할 때)\n" +
    "가산액(원)       정액 가산. 근속과 무관하게 얹는 금액\n" +
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
       "명부에서 직군 읽어오기"));
  mapSummary = el("div", { class: "hint" }, "명부를 읽으면 조합이 나타납니다. " +
    "(명부 파일은 [산출] 탭에서 고른 것을 씁니다)");
  mapTable = el("tbody");
  const table = el("table", { class: "grid" });
  table.append(mapTable);
  page.append(
    el("div", { class: "hint" },
       "명부 직군이 곧 산출 직군입니다 — 퇴직률·승급률·사망률·정년이 이 " +
       "단위로 걸립니다. 지급률 규정(재직자명부 I열)은 별개의 축이라 여기와 " +
       "무관하게 [지급률] 열로 갑니다. 임원 판정만 여기서 확인하세요."),
    toolbar, mapSummary, el("div", { class: "scroll-x" }, table));
}

function renderMap() {
  // 변환(묶음 배정) 열은 없앴다 — 명부 직군이 곧 산출 직군이다. 직군과
  // 지급규정이 별개의 축이 되면서, 직군을 합성해 만들 이유가 사라졌다.
  const headers = ["명부 직군", "임직원구분", "임원 판정", "재직", "퇴직"];
  mapTable.replaceChildren(el("tr", {}, ...headers.map((h) => el("th", {}, h))));
  for (const row of mapData) {
    row.target = row.source;
    mapTable.append(el("tr", {},
      el("td", { class: "name" }, row.source || "(빈 값)"),
      el("td", { class: "name" }, row.kind || "(빈 값)"),
      el("td", { class: "name" }, row.normalized || ""),
      el("td", { class: "num" }, String(row.active ?? "")),
      el("td", { class: "num" }, String(row.retired ?? ""))));
  }
  if (mapData.length) {
    const people = mapData.reduce((n, r) => n + (r.active || 0) + (r.retired || 0), 0);
    mapSummary.textContent =
      `조합 ${mapData.length}개 · 인원 ${people.toLocaleString()}명`;
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
    // 인원을 이제 알게 됐으니 표준률 규모를 다시 제안한다. 손으로 고른
    // 뒤라면 건드리지 않는다.
    if (!$("ed-size").dataset.touched) $("ed-size").value = suggestedSize();
    saveEditorLocal();
  } catch (error) {
    alert("명부에서 직군을 읽지 못했습니다.\n\n" + error.message);
  }
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
    mapping: mapData.map((r) => [r.source, r.kind, r.source]),
  };
}

//: 열 머리 패널이 읽을 값. 시트마다 state 의 어느 자리에서 오는지.
const PANEL_SOURCE = { benefit: "benefit_rules", longterm: "longterm_rules" };

/** 파이썬 ``cause_split_rules`` 와 같은 판정. 되그릴 때마다 물으면 느리다. */
function splitRulesOf(state) {
  const grid = state.grids?.[benefitSheet()] || {};
  const columns = new Set([...(state.job_groups || []), ...(grid.extra || [])]);
  const linked = new Map(
    (state.exit_causes || []).map((row) => [`${row[0]} ${row[1]}`, row[2] || ""]));
  return [...columns].filter((rule) => !rule.includes("·")
    && META.exit_causes.every((cause) =>
      columns.has(`${rule}·${cause}`)
      && linked.get(`${rule} ${cause}`) === `${rule}·${cause}`));
}

function renderState(state) {
  groups = state.job_groups?.length ? [...state.job_groups] : [...META.default_groups];
  $("ed-groups").value = groups.join(", ");
  benefitSplit = splitRulesOf({ ...state, job_groups: groups });
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
    if (!result.groups.length) {
      alert("명부에서 직군을 찾지 못했습니다.\n\n"
            + "[기본정보] 의 직군 규칙, 또는 재직자명부의 [직군] 칸을 확인하세요.");
      return;
    }
    // 직군과 지급규정은 별개의 축이다. 직군은 퇴직률·승급률·정년(지급규정)
    // 의 줄이 되고, 명부 I열의 규정명은 [지급률] 표의 열이 된다 — 같은
    // 목록에 섞으면 규정 이름으로 퇴직률을 묻는 꼴이 된다.
    applyGroups(result.groups);
    const state = collectState();
    const addExtras = (sheet, names) => {
      const grid = state.grids[sheet];
      const have = new Set([...state.job_groups, ...(grid.extra || [])]);
      for (const name of names || []) {
        if (have.has(name)) continue;
        grid.extra = [...(grid.extra || []), name];
        grid.rows = grid.rows.map((row) => [...row, ""]);
        have.add(name);
      }
    };
    addExtras(benefitSheet(), result.scale_rules);
    addExtras("장기급여지급률", result.longterm_rules);
    renderState(state);
    saveEditorLocal();

    const from = [`직군 ${result.groups.length}개 (${result.groups.join(", ")})`];
    if (result.scale_rules?.length) {
      from.push(`지급률 규정 ${result.scale_rules.length}개`
                + (result.rule_column
                   ? ` — ${result.rule_column}열 [${result.rule_header}]`
                   : "") + ` (${result.scale_rules.join(", ")})`);
    }
    if (result.longterm_rules?.length) {
      from.push(`장기급여 규정 ${result.longterm_rules.length}개`);
    }
    $("ed-status").textContent = "명부에서 " + from.join(" · ")
      + " 을(를) 가져왔습니다. 규정 열마다 지급률을 넣으세요.";

    // 명부에 적혀 왔지만 규정에 옮겨 적기 전에는 산출에 들어가지 않는 것들.
    // 여기서 말해 주지 않으면 '적었는데 왜 안 들어갔나' 로 끝난다.
    const notes = [];
    if (result.rules?.length && !result.rule_column) {
      notes.push("재직자명부에서 규정명 칸을 찾지 못했습니다. 그 칸의 머리글이 "
                 + "아래에 있는지 확인하세요.\n\n"
                 + (result.headers?.length ? result.headers.join("\n") : ""));
    }
    if (result.blank_rule) {
      notes.push(`규정명이 빈 줄이 ${result.blank_rule}명 있습니다. `
                 + "그 사람들은 직군에 걸린 규정으로 산출됩니다.");
    }
    if (result.extra_pay) {
      notes.push(`[${result.extra_pay_column}] 에 금액이 적힌 사람이 `
                 + `${result.extra_pay}명 있습니다. 이 금액은 저절로 더해지지 `
                 + "않습니다 — 사망 위로금이라면 [퇴직사유] 탭에서 사유를 사망으로 "
                 + "두고 가산액에 적으세요.");
    }
    if (notes.length) alert(notes.join("\n\n"));
  } catch (error) {
    alert("명부에서 읽지 못했습니다.\n\n" + error.message);
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

// 내장 표준률의 승급률·중도퇴직률은 상시근로자 300인 미만/이상으로 갈린다.
// 명부를 읽어 두었으면 그 인원으로 한쪽을 잡아 준다 — 제안일 뿐이라 바꿀 수 있다.
function suggestedSize() {
  const people = mapData.reduce((n, r) => n + (r.active || 0), 0);
  const [small, large] = META.standard_sizes;
  return people >= META.standard_size_threshold ? large : small;
}

// 등록해 둔 표준률 워크북에는 규모 개념이 없다 — 파일에 적힌 값이 곧 답이다.
function syncSizeRow() {
  const builtin = $("ed-rates").value === "__builtin__";
  $("ed-size").disabled = !builtin;
  $("ed-size-hint").style.display = builtin ? "" : "none";
}

$("ed-rates").addEventListener("change", syncSizeRow);
$("ed-size").addEventListener("change", () => { $("ed-size").dataset.touched = "1"; });

$("ed-rates-load").addEventListener("click", () => {
  try {
    const name = $("ed-rates").value;
    const size = $("ed-size").value;
    const state = name === "__builtin__"
      ? py("standard_state", { groups, size }).state
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
      (name === "__builtin__"
        ? `내장 표준률 ${META.standard_year} (${size})`
        : `표준률 '${name}'`) +
      " 을(를) 불러왔습니다. 회사에 맞게 고친 뒤 쓰세요.";
  } catch (error) {
    alert(error.message);
  }
});

// 곡선의 기준일과 산출기준일이 다르면 옛 곡선으로 조용히 산출된다. 할인율은
// 결산일마다 달라지므로 이것이 어긋난 채로 넘어가면 채무 전체가 틀린다.
function showCurveDate(baseDate) {
  const box = $("ed-curve-date");
  if (!box) return;
  const wanted = ($("base_date") || {}).value || "";
  if (!baseDate) { box.textContent = ""; box.className = "hint"; return; }
  if (wanted && wanted !== baseDate) {
    box.className = "hint bad-text";
    box.textContent = `이 금리표는 ${baseDate} 곡선입니다. `
      + `산출기준일 ${wanted} 과 다릅니다 — 결산일 곡선으로 바꿔 등록하십시오.`;
  } else {
    box.className = "hint";
    box.textContent = `이 금리표는 ${baseDate} 곡선입니다.`;
  }
}

$("ed-curve-apply").addEventListener("click", () => {
  try {
    const result = py("curve_rows", { name: $("ed-curve").value, grade: $("ed-grade").value });
    showCurveDate(result.base_date);
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
// 접힌 구획은 안이 안 보인다. 몇 개 들어 있는지는 머리줄에 적어 둔다.
function libCount(sectionId, data) {
  const mark = $(sectionId).querySelector("summary .count");
  if (!mark) return;
  const n = data.entries.length;
  if (!n) { mark.textContent = "없음"; return; }
  mark.textContent = data.default ? `${n}개 · 기본 ${data.default}` : `${n}개`;
}

function refreshLibrary() {
  fillTemplates();
  const { library, backup } = py("library_list");
  renderLibraryList("금리표", library["금리표"], $("lib-curve-list"));
  renderLibraryList("표준률", library["표준률"], $("lib-rates-list"));
  renderLibraryList("명부", library["명부"], $("lib-roster-list"), { pin: false });
  renderLibraryList("가정세트", library["가정세트"], $("lib-preset-list"));
  libCount("sec-lib-preset", library["가정세트"]);
  libCount("sec-lib-curve", library["금리표"]);
  libCount("sec-lib-roster", library["명부"]);
  libCount("sec-lib-rates", library["표준률"]);
  $("lib-backup").querySelector("summary .count").textContent =
    backup ? `마지막 ${backup}` : "내보낸 적 없음";

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
    el("option", { value: "__builtin__" }, `내장 표준률 ${META.standard_year}`),
    ...library["표준률"].entries.map((e) => el("option", { value: e.name }, e.name)));
  if (library["표준률"].default) rates.value = library["표준률"].default;

  const size = $("ed-size");
  const kept = size.value;
  size.replaceChildren(
    ...META.standard_sizes.map((s) => el("option", { value: s }, s)));
  size.value = kept || suggestedSize();
  syncSizeRow();

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
    $("roster-hint").textContent = "기본정보 · 재직자명부 · 퇴직자명부 시트가 들어 있는 통합문서";
    $("general-filled").style.display = "none";
    return;
  }
  $("roster").value = "";
  $("roster-hint").textContent =
    `저장된 명부 '${name}' 를 사용합니다. 새 파일을 고르면 그 파일이 우선합니다.`;
  fillFromGeneralSheet();
});

// 명부를 고르면 [기본정보]·[사외적립자산] 에 담당자가 채워 보낸 것을 입력칸에 옮긴다.
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
                  "asset_contributions", "asset_paid", "asset_closing",
                  "unpaid_benefits", "asset_ceiling"]) {
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
      lines.push("명부에 딸려 온 [기본정보]·[사외적립자산] 에서 가져왔습니다 — "
        + info.found.join(" · "));
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
    if ("asset_opening" in fields || "asset_ceiling" in fields
        || "unpaid_benefits" in fields) $("sec-assets").open = true;

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

// 받아 온 명부를 우리 양식으로 되돌려 준다. 회사마다 열 순서와 머리글이
// 달라 매 결산 눈으로 맞춰야 했는데, 한 번 올린 것을 이 모양으로 되받아
// 다음 해에 그것을 채워 달라고 하면 어긋날 자리가 없다.
$("roster-export").addEventListener("click", async () => {
  const button = $("roster-export");
  button.disabled = true;
  try {
    status("명부를 표준양식으로 옮기는 중…");
    const path = await rosterIntoFS();
    const name = currentRosterName().replace(/\.(xls|xlsx|xlsm)$/i, "");
    const made = py("roster_export", { path, name });
    download(made.path, made.file);
    status(`표준양식으로 옮겼습니다 — ${made.summary}`);
    if (made.carried.length) {
      // 우리 열로 못 들어간 것은 버리지 않고 오른쪽에 붙였다. 어느 열인지
      // 말해 주지 않으면 '왜 여기 있지' 로 끝난다.
      alert("알아보지 못한 열은 오른쪽에 그대로 붙였습니다.\n"
            + "회사가 쓰는 열이면 그대로 두셔도 되고, 우리 열과 같은 뜻이면 "
            + "머리글을 우리 이름으로 고쳐 주세요.\n\n"
            + made.carried.join("\n"));
    }
  } catch (error) {
    status("");
    alert("표준양식으로 옮기지 못했습니다.\n\n" + error.message);
  } finally {
    button.disabled = false;
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

// 보고서·사번 조회 탭은 산출이 있어야 쓸 수 있다. 눌러 본 뒤에 '먼저 산출을
// 실행하세요' 라고 하는 대신, 탭을 열자마자 지금 무엇을 볼 수 있는지 알린다.
function syncRunPages() {
  const ready = Boolean(lastRun);
  for (const id of ["report-empty", "member-empty"]) {
    const note = $(id);
    if (note) note.style.display = ready ? "none" : "";
  }
  for (const id of ["report-sev", "report-lt", "lookup-run"]) {
    const button = $(id);
    if (button) button.disabled = !ready;
  }
}
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
      split_remeasurement: $("split_remeasurement").checked,
      base_date: $("base_date").value, period_start: $("period_start").value,
      prior_dbo: $("prior_dbo").value, prior_rate: $("prior_rate").value,
      prior_run: $("prior-run").value,
      past_service_cost: $("past_service_cost").value,
      settlement_obligation: $("settlement_obligation").value,
      prior_longterm_dbo: $("prior_longterm_dbo").value,
      asset_opening: $("asset_opening").value,
      asset_contributions: $("asset_contributions").value,
      asset_paid: $("asset_paid").value,
      asset_closing: $("asset_closing").value,
      unpaid_benefits: $("unpaid_benefits").value,
      expected_contributions: $("expected_contributions").value,
      asset_ceiling: $("asset_ceiling").value,
    };
    if (options.base_date && options.period_start
        && options.period_start >= options.base_date) {
      throw new Error("산출 시작일은 산출기준일보다 앞서야 합니다.");
    }
    const report = py("run", {
      roster: rosterPath, assumptions: assumptionsPath,
      force: options.force, sensitivity: options.sensitivity,
      longterm: options.longterm,
      split_remeasurement: options.split_remeasurement,
      base_date: options.base_date, period_start: options.period_start,
      prior_dbo: parseNumber(options.prior_dbo),
      prior_rate: parseRate(options.prior_rate),
      prior_service_cost: priorLink ? priorLink.values.service_cost : 0,
      prior_assumptions: priorLink ? priorLink.assumptions : "",
      past_service_cost: parseNumber(options.past_service_cost),
      settlement_obligation: parseNumber(options.settlement_obligation),
      prior_longterm_dbo: parseNumber(options.prior_longterm_dbo),
      asset_opening: parseNumber(options.asset_opening),
      asset_contributions: parseNumber(options.asset_contributions),
      asset_paid: parseNumber(options.asset_paid),
      asset_closing: parseNumber(options.asset_closing),
      unpaid_benefits: parseNumber(options.unpaid_benefits),
      expected_contributions: parseNumber(options.expected_contributions),
      // 상한은 "비움"과 "0"이 다르다 — 0 은 초과적립을 전부 깎으라는 뜻이다.
      asset_ceiling: options.asset_ceiling.trim()
        ? String(parseNumber(options.asset_ceiling)) : "",
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
    // 퇴직사유(급부)별. 합은 확정급여채무와 원 단위까지 같다.
    fillTable($("causes"),
      [["퇴직사유", "확정급여채무", "당기근무원가", "급여 현가", "채무 비중"],
       ...(report.causes || [])], 1);
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

    // 산출할 때마다 분석 화면을 그 결과로 다시 그린다 — 두 화면이 다른 회차를
    // 보여 주는 일이 없어야 한다.
    refreshDashboard();
    syncRunPages();

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

// ═════════ 분석 화면 ═════════════════════════════════════════════
// 산출을 마칠 때마다 그 결과로 다시 그린다. 숫자는 전부 파이썬이 낸 값이고
// 여기서는 배치만 한다 — 화면과 결과 엑셀이 어긋나는 사고를 막기 위해서다.

const CAUSES = [
  { key: "중도", color: "var(--navy)", label: "중도퇴직" },
  { key: "사망", color: "var(--plum)", label: "사망퇴직" },
  { key: "정년", color: "var(--teal)", label: "정년퇴직" },
];
const SERIES = ["var(--navy)", "var(--teal)", "var(--plum)", "var(--amber)"];
const DASH = ["", "7 4", "2 4", "10 3 2 3"];

const won = (n) => (n == null ? "—" : Math.round(n).toLocaleString("ko-KR"));
const eok = (n) => (Math.abs(n) >= 1e12 ? (n / 1e12).toFixed(1) + "조"
                  : (n / 1e8).toFixed(Math.abs(n) >= 1e10 ? 0 : 1) + "억");
const pctOf = (n, d = 2) => (n * 100).toFixed(d) + "%";
// 차감 항목은 공시 표에서 쓰는 괄호 표기로.
const signed = (v) => (v < 0 ? `(${won(-v)})` : won(v));
// 눈금은 사람이 읽는 자리에서 끊는다 — 0.1억·0.3억 같은 값은 읽어도 남지 않는다.
function niceStep(max, ticks) {
  const raw = max / ticks, mag = 10 ** Math.floor(Math.log10(raw)), n = raw / mag;
  return mag * (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10);
}

let DASH_DATA = null;   // 마지막으로 그린 분석 자료
let dashScenario = 0;
let dashYear = null;
let dashRate = 0;

function refreshDashboard(employeeId = "") {
  let data;
  try {
    data = py("dashboard", employeeId ? { employee_id: employeeId } : {});
  } catch (error) {
    if (employeeId) { alert(error.message); return; }
    $("dash-empty").style.display = "";
    $("dash-body").style.display = "none";
    DASH_DATA = null;
    return;
  }
  DASH_DATA = data;
  dashScenario = 0;
  dashYear = null;
  $("dash-empty").style.display = "none";
  $("dash-body").style.display = "";
  $("dash-when").textContent = `기준일 ${data.base_date}`;
  $("dash-scope").textContent =
    `${CLIENT} · 재직 ${data.totals.headcount.toLocaleString("ko-KR")}명`
    + (data.issues.오류 || data.issues.경고
       ? ` · 오류 ${data.issues.오류} / 경고 ${data.issues.경고}` : "");
  drawDashStrip();
  drawMemberChart();
  drawDashGroups();
  drawDashRoll();
  drawDashSensitivity();
  drawDashMaturity();
  drawDashRates();
  drawDashLongterm();
  drawDashNextYear();
  refreshDashBadges();
}

function refreshDashBadges() {
  const d = DASH_DATA;
  if (!d) return;
  const mark = (id, text) => {
    const box = $(id); if (box) box.querySelector("summary .count").textContent = text;
  };
  mark("sec-dash-member", `${d.profile.사번} · ${won(d.scenarios[0].dbo)}원`);
  mark("sec-dash-group", `${d.groups.length}개 직군 · 급부 ${(d.causes || []).length}종`);
  mark("sec-dash-roll", d.rollforward.length ? "전기 연결됨" : "전기 미연결");
  mark("sec-dash-sens", d.sensitivity.length ? `${d.sensitivity.length}건` : "끄고 산출");
  mark("sec-dash-mat", `${d.maturity.length}구간`);
  mark("sec-dash-rates", Object.keys(d.curves).join(" · "));
  mark("sec-dash-lt", d.totals.lt_head
    ? `${d.totals.lt_head}명 · ${eok(d.totals.lt_dbo)}원` : "산출 안 함");
  mark("sec-dash-next", d.projection
    ? `기말 채무 ${eok(d.projection.dbo[d.projection.dbo.length - 1][1])}원` : "—");
}

function drawDashStrip() {
  const t = DASH_DATA.totals, net = DASH_DATA.net;
  const rows = [
    ["재직 인원", t.headcount.toLocaleString("ko-KR"), "명"],
    ["확정급여채무", eok(t.dbo), "원"],
    ["당기근무원가", eok(t.sc), "원"],
    ["이자원가 (차기)", eok(t.ic), "원"],
    ["가중평균만기", t.duration.toFixed(2), "년"],
    ["적용 할인율", pctOf(DASH_DATA.single_rate, 3), ""],
  ];
  if (net.length) rows.push(["순확정급여부채", eok(net[net.length - 1][1]), "원"]);
  if (t.lt_dbo) rows.push(["장기급여채무", eok(t.lt_dbo), "원"]);
  $("dash-strip").replaceChildren(...rows.map(([k, v, u]) => {
    const box = el("div", { class: "stat" }, el("dt", {}, k));
    const value = el("dd", {}, v);
    if (u) value.append(el("small", {}, u));
    box.append(value);
    return box;
  }));
}

/* ── 개인별 채무 해부 ── */
const traceYears = (s) => [...new Set(s.trace.map((r) => r.t))].sort((a, b) => a - b);

function drawMemberChart() {
  const d = DASH_DATA, s = d.scenarios[dashScenario];
  const years = traceYears(s);
  // 처음에는 첫 해에 선다 — 누적이 몇 %에서 출발하는지 보이고 큰 막대까지 따라간다.
  if (dashYear === null || !years.includes(dashYear)) dashYear = years[0];

  const peak = Math.max(...d.scenarios.flatMap((sc) =>
    traceYears(sc).map((t) => sc.trace.filter((r) => r.t === t)
      .reduce((sum, r) => sum + r.dbo, 0))));
  const step = niceStep(peak, 4), ymax = peak * 1.05;
  const unit = ymax >= 1e9 ? { div: 1e8, name: "억원" } : { div: 1e6, name: "백만원" };

  const W = 1000, H = 380, L = 74, R = 52, T = 34, B = 42;
  const iw = W - L - R, ih = H - T - B;
  const bw = Math.min(38, (iw / years.length) * 0.62);
  const xOf = (i) => L + (iw / years.length) * (i + 0.5);
  const yOf = (v) => T + ih - (v / ymax) * ih;
  const p = [];

  for (let v = 0; v <= ymax; v += step) {
    const y = yOf(v);
    p.push(`<line x1="${L}" x2="${W - R}" y1="${y}" y2="${y}" stroke="var(--line)" opacity=".5"/>`);
    p.push(`<text x="${L - 9}" y="${y + 4}" text-anchor="end" font-size="11"
      fill="var(--hint)">${(v / unit.div).toFixed(0)}</text>`);
  }
  p.push(`<text x="${L - 9}" y="${T - 12}" text-anchor="end" font-size="10.5"
    fill="var(--hint)">${unit.name}</text>`);
  [0, 0.5, 1].forEach((q) => p.push(`<text x="${W - R + 9}" y="${T + ih - q * ih + 4}"
    font-size="11" fill="var(--amber)">${q * 100}%</text>`));

  years.forEach((t, i) => {
    const rows = s.trace.filter((r) => r.t === t);
    let acc = 0;
    const on = t === dashYear;
    CAUSES.forEach((c) => {
      const v = rows.filter((r) => r.cause === c.key).reduce((sum, r) => sum + r.dbo, 0);
      if (v <= 0) return;
      p.push(`<rect x="${xOf(i) - bw / 2}" y="${yOf(acc + v)}" width="${bw}"
        height="${(v / ymax) * ih}" fill="${c.color}" opacity="${on ? 1 : 0.62}"/>`);
      acc += v;
    });
    p.push(`<rect class="hit" data-t="${t}" x="${xOf(i) - iw / years.length / 2}" y="${T}"
      width="${iw / years.length}" height="${ih}" fill="transparent" tabindex="0"
      role="button" aria-label="경과 ${t}년차"/>`);
    p.push(`<text x="${xOf(i)}" y="${H - B + 18}" text-anchor="middle" font-size="11"
      fill="${on ? "var(--ink)" : "var(--hint)"}" font-weight="${on ? 700 : 400}">${t}</text>`);
  });

  const line = years.map((t, i) => {
    const r = s.trace.find((row) => row.t === t);
    return `${xOf(i)},${T + ih - r.survival * ih}`;
  }).join(" ");
  p.push(`<polyline points="${line}" fill="none" stroke="var(--amber)" stroke-width="2"
    stroke-linejoin="round"/>`);
  years.forEach((t, i) => {
    const r = s.trace.find((row) => row.t === t);
    p.push(`<circle cx="${xOf(i)}" cy="${T + ih - r.survival * ih}"
      r="${t === dashYear ? 4 : 2.4}" fill="var(--amber)"/>`);
  });
  p.push(`<line x1="${L}" x2="${W - R}" y1="${T + ih}" y2="${T + ih}" stroke="var(--line)"/>`);
  p.push(`<text x="${W - R}" y="${H - 6}" text-anchor="end" font-size="11"
    fill="var(--hint)">경과연수 (년)</text>`);

  const svg = $("dash-chart");
  svg.innerHTML = p.join("");
  svg.querySelectorAll(".hit").forEach((h) => {
    const pick = () => { dashYear = +h.dataset.t; drawMemberChart(); };
    h.addEventListener("click", pick);
    h.addEventListener("focus", pick);
    h.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); }
    });
  });

  const m = d.profile;
  $("dash-who").textContent = `${m.사번} ${m.성명} · ${m.연령}세 ${m.성별} · ${m.직군}`
    + ` · 근속 ${m.근속}년 · 정년 ${m.정년}세 · 월평균임금 ${won(m.월평균임금)}원`
    + ` · 지급률규정 ${m.지급률규정}`;
  drawDashChips();
  drawDashReadout(s);
  drawDashTrace(s);
  refreshDashBadges();
}

function drawDashChips() {
  const base = DASH_DATA.scenarios[0].dbo;
  const groups = [["", [0]], ["할인율", [1, 2]], ["임금상승률", [3, 4]],
                  ["사망률", [5, 6]], ["퇴직률", [7, 8]]]
    .filter(([, list]) => list.every((i) => DASH_DATA.scenarios[i]));
  $("dash-chips").innerHTML = groups.map(([tag, list]) => `
    <span class="chipset">${tag ? `<span class="tag">${tag}</span>` : ""}${
      list.map((i) => {
        const s = DASH_DATA.scenarios[i], delta = (s.dbo - base) / base;
        const name = i === 0 ? "기준" : s.label.replace(/^\S+\s/, "");
        return `<button class="chip" type="button" data-s="${i}"
          aria-pressed="${i === dashScenario}">${name}${i === 0 ? ""
          : `<span class="delta">${delta >= 0 ? "+" : ""}${(delta * 100).toFixed(1)}%</span>`}</button>`;
      }).join("")}</span>`).join("");
  $("dash-chips").querySelectorAll(".chip").forEach((b) =>
    b.addEventListener("click", () => { dashScenario = +b.dataset.s; drawMemberChart(); }));
}

function drawDashReadout(s) {
  const rows = s.trace.filter((r) => r.t === dashYear);
  const total = rows.reduce((sum, r) => sum + r.dbo, 0);
  const cum = s.trace.filter((r) => r.t <= dashYear).reduce((sum, r) => sum + r.dbo, 0);
  const f = rows[0];
  const causes = rows.map((r) => {
    const c = CAUSES.find((x) => x.key === r.cause) || CAUSES[0];
    return `<div class="cause"><b class="nm"><i style="background:${c.color}"></i>${c.label}</b>
      <div class="expr">귀속액 ${won(r.attributed)}<br>× 퇴직확률 ${pctOf(r.exit_probability, 3)}<br>
      × 할인계수 ${r.discount.toFixed(6)}<br>= <b>${won(r.dbo)}원</b></div></div>`;
  }).join("");
  $("dash-readout").innerHTML = `
    <div><div class="head">경과 ${dashYear}년차 · 지급시점 ${f.timing}년</div>
      <div class="big">${won(total)}<small> 원</small></div>
      <dl><dt>퇴직 시 연령</dt><dd>${f.age.toFixed(1)}세</dd>
        <dt>그때 근속</dt><dd>${f.service.toFixed(2)}년</dd>
        <dt>그때 월평균임금</dt><dd>${won(f.wage)}</dd>
        <dt>연초 잔존확률</dt><dd>${pctOf(f.survival)}</dd>
        <dt>누적 채무</dt><dd>${(cum / s.dbo * 100).toFixed(1)}%</dd></dl></div>
    <div class="causes">${causes}</div>`;
}

function drawDashTrace(s) {
  const head = ["경과", "사유", "연령", "근속", "월평균임금", "중도퇴직률", "사망률",
                "잔존확률", "퇴직확률", "퇴직 시 지급액", "귀속액", "할인계수", "채무 기여"];
  const body = s.trace.map((r) => {
    const c = CAUSES.find((x) => x.key === r.cause) || CAUSES[0];
    return `<tr class="pick ${r.t === dashYear ? "on" : ""}" data-t="${r.t}">
      <td>${r.t}년차</td>
      <td><span class="dot" style="background:${c.color}"></span>${c.label}</td>
      <td class="num">${r.age.toFixed(1)}</td><td class="num">${r.service.toFixed(2)}</td>
      <td class="num">${won(r.wage)}</td><td class="num">${pctOf(r.withdrawal)}</td>
      <td class="num">${pctOf(r.mortality, 3)}</td><td class="num">${pctOf(r.survival)}</td>
      <td class="num">${pctOf(r.exit_probability, 3)}</td><td class="num">${won(r.benefit)}</td>
      <td class="num">${won(r.attributed)}</td><td class="num">${r.discount.toFixed(6)}</td>
      <td class="num">${won(r.dbo)}</td></tr>`;
  }).join("");
  $("dash-trace").innerHTML = `<tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr>${body}
    <tr class="total"><td colspan="12">합계 = 확정급여채무</td>
      <td class="num">${won(s.dbo)}</td></tr>`;
  $("dash-trace").querySelectorAll("tr.pick").forEach((tr) =>
    tr.addEventListener("click", () => { dashYear = +tr.dataset.t; drawMemberChart(); }));
  $("sec-dash-trace").querySelector("summary .count").textContent =
    `${s.trace.length}줄 · 합계 ${won(s.dbo)}원`;
}

/* ── 직군·증감·자산 ── */
function dataTable(node, head, rows, foot) {
  node.innerHTML = `<tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr>`
    + rows.map((r) => {
        const cells = (r.cells || r);
        return `<tr class="${r.cls || ""}">` + cells.map((c, i) =>
          i === 0 ? `<td>${c}</td>`
                  : `<td class="num${String(c).startsWith("(") ? " neg" : ""}">${c}</td>`
        ).join("") + "</tr>";
      }).join("")
    + (foot || "");
}

function drawDashGroups() {
  const d = DASH_DATA, t = d.totals;
  dataTable($("dash-groups"), ["직군", "인원", "확정급여채무", "당기근무원가", "비중"],
    d.groups.map((g) => [g.name, `${g.n.toLocaleString("ko-KR")}명`,
      won(g.dbo), won(g.sc), pctOf(g.dbo / t.dbo, 1)]),
    `<tr class="total"><td>합계</td><td class="num">${t.headcount.toLocaleString("ko-KR")}명</td>
     <td class="num">${won(t.dbo)}</td><td class="num">${won(t.sc)}</td>
     <td class="num">100.0%</td></tr>`);
  const ex = Object.entries(d.excluded);
  $("dash-excluded").innerHTML = ex.length
    ? `<p class="hint" style="margin-top:8px">산출 제외 — ${
        ex.map(([k, v]) => `<b>${v}명</b> ${k}`).join(" / ")}</p>`
    : "";

  const causes = d.causes || [];
  dataTable($("dash-causes"),
    ["급부 (퇴직사유)", "확정급여채무", "당기근무원가", "급여 현가", "채무 비중"],
    causes.map((c) => [c.name, won(c.dbo), won(c.sc), won(c.pv),
                       pctOf(t.dbo ? c.dbo / t.dbo : 0, 1)]),
    causes.length
      ? `<tr class="total"><td>합계</td>
         <td class="num">${won(causes.reduce((s, c) => s + c.dbo, 0))}</td>
         <td class="num">${won(causes.reduce((s, c) => s + c.sc, 0))}</td>
         <td class="num">${won(causes.reduce((s, c) => s + c.pv, 0))}</td>
         <td class="num">100.0%</td></tr>`
      : "");
}

function drawDashRoll() {
  const d = DASH_DATA;
  const kv = (rows) => rows.map(([k, v], i) => ({
    cls: i === 0 || i === rows.length - 1 ? "total" : "", cells: [k, signed(v)] }));
  const box = $("dash-roll");
  if (!d.rollforward.length && !d.assets.length) {
    box.innerHTML = `<p class="hint">전기 산출 결과와 사외적립자산을 넣지 않아
      증감분석·자산 증감표가 없습니다. [산출] 탭의 5·6번 칸을 채우면 채워집니다.</p>`;
    return;
  }
  box.innerHTML = `<div class="two-up">
    <div><h4>확정급여채무 변동내역</h4><div class="scroll-x">
      <table class="data" id="dash-roll-tbl"></table></div></div>
    <div><h4>사외적립자산 · 순확정급여부채</h4><div class="scroll-x">
      <table class="data" id="dash-asset-tbl"></table></div>
      <div class="scroll-x"><table class="data" id="dash-net-tbl"></table></div></div>
  </div>`;
  if (d.rollforward.length) {
    dataTable($("dash-roll-tbl"), ["항목", "금액"], kv(d.rollforward));
    if (d.assumption_steps.length) {
      $("dash-roll-tbl").insertAdjacentHTML("afterend",
        `<h4 style="margin-top:12px">가정변경효과 분해</h4>`
        + `<div class="scroll-x"><table class="data" id="dash-steps"></table></div>`
        + `<p class="hint">사망률 → 퇴직률 → 임금상승률 → 할인율 차례로 하나씩`
        + ` 갈아 끼우며 잰 값입니다. 합계는 가정변경효과와 일치합니다.</p>`);
      dataTable($("dash-steps"), ["가정", "금액"],
        d.assumption_steps.map(([k, v]) => [k, signed(v)]).concat([{
          cls: "total",
          cells: ["합계", signed(d.assumption_steps.reduce((s2, r) => s2 + r[1], 0))] }]));
    }
  } else {
    $("dash-roll-tbl").innerHTML =
      `<tr><td class="hint">전기 산출을 연결하지 않았습니다.</td></tr>`;
  }
  if (d.assets.length) {
    dataTable($("dash-asset-tbl"), ["항목", "금액"], kv(d.assets));
    dataTable($("dash-net-tbl"), ["순확정급여부채", `적립비율 ${pctOf(d.funded, 1)}`],
      d.net.map(([k, v], i) => ({
        cls: i === d.net.length - 1 ? "total" : "", cells: [k, signed(v)] })));
    if (d.ceiling) {
      $("dash-net-tbl").insertAdjacentHTML("afterend",
        `<p class="hint">자산인식상한 ${won(d.ceiling.limit)}원 · 초과적립액 `
        + `${won(d.ceiling.surplus)}원 → 자산차감 ${won(d.ceiling.effect)}원 (문단 64)</p>`);
    }
  } else {
    $("dash-asset-tbl").innerHTML =
      `<tr><td class="hint">사외적립자산 입력이 없습니다.</td></tr>`;
    $("dash-net-tbl").innerHTML = "";
  }
}

function drawDashNextYear() {
  const p = DASH_DATA.projection;
  const box = $("dash-next");
  if (!p) { box.innerHTML = `<p class="hint">차년도 예측이 없습니다.</p>`; return; }
  const kv = (rows) => rows.map(([k, v], i) => ({
    cls: i === 0 || i === rows.length - 1 ? "total" : "", cells: [k, signed(v)] }));
  box.innerHTML = `<div class="two-up">
    <div><h4>차년도 예상 퇴직급여 비용</h4><div class="scroll-x">
      <table class="data" id="dash-next-expense"></table></div>
      <h4 style="margin-top:12px">차년도 확정급여채무 예측</h4><div class="scroll-x">
      <table class="data" id="dash-next-dbo"></table></div></div>
    <div><h4>차년도 사외적립자산 예측</h4><div class="scroll-x">
      <table class="data" id="dash-next-assets"></table></div></div>
  </div>`;
  dataTable($("dash-next-expense"), ["항목", "금액"],
    p.expense.map(([k, v], i) => ({
      cls: i === p.expense.length - 1 ? "total" : "", cells: [k, signed(v)] })));
  dataTable($("dash-next-dbo"), ["항목", "금액"], kv(p.dbo));
  if (p.assets.length) {
    dataTable($("dash-next-assets"), ["항목", "금액"], kv(p.assets));
  } else {
    $("dash-next-assets").innerHTML =
      `<tr><td class="hint">사외적립자산 입력이 없습니다.</td></tr>`;
  }
}

function drawDashSensitivity() {
  const cases = DASH_DATA.sensitivity;
  if (!cases.length) {
    $("dash-sens").innerHTML = "";
    $("dash-sens-tbl").innerHTML =
      `<tr><td class="hint">민감도분석을 끄고 산출했습니다. [산출] 탭 옵션에서 켜세요.</td></tr>`;
    return;
  }
  const W = 1000, H = 250, L = 152, R = 62, T = 12, B = 20;
  const iw = W - L - R, ih = H - T - B, mid = L + iw / 2;
  const bh = Math.min(20, (ih / cases.length) * 0.6);
  const vmax = Math.max(...cases.map((c) => Math.abs(c[3]))) * 1.25 || 1;
  const xOf = (r) => mid + (r / vmax) * (iw / 2);
  const p = [`<line x1="${mid}" x2="${mid}" y1="${T}" y2="${T + ih}" stroke="var(--line)"/>`];
  cases.forEach(([name, , , ratio], i) => {
    const y = T + (ih / cases.length) * (i + 0.5);
    p.push(`<rect x="${Math.min(mid, xOf(ratio))}" y="${y - bh / 2}"
      width="${Math.abs(xOf(ratio) - mid)}" height="${bh}"
      fill="${ratio >= 0 ? "var(--navy)" : "var(--teal)"}" opacity=".8"/>`);
    p.push(`<text x="${L - 10}" y="${y + 4}" text-anchor="end" font-size="11.5"
      fill="var(--ink)">${name}</text>`);
    p.push(`<text x="${xOf(ratio) + (ratio >= 0 ? 7 : -7)}" y="${y + 4}"
      text-anchor="${ratio >= 0 ? "start" : "end"}" font-size="11"
      fill="var(--hint)">${ratio >= 0 ? "+" : ""}${(ratio * 100).toFixed(2)}%</text>`);
  });
  $("dash-sens").innerHTML = p.join("");
  dataTable($("dash-sens-tbl"), ["가정 변동", "확정급여채무", "증감액", "변화율"],
    cases.map(([n, dbo, ch, r]) => [n, won(dbo), signed(ch),
      `${r >= 0 ? "+" : ""}${(r * 100).toFixed(2)}%`]));
}

function drawDashMaturity() {
  const rows = DASH_DATA.maturity;
  const W = 1000, H = 260, L = 62, R = 16, T = 14, B = 66;
  const iw = W - L - R, ih = H - T - B;
  const slot = iw / rows.length, bw = Math.min(15, slot * 0.34);
  const peak = Math.max(...rows.flatMap(([, a, b]) => [a, b])) || 1;
  const step = niceStep(peak, 4), vmax = peak * 1.05;
  const yOf = (v) => T + ih - (v / vmax) * ih;
  const p = [];
  for (let v = 0; v <= vmax; v += step) {
    const y = yOf(v);
    p.push(`<line x1="${L}" x2="${W - R}" y1="${y}" y2="${y}" stroke="var(--line)" opacity=".5"/>`);
    p.push(`<text x="${L - 8}" y="${y + 4}" text-anchor="end" font-size="11"
      fill="var(--hint)">${(v / 1e8).toFixed(0)}</text>`);
  }
  p.push(`<text x="${L - 8}" y="${T - 2}" text-anchor="end" font-size="10.5"
    fill="var(--hint)">억원</text>`);
  rows.forEach(([label, dbo, paid], i) => {
    const cx = L + slot * (i + 0.5);
    p.push(`<rect x="${cx - bw - 1}" y="${yOf(dbo)}" width="${bw}"
      height="${(dbo / vmax) * ih}" fill="var(--navy)" opacity=".85"/>`);
    p.push(`<rect x="${cx + 1}" y="${yOf(paid)}" width="${bw}"
      height="${(paid / vmax) * ih}" fill="var(--teal)" opacity=".85"/>`);
    const short = label.replace("년미만", "").replace("년이상~", "–").replace("년", "");
    p.push(`<text x="${cx}" y="${T + ih + 14}" text-anchor="end" font-size="10"
      fill="var(--hint)" transform="rotate(-45 ${cx} ${T + ih + 14})">${short}</text>`);
  });
  p.push(`<line x1="${L}" x2="${W - R}" y1="${T + ih}" y2="${T + ih}" stroke="var(--line)"/>`);
  $("dash-mat").innerHTML = p.join("");
}

function drawDashRates() {
  const names = Object.keys(DASH_DATA.curves);
  $("dash-rate-chips").innerHTML = names.map((n, i) =>
    `<button class="chip" type="button" data-r="${i}"
      aria-pressed="${i === dashRate}">${n}</button>`).join("");
  $("dash-rate-chips").querySelectorAll(".chip").forEach((b) =>
    b.addEventListener("click", () => { dashRate = +b.dataset.r; drawDashRates(); }));

  const key = names[dashRate] || names[0];
  const src = DASH_DATA.curves[key] || {};
  const series = Object.keys(src).filter((n) => Object.keys(src[n]).length);
  if (!series.length) { $("dash-rate").innerHTML = ""; $("dash-rate-legend").innerHTML = ""; return; }
  const keys = [...new Set(series.flatMap((n) => Object.keys(src[n]).map(Number)))]
    .sort((a, b) => a - b);
  const vmax = Math.max(...series.flatMap((n) => Object.values(src[n]))) || 1;

  const W = 1000, H = 300, L = 66, R = 22, T = 18, B = 34;
  const iw = W - L - R, ih = H - T - B;
  const span = Math.max(1, keys[keys.length - 1] - keys[0]);
  const xs = (k) => L + ((k - keys[0]) / span) * iw;
  const ys = (v) => T + ih - (v / vmax) * ih;
  const p = [];
  for (let g = 0; g <= 4; g++) {
    const v = (vmax / 4) * g, y = ys(v);
    p.push(`<line x1="${L}" x2="${W - R}" y1="${y}" y2="${y}" stroke="var(--line)" opacity=".5"/>`);
    p.push(`<text x="${L - 8}" y="${y + 4}" text-anchor="end" font-size="11"
      fill="var(--hint)">${(v * 100).toFixed(1)}%</text>`);
  }
  // 직군이 같은 표를 쓰면 선이 정확히 겹친다. 파선을 달리해 몇 개인지 남긴다.
  series.forEach((n, i) => {
    const pts = keys.filter((k) => src[n][k] !== undefined)
      .map((k) => `${xs(k)},${ys(src[n][k])}`).join(" ");
    p.push(`<polyline points="${pts}" fill="none" stroke="${SERIES[i % 4]}"
      stroke-width="2" stroke-linejoin="round" stroke-dasharray="${DASH[i % DASH.length]}"/>`);
  });
  const stride = Math.ceil(keys.length / 10);
  keys.filter((k, i) => i % stride === 0 && xs(k) < W - R - 46)
      .forEach((k) => p.push(`<text x="${xs(k)}" y="${H - 12}" text-anchor="middle"
        font-size="11" fill="var(--hint)">${k}</text>`));
  p.push(`<text x="${W - R}" y="${H - 12}" text-anchor="end" font-size="11"
    fill="var(--hint)">${DASH_DATA.curve_axis[key] || ""}</text>`);
  p.push(`<line x1="${L}" x2="${W - R}" y1="${T + ih}" y2="${T + ih}" stroke="var(--line)"/>`);
  $("dash-rate").innerHTML = p.join("");
  const same = series.length > 1 &&
    series.every((n) => JSON.stringify(src[n]) === JSON.stringify(src[series[0]]));
  $("dash-rate-legend").innerHTML = series.map((n, i) =>
    `<span><i style="background:${SERIES[i % 4]}"></i>${n}</span>`).join("")
    + (same ? `<span style="color:var(--amber)">※ 세 직군이 같은 표를 씁니다</span>` : "");
}

function drawDashLongterm() {
  const t = DASH_DATA.totals;
  const roll = DASH_DATA.longterm_roll;
  if (!roll.length && !t.lt_head) {
    $("dash-lt").innerHTML = `<tr><td class="hint">${
      DASH_DATA.has_longterm
        ? "장기급여 대상자가 없습니다. 명부의 [장기급여 대상] 열과 장기급여 지급률 규정을 확인하세요."
        : "장기급여를 끄고 산출했습니다. [산출] 탭 옵션에서 켜세요."}</td></tr>`;
    return;
  }
  if (roll.length) {
    dataTable($("dash-lt"), ["항목", "금액"], roll.map(([k, v], i) => ({
      cls: i === 0 || i === roll.length - 1 ? "total" : "", cells: [k, signed(v)] })));
    return;
  }
  dataTable($("dash-lt"), ["항목", "금액"], [
    ["산출 대상 인원", `${t.lt_head.toLocaleString("ko-KR")}명`],
    ["장기급여채무", won(t.lt_dbo)],
    ["당기근무원가", won(t.lt_sc)],
    ["이자원가 (차기)", won(t.lt_ic)],
  ]);
}

$("dash-print").addEventListener("click", () => {
  // 접어 둔 구획이 종이에서 빠지면 안 된다. 모두 펼친 뒤 인쇄를 부른다.
  document.querySelectorAll("#page-dash details.section")
    .forEach((box) => { box.open = true; });
  const d = DASH_DATA;
  $("dash-print-title").textContent = d
    ? `${CLIENT} · 확정급여채무 산출 결과 (기준일 ${d.base_date})` : "";
  drawMemberChart();
  setTimeout(() => window.print(), 120);
});
$("dash-refresh").addEventListener("click", () => refreshDashboard());
$("dash-emp-go").addEventListener("click", () =>
  refreshDashboard($("dash-emp").value.trim()));
$("dash-emp").addEventListener("keydown", (e) => {
  if (e.key === "Enter") refreshDashboard($("dash-emp").value.trim());
});

// ═════════ 계리평가 보고서 ═══════════════════════════════════════
// 표지부터 용어정리까지 갖춘 인쇄용 HTML 을 파이썬이 만들고, 여기서는
// 미리보기(iframe)와 인쇄만 한다. 인쇄 화면에서 PDF 로 저장하면 끝이다.

function showValuationReport(kind) {
  if (!lastRun) { alert("먼저 산출을 실행하세요."); return; }
  try {
    const { path, filename } = py("report_html", { kind, client: CLIENT, work: "/work" });
    const page = new TextDecoder().decode(pyodide.FS.readFile(path));
    $("print-title").textContent = filename.replace(/\.html$/, "");
    const frame = $("print-frame");
    frame.srcdoc = page;
    $("print-go").onclick = () => {
      frame.contentWindow.focus();
      frame.contentWindow.print();
    };
    $("print-download").onclick = () => download(path, filename, "text/html");
    $("print-dialog").showModal();
  } catch (error) {
    alert(error.message);
  }
}
$("report-sev").addEventListener("click", () => showValuationReport("severance"));
$("report-lt").addEventListener("click", () => showValuationReport("longterm"));

// ═════════ 사번 조회 ═════════════════════════════════════════════
// 그 한 명을 같은 명부·기초율로 다시 산출해 연차별 근거를 보여준다.
// 엔진의 같은 코드가 돌므로 여기 나온 채무 합은 전체 산출의 그 사람 몫과 같다.


function kvTable(pairs) {
  return el("div", { class: "scroll-x" }, el("table", { class: "data" },
    ...pairs.map(([k, v]) => el("tr", {}, el("td", {}, String(k)),
                                 el("td", { class: "num" }, String(v))))));
}

function traceTable(trace) {
  // '귀속액' 은 기준일까지 쌓인 몫, '당기 귀속액' 은 그중 올해 한 해가 더한
  // 몫이다. 둘 다 확률·할인 **전** 금액이라 당기근무원가 자체가 아니다 —
  // 확률과 할인계수를 곱한 것이 오른쪽 끝의 '당기근무원가 기여' 다.
  const head = ["연차", "시점", "연령", "근속", "월평균임금", "중도퇴직률", "사망률",
                "연초 재직확률", "퇴직사유", "그 해 퇴직확률", "지급액",
                "귀속액 (누적)", "당기 귀속액", "할인계수", "DBO 기여",
                "당기근무원가 기여"];
  const rows = trace.map((r) => [
    r.t, r.timing, r.age.toFixed(1), r.service.toFixed(2), won(r.wage),
    pctOf(r.withdrawal), pctOf(r.mortality, 3), pctOf(r.survival),
    r.cause, pctOf(r.exit_probability, 3), won(r.benefit),
    won(r.attributed), won(r.unit), r.discount.toFixed(6),
    won(r.dbo), won(r.service_cost),
  ]);
  return el("div", { class: "scroll-x" }, el("table", { class: "data" },
    el("tr", {}, ...head.map((h) => el("th", {}, h))),
    ...rows.map((cells) => el("tr", {},
      ...cells.map((c, i) => el("td", { class: i >= 4 ? "num" : "" }, String(c)))))));
}

function longtermTraceTable(trace) {
  const head = ["지급 항목", "도달 근속", "지급 시점(년)", "표 값", "지급액",
                "도달(잔존) 확률", "할인계수", "귀속비율", "DBO 기여", "근무원가 기여"];
  const rows = trace.map((r) => [
    r.item, r.target_service, r.timing.toFixed(2), r.value, won(r.benefit),
    pctOf(r.survival), r.discount.toFixed(6), pctOf(r.share), won(r.dbo),
    won(r.service_cost),
  ]);
  return el("div", { class: "scroll-x" }, el("table", { class: "data" },
    el("tr", {}, ...head.map((h) => el("th", {}, h))),
    ...rows.map((cells) => el("tr", {},
      ...cells.map((c, i) => el("td", { class: i >= 3 ? "num" : "" }, String(c)))))));
}

function showMemberDetail(args, sourceLabel) {
  let detail;
  try {
    detail = py("member_detail", { work: "/work", ...args });
  } catch (error) {
    alert(error.message);
    return;
  }
  const first = detail.rows[0];
  const who = first
    ? `${first.profile["사번"] || first.profile["성명"]} — ${first.profile["성명"]}`
    : (detail.retired[0] ? `${detail.retired[0]["사번"]} — ${detail.retired[0]["성명"]} (퇴직자)` : "");
  $("member-title").textContent = `개인별 산출 근거 · ${who}`;
  $("member-note").textContent =
    `${sourceLabel} · 산출기준일 ${detail.base_date} · 할인율 ${pctOf(detail.discount_rate, 3)}`;

  const body = $("member-body");
  body.replaceChildren();

  detail.rows.forEach((row, i) => {
    if (detail.rows.length > 1) {
      body.append(el("h2", { style: "font-size:1rem;color:var(--navy)" },
        `지급 구간 ${i + 1} / ${detail.rows.length}`));
    }
    if (row.excluded) {
      body.append(el("p", { class: "warn-box" }, `산출 제외: ${row.excluded}`));
    }
    body.append(el("h3", {}, "인적사항"), kvTable(Object.entries(row.profile)));
    body.append(el("h3", {}, "적용한 규정·가정"), kvTable(Object.entries(row.applied)));
    body.append(el("h3", {}, "산출 결과"),
      kvTable(Object.entries(row.result).map(([k, v]) =>
        [k, k.includes("듀레이션") ? Number(v).toFixed(2) + "년" : won(v) + "원"])));
    if (row.trace.length) {
      body.append(el("h3", {}, "연차별 계산 근거 (퇴직급여)"), traceTable(row.trace));
    }
  });

  detail.longterm.forEach((block) => {
    body.append(el("h3", {}, "장기종업원급여"));
    if (block.excluded) {
      body.append(el("p", { class: "notice" }, `산출 제외: ${block.excluded}`));
      return;
    }
    body.append(kvTable([
      ["지급유형", block["지급유형"] || "—"],
      ["1일 통상임금", won(block["1일 통상임금"]) + "원"],
      ["다음 지급 근속", block["다음 지급 근속"] == null ? "—" : block["다음 지급 근속"] + "년"],
      ["남은 지급 시점 수", block["남은 지급 시점 수"]],
      ...Object.entries(block.result).map(([k, v]) => [k, won(v) + "원"]),
    ]));
    if ((block.trace || []).length) {
      body.append(el("h3", {}, "지급 시점별 계산 근거 (장기급여)"),
                  longtermTraceTable(block.trace));
    }
  });

  if (detail.retired.length) {
    body.append(el("h3", {}, "퇴직자명부 내역"));
    detail.retired.forEach((row) => body.append(kvTable(
      Object.entries(row).map(([k, v]) =>
        [k, typeof v === "number" ? won(v) + "원" : (v || "—")]))));
  }

  if (detail.rows.length > 1) {
    body.append(el("h3", {}, "합계 (지급 구간 전체)"),
      kvTable(Object.entries(detail.total).map(([k, v]) => [k, won(v) + "원"])));
  }
  $("member-dialog").showModal();
}

$("lookup-run").addEventListener("click", () => {
  const id = $("lookup-id").value.trim();
  if (!id) { alert("사번 또는 성명을 입력하세요."); return; }
  if (!lastRun) { alert("먼저 산출을 실행하세요."); return; }
  showMemberDetail({ employee_id: id }, "방금 산출한 명부·기초율");
});

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
    status(`산출 '${name}' 을(를) 단체 '${CLIENT}' 에 저장했습니다. `
           + "[산출 내역] 탭에서 볼 수 있습니다.");
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
    $("prior-check").disabled = true;
    $("prior-check-result").replaceChildren();
    $("prior-check-status").textContent = "";
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
    $("prior-check").disabled = false;
    $("prior-check-result").replaceChildren();
    $("prior-check-status").textContent = "";
  } catch (error) {
    priorLink = null;
    alert(error.message);
  }
});

// ── 전기 명부와 맞대어 보기 ──────────────────────────────────────
// 당기 명부만 보면 멀쩡한데 전기와 나란히 놓아야 드러나는 것이 있다. 결산이
// 끝난 뒤에 발견하면 다시 산출해야 하므로 **산출 전에** 본다.

$("prior-check").addEventListener("click", async () => {
  const name = $("prior-run").value;
  if (!name) return;

  const status = $("prior-check-status");
  const target = $("prior-check-result");
  target.replaceChildren();
  status.textContent = "맞대어 보는 중…";
  status.className = "hint";

  try {
    const roster = await rosterIntoFS();
    const found = py("prior_check", { name, roster });
    status.textContent = found.summary;
    status.className = found.serious.length ? "hint bad-text" : "hint ok-text";
    target.replaceChildren(...priorCheckTables(found));
  } catch (error) {
    status.textContent = "";
    status.className = "hint";
    alert(error.message);
  }
});

function priorCheckTables(found) {
  const made = [];
  // 저장본이 지금과 다른 세대면 아래 차이를 곧이곧대로 읽으면 안 된다.
  // 자료가 달라진 것인지 서식이 바뀐 것인지 가릴 수 없기 때문이다.
  if (found.schema_gap) {
    made.push(el("div", { class: "notice bad-text" },
      el("b", {}, "이 저장본은 지금과 다른 판입니다. "), found.schema_gap));
  }
  made.push(el("div", { class: "hint" },
    `전기 재직 ${found.prior_active.toLocaleString()}명 · `
    + `당기 재직 ${found.current_active.toLocaleString()}명 · `
    + `사번이 겹치는 사람 ${found.matched.toLocaleString()}명`));

  // 같은 성격이 수십 건씩 나온다. 무엇이 몇 건인지 먼저 보이고, 낱낱은 접어 둔다.
  const counts = Object.entries(found.counts);
  if (counts.length) {
    made.push(el("div", { class: "chips" }, ...counts.map(([code, n]) =>
      el("span", { class: "chip" }, `${code} ${n}건`))));
  }

  const section = (title, rows, bad) => {
    if (!rows.length) return null;
    const table = el("table", { class: "data" },
      el("tr", {}, el("th", {}, "구분"), el("th", {}, "사번"), el("th", {}, "내용")),
      ...rows.map((row) => el("tr", {},
        el("td", { class: bad ? "bad-text" : "" }, row[0]),
        el("td", {}, row[1] || "—"),
        el("td", { style: "text-align:left" }, row[2]))));
    return el("details", { class: "section", ...(bad ? { open: "" } : {}) },
      el("summary", {}, title, el("span", { class: "count" }, `${rows.length}건`)),
      el("div", { class: "section-body" }, el("div", { class: "scroll-x" }, table)));
  };

  const serious = section("확인이 필요합니다 — 산출값이 달라집니다", found.serious, true);
  const notes = section("살펴볼 것 — 정상일 수도 있습니다", found.notes, false);
  if (serious) made.push(serious);
  if (notes) made.push(notes);
  if (!found.serious.length && !found.notes.length) {
    made.push(el("div", { class: "hint ok-text" },
      "전기와 달라진 것이 없습니다. 그대로 산출하셔도 됩니다."));
  }
  return made;
}

// ── 단체 ─────────────────────────────────────────────────────────
// 가장 먼저 고르는 것. 산출 내역·전기 산출 목록이 모두 이 단체 안으로 좁혀진다.
// 전기 확정급여채무를 다른 단체 것으로 끌어오면 증감분석이 통째로 틀리는데,
// 목록에 뜬 이름만 보고는 알아채기 어렵다.

let CLIENT = "";

function renderClients(client, list) {
  CLIENT = client;
  const pick = $("client-pick");
  pick.replaceChildren(...list.map((c) => el(
    "option", { value: c.name },
    c.runs ? `${c.name} (${c.runs}건)` : c.name)));
  pick.value = client;
  const here = list.find((c) => c.name === client);
  $("client-runs").textContent = `${here ? here.runs : 0}건`;
  $("client-remove").disabled = list.length <= 1;
  $("runs-client").textContent = client;
  $("save-client").textContent = client;
}

$("client-pick").addEventListener("change", async () => {
  applyRuns(py("client_select", { name: $("client-pick").value }));
  await persistHome();
  // 단체를 바꾸면 전기 산출은 앞 단체 것이므로 반드시 놓는다.
  $("prior-run").value = "";
  $("prior-run").dispatchEvent(new Event("change"));
});

$("client-add").addEventListener("click", async () => {
  const name = prompt("새 단체 이름 (예: 1번단체, 가나다㈜)", "");
  if (!name || !name.trim()) return;
  applyRuns(py("client_add", { name: name.trim() }));
  await persistHome();
});

$("client-rename").addEventListener("click", async () => {
  const name = prompt("단체 이름 바꾸기", CLIENT);
  if (!name || !name.trim() || name.trim() === CLIENT) return;
  applyRuns(py("client_rename", { name: CLIENT, new_name: name.trim() }));
  await persistHome();
});

$("client-remove").addEventListener("click", async () => {
  const here = $("client-pick").value;
  let answer = py("client_remove", { name: here }, { quiet: true });
  if (!answer.ok) {
    // 안에 산출이 남아 있으면 파이썬이 건수를 세어 막는다. 그 문장 그대로 묻는다.
    if (!confirm(`${answer.error}\n\n정말 '${here}' 를 산출까지 함께 지울까요?`)) return;
    answer = py("client_remove", { name: here, force: true });
  }
  applyRuns(answer);
  await persistHome();
});

function applyRuns(answer) {
  renderClients(answer.client, answer.clients || []);
  renderRuns(answer.runs || []);
}

function refreshRuns() {
  applyRuns(py("run_list"));
}

const BACKUP_AT = "backup-at";

/** 보관함을 내보낸 날을 적어 둔다. 안 내보냈다는 사실을 알려면 기록이 있어야 한다. */
function markBackedUp() {
  try {
    localStorage.setItem(BACKUP_AT, new Date().toISOString());
  } catch (err) {
    /* 저장을 못 하는 브라우저에서도 내보내기 자체는 끝났다 */
  }
}

/**
 * 산출 내역이 이 기기에만 있다는 것을 알린다.
 *
 * 저장한 산출은 브라우저 안에만 남는다. 기기를 바꾸거나 브라우저 자료를
 * 지우면 결산 근거가 통째로 사라지는데, 보관함 내보내기는 **손으로 눌러야**
 * 하는 일이라 안 하면 그만이다. 며칠째 안 했는지 눈에 보이게 한다.
 */
function backupNotice(count) {
  if (!count) return null;
  let saved = "";
  try {
    saved = localStorage.getItem(BACKUP_AT) || "";
  } catch (err) {
    return null;             // 저장을 못 읽으면 겁줄 근거도 없다
  }
  if (!saved) {
    return el("div", { class: "notice bad-text" },
      el("b", {}, "아직 한 번도 보관함으로 내보내지 않았습니다. "),
      `산출 ${count}건이 이 기기 안에만 있습니다 — 기기를 바꾸거나 브라우저 `
      + "자료를 지우면 사라집니다. [자료실] → 보관함에서 내보내 두세요.");
  }
  const days = Math.floor((Date.now() - Date.parse(saved)) / 86400000);
  if (!(days >= 14)) return null;
  return el("div", { class: "notice" },
    `보관함으로 내보낸 지 ${days}일 지났습니다. 그 뒤에 저장한 산출은 이 기기 `
    + "안에만 있습니다.");
}

function renderRuns(runs) {
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
      `'${CLIENT}' 에 아직 저장된 산출이 없습니다. 산출을 마친 뒤 결과 아래 ` +
      "[이 산출을 기기에 저장] 에서 이름을 붙여 저장하세요."));
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
  const notice = backupNotice(runs.length);
  target.replaceChildren(
    ...(notice ? [notice] : []),
    el("div", { class: "scroll-x" },
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
    if ("split_remeasurement" in options) {
      $("split_remeasurement").checked = Boolean(options.split_remeasurement);
    }
    $("base_date").value = options.base_date || "";
    $("period_start").value = options.period_start || "";
    $("prior_dbo").value = options.prior_dbo || "";
    $("prior_rate").value = options.prior_rate || "";
    // 저장한 칸은 하나도 빠짐없이 되돌린다. 빠진 칸은 빈 값으로 남아 조용히
    // 다르게 산출된다 — 자산인식상한이 그랬다. 상한을 걸어 둔 회차를 불러와
    // 다시 돌리면 문단 64 가 통째로 빠진 채 순확정급여자산이 나왔다.
    for (const key of ["past_service_cost", "settlement_obligation",
                       "prior_longterm_dbo", "asset_opening",
                       "asset_contributions", "asset_paid", "asset_closing",
                       "unpaid_benefits", "expected_contributions",
                       "asset_ceiling"]) {
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
  $("roster-hint").textContent = "기본정보 · 재직자명부 · 퇴직자명부 시트가 들어 있는 통합문서";
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
    $("run-lookup-go").onclick = () => {
      const id = $("run-lookup-id").value.trim();
      if (!id) { alert("사번을 입력하세요."); return; }
      showMemberDetail({ name, employee_id: id }, `저장된 산출 '${name}'`);
    };
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
    markBackedUp();
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
  // 시험 명부에도 [일반사항] 이 들어 있다. 올린 파일과 똑같이 채워 준다.
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

// ── 특이사항 한 가지씩 ──────────────────────────────────────────
// 사람은 한 벌, 명부마다 특이사항 하나. 기준 명부와의 차이가 곧 그 특이사항이
// 만든 차이다 — 위의 세 벌로는 답할 수 없는 "왜 이 숫자인가" 에 답한다.

let features = null;

$("feat-run").addEventListener("click", () => {
  const measure = $("feat-measure").checked;
  try {
    status(measure
      ? "특이사항 명부를 만들고 채무를 재는 중… (명부가 열한 벌이라 오래 걸립니다)"
      : "특이사항 명부를 만드는 중…");
    const seed = parseInt($("gen-seed").value, 10) || 20251231;
    features = py("gen_features", {
      work: "/work", seed, base_date: $("gen-base-date").value, measure,
    });
    renderFeatures();
    $("feat-download").disabled = false;
    $("feat-report").disabled = false;
    status(`특이사항 명부 ${features.cases.length}벌을 만들었습니다.` +
           (measure ? " 안내문에 확정급여채무 표가 들어 있습니다." : ""));
  } catch (error) {
    status("만들지 못했습니다: " + (error.message || error));
    alert(error.message || error);
  }
});

$("feat-download").addEventListener("click", () => {
  if (features) download(features.path, features.filename, "application/zip");
});

$("feat-report").addEventListener("click", () => {
  if (features) $("feat-dialog").showModal();
});

// 잰 값은 **표로** 그린다. 자릿수를 맞춘 고정폭 글자표를 그대로 띄우면 좁은
// 화면에서 한 줄이 세 줄로 끊겨, 어느 숫자가 어느 명부 것인지 알 수 없다.
function featureTable() {
  const rows = features.rows || [];
  if (!rows.length) {
    return el("p", { class: "hint" },
      "[확정급여채무까지 재기] 를 켜고 다시 만들면 여기에 채무 표가 나옵니다.");
  }
  const head = el("tr", {},
    ...["명부", "범위", "확정급여채무", "기준 대비", "판정"]
      .map((h) => el("th", {}, h)));
  const body = rows.map((r) => {
    const diff = r.change
      ? `${r.change > 0 ? "+" : "−"}${won(Math.abs(r.change))}` +
        `\n(${(r.ratio * 100).toFixed(2)}%)`
      : (r.name === rows[0].name ? "" : "±0");
    const mark = r.verdict === "어긋남" ? "✕ 어긋남"
      : r.verdict === "맞음" ? "✓ 맞음" : "—";
    return el("tr", { class: r.verdict === "어긋남" ? "bad" : "" },
      el("td", {}, r.name),
      el("td", {}, r.scope),
      el("td", { class: "num" }, won(r.dbo)),
      el("td", { class: "num", style: "white-space:pre-line" }, diff),
      el("td", {}, `${r.expect || "—"}\n${mark}`));
  });
  const table = el("table", { class: "data" }, head, ...body);
  table.querySelectorAll("td:last-child").forEach((cell) => {
    cell.style.whiteSpace = "pre-line";
  });
  return el("div", { class: "scroll-x" }, table);
}

function renderFeatures() {
  // 창 안 — 표 하나와 명부별 설명 카드.
  const parts = [
    el("p", { class: "hint" },
      `산출기준일 ${features.base_date} · 난수 씨앗 ${features.seed}. ` +
      "명부 하나에 특이사항 하나만 담았고, 사람과 기초율은 모두 같습니다. " +
      "그래서 기준 명부와의 차이가 곧 그 특이사항이 만든 차이입니다."),
    featureTable(),
    el("p", { class: "hint" },
      "확정급여채무는 이 프로그램이 낸 값입니다 — 손으로 검산한 '정답' 이 아니라 " +
      "기준값입니다. 실제로 검산이 되는 것은 [판정] 입니다. 사람도 가정도 같고 한 " +
      "칸만 달라졌으므로 어느 쪽으로 움직여야 하는지는 계산 없이도 알 수 있습니다. " +
      "✕ 가 하나라도 있으면 그 명부부터 보십시오."),
  ];
  for (const item of features.cases) {
    const card = el("div", { class: "feat-card" },
      el("b", {}, `${item.title} — ${item.heading}`),
      el("div", { class: "hint" }, item.detail));
    if (item.expect) {
      card.append(el("div", { class: "hint" },
        `적용 범위 ${item.scope} · 기대 ${item.expect} — ${item.why}`));
    }
    parts.push(card);
  }
  $("feat-body").replaceChildren(...parts);

  // 화면 아래 — 바로 산출로 넘길 수 있게.
  $("feat-cases").replaceChildren(...features.cases.map((item) => {
    const box = el("fieldset", {},
      el("legend", {}, `${item.title}  ·  ${item.scope}`),
      el("div", { class: "hint" }, `${item.heading} — ${item.detail}`),
      el("div", { class: "toolbar" },
        el("button", { class: "small primary", type: "button",
          onclick: () => useGenerated(item) }, "이 명부로 산출 준비")));
    if (item.force) {
      box.append(el("div", { class: "warn-box" },
        "자료 오류 명부입니다 — [검증 오류가 있어도 산출 강행] 을 켜야 끝까지 돕니다."));
    }
    return box;
  }));
}

// 접힌 구획의 제목 옆 요약은 값이 바뀔 때마다 다시 맞춘다.
$("page-calc").addEventListener("input", refreshCalcBadges);
$("page-calc").addEventListener("change", refreshCalcBadges);
refreshCalcBadges();

// ── 사용설명서 ────────────────────────────────────────────────
// 빌드가 docs/사용설명서.md 를 help.html 로 바꿔 넣어 둔다. 처음 누를 때만
// 받아 두고 그 뒤로는 그대로 다시 보여 준다 — 산출 중에 여는 것이라 기다리게
// 하면 안 된다.
let helpLoaded = false;

async function openHelp() {
  const box = document.getElementById("help");
  box.hidden = false;
  // 찾기 칸을 자동으로 잡지 않는다. 손가락 기기에서 입력칸에 포커스가 가면
  // 화면이 확대되는데, 설명서를 **읽으려고** 연 사람에게는 그것이 방해다.
  if (helpLoaded) return;
  const body = document.getElementById("help-body");
  try {
    const answer = await fetch("help.html", { cache: "no-cache" });
    if (!answer.ok) throw new Error(String(answer.status));
    body.innerHTML = await answer.text();
    helpLoaded = true;
  } catch (err) {
    body.innerHTML = "<p>설명서를 불러오지 못했습니다. " +
      "빌드한 앱에서만 볼 수 있습니다.</p>";
  }
}

function closeHelp() {
  document.getElementById("help").hidden = true;
}

// 찾기 — 문서가 길어서 눈으로 훑기 어렵다. 맞는 곳을 표시하고 첫 곳으로 옮긴다.
function findInHelp(needle) {
  const body = document.getElementById("help-body");
  body.querySelectorAll("mark").forEach((mark) => {
    mark.replaceWith(document.createTextNode(mark.textContent));
  });
  body.normalize();
  const word = needle.trim();
  if (word.length < 2) return;
  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
  const hits = [];
  let node;
  while ((node = walker.nextNode())) {
    if (node.nodeValue.toLowerCase().includes(word.toLowerCase())) hits.push(node);
  }
  let first = null;
  for (const text of hits) {
    const parts = text.nodeValue.split(new RegExp(`(${word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi"));
    const holder = document.createDocumentFragment();
    for (const part of parts) {
      if (part.toLowerCase() === word.toLowerCase()) {
        const mark = document.createElement("mark");
        mark.textContent = part;
        holder.appendChild(mark);
        if (!first) first = mark;
      } else if (part) {
        holder.appendChild(document.createTextNode(part));
      }
    }
    text.replaceWith(holder);
  }
  if (first) first.scrollIntoView({ block: "center" });
}

// ── 첫 인사 ──────────────────────────────────────────────────
// 자료실에 양식·시험명부·금리표가 들어 있다는 것은 눌러 보기 전에는 모른다.
// 엔진을 기다리지 않고 바로 띄운다 — 뜨는 데 20초가 걸리면 그 사이에 사람은
// 이미 다른 데를 보고 있다.
const INTRO_SEEN = "intro-seen";

function showIntro() {
  const box = document.getElementById("intro-dialog");
  if (!box || box.open) return;
  document.getElementById("intro-hide").checked = false;
  box.showModal();
}

function closeIntro() {
  const box = document.getElementById("intro-dialog");
  if (document.getElementById("intro-hide").checked) {
    try { localStorage.setItem(INTRO_SEEN, "1"); } catch (err) { /* 사설 모드 */ }
  }
  box.close();
}

document.getElementById("intro-close").addEventListener("click", closeIntro);
document.getElementById("intro-go").addEventListener("click", () => {
  closeIntro();
  document.getElementById("tab-lib").click();
});

try {
  if (localStorage.getItem(INTRO_SEEN) !== "1") showIntro();
} catch (err) {
  showIntro();      // 저장을 못 하는 브라우저에서도 안내는 보여야 한다
}

document.getElementById("help-open").addEventListener("click", openHelp);
document.getElementById("help-close").addEventListener("click", closeHelp);
document.getElementById("help").addEventListener("click", (event) => {
  if (event.target.id === "help") closeHelp();   // 바깥을 눌러도 닫힌다
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !document.getElementById("help").hidden) closeHelp();
});
document.getElementById("help-find").addEventListener("input", (event) => {
  findInHelp(event.target.value);
});


// ── 양식 내려받기 ────────────────────────────────────────────────
// 양식 파일을 어딘가에 만들어 두고 링크만 걸면, 프로그램이 바뀔 때 그 파일이
// 옛것으로 남는다. 누를 때마다 지금 코드로 만들어 준다.
function fillTemplates() {
  const target = $("template-list");
  if (!target) return;
  const rows = py("templates").templates.map((item) => {
    const button = el("button", { class: "small primary", type: "button" }, "내려받기");
    button.addEventListener("click", () => {
      button.disabled = true;
      try {
        const made = py("template_make", { key: item.key });
        download(made.path, made.file);
      } catch (err) {
        alert(err.message);
      } finally {
        button.disabled = false;
      }
    });
    return el("div", { class: "lib-line" },
      el("div", { style: "flex:1;min-width:180px" },
        el("b", {}, item.file),
        el("div", { class: "hint" }, item.note)),
      button);
  });
  target.replaceChildren(...rows);
}
