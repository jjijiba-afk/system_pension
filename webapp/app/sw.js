/* 오프라인 캐시 · 그리고 **승인한 판만 돈다**.
 *
 * 처음 접속할 때 앱 파일 전부(런타임 포함)를 캐시해 두면, 이후에는 네트워크
 * 없이 돈다. 캐시 이름에 빌드 해시가 들어 있어 앱을 고치면 새 창고가 생긴다 —
 * 반쯤 섞인 버전이 돌아다니는 것이 최악이다.
 *
 * ── 왜 '승인' 이 필요한가 ────────────────────────────────────────
 *
 * 예전에는 화면 파일(index.html · app.js · app.css)을 **네트워크 먼저** 로
 * 줬다. 새 판을 올려도 휴대폰이 옛 화면을 계속 띄우는 일이 있어서 그렇게
 * 했는데, 그 대가로 **새로고침 한 번이면 새 판이 그냥 들어왔다.** 업데이트
 * 창에서 비밀번호를 받아도 소용이 없었다 — 창을 닫았다 다시 열면 그만이다.
 * 산출이 몇 분째 도는 중에 판이 갈리면 그 산출은 사라진다.
 *
 * 그래서 **고정(pin)** 을 둔다. 사람이 비밀번호로 승인한 판의 창고 이름을
 * 따로 적어 두고, 화면 파일은 언제나 그 창고에서만 꺼내 준다.
 *
 * 새 일꾼(서비스워커)이 들어오는 것 자체는 막을 수 없다. 창을 모두 닫으면
 * 브라우저가 알아서 자리를 넘긴다. 하지만 새 일꾼도 고정을 먼저 읽고 **승인된
 * 창고** 를 내주므로, 사람이 비밀번호를 넣기 전까지 화면은 옛 판 그대로다.
 * 승인은 오직 [업데이트] 창이 보내는 ``take-over`` 신호로만 옮겨진다.
 *
 * ``release.json`` 만 예외다. 새 판이 나왔는지를 이 파일 하나로 판별하므로
 * 캐시를 아예 태우지 않고 매번 서버에 묻는다 — 캐시로 주면 옛 판이 자기
 * 자신을 최신이라고 답하게 되어 업데이트 버튼이 영영 안 뜬다.
 */
"use strict";
const CACHE = "pension-__VERSION__";
const PRECACHE = __PRECACHE__;

//: 승인된 창고 이름을 적어 두는 곳. 판이 갈려도 이 창고는 지우지 않는다.
const PIN = "pension-pin";
const PIN_KEY = "approved-build";

//: 런타임·휠. 설치 때 한꺼번에 받지 않고, 화면이 다 뜬 뒤에 **하나씩** 채운다.
//: 이것이 캐시에 없으면 인터넷이 끊겼을 때 화면만 뜨고 엔진이 없다.
const HEAVY = __HEAVY__;

/** 사람이 승인한 창고 이름. 없거나 이미 지워졌으면 ``null``. */
async function approvedCache() {
  try {
    const pin = await caches.open(PIN);
    const hit = await pin.match(PIN_KEY);
    if (!hit) return null;
    const name = (await hit.text()).trim();
    return name && (await caches.has(name)) ? name : null;
  } catch (error) {
    return null;                      // 저장소를 못 읽으면 이 판으로 돈다
  }
}

/** 이제부터 이 창고를 쓴다고 적는다. 비밀번호를 통과한 순간에만 불린다. */
async function approve(name) {
  const pin = await caches.open(PIN);
  await pin.put(PIN_KEY, new Response(name));
}

/** 지금 화면이 쓰는 창고. 승인된 것이 있으면 그것, 없으면 이 판의 것. */
async function activeCache() {
  return (await approvedCache()) || CACHE;
}

/** 무거운 파일을 하나씩 캐시에 채운다. 실패해도 다음 것을 계속 받는다. */
async function warmUp() {
  // 지금 도는 화면의 창고에 채운다. 승인 전인 새 판의 창고에 넣으면, 정작
  // 인터넷이 끊겼을 때 화면은 옛 판인데 엔진이 없는 꼴이 된다.
  const cache = await caches.open(await activeCache());
  for (const url of HEAVY) {
    try {
      if (await cache.match(url)) continue;      // 이미 있으면 건너뛴다
      const response = await fetch(url, { credentials: "same-origin" });
      if (response.ok) await cache.put(url, response.clone());
    } catch (error) {
      /* 회선이 끊기면 다음 방문에 다시 채운다 */
    }
  }
}

//: 주소에 빌드 값이 붙지 않는 화면 파일. 승인된 창고에서만 꺼내 준다.
const SHELL = ["index.html", "app.js", "app.css", "manifest.webmanifest"];

//: 캐시를 태우지 않는 파일. 새 판이 나왔는지 묻는 창구다.
const LIVE = ["release.json"];

function named(request, names) {
  const path = new URL(request.url).pathname;
  return names.some((name) => path.endsWith("/" + name) || path === "/" + name);
}

function isShell(request) {
  return request.mode === "navigate" || named(request, SHELL);
}

// 설치 때는 **화면 파일만** 받는다. 예전에는 런타임과 휠까지 한꺼번에 받았는데
// (14MB), 그중 하나라도 실패하면 `addAll` 이 통째로 실패해 새 일꾼이 아예 설치
// 되지 않는다 — 그러면 옛 일꾼이 그대로 남아 **새 판이 영영 안 뜬다.** 휴대폰
// 회선에서는 이것이 드물지 않다. 무거운 것은 처음 쓸 때 받아 두면 된다.
//
// 받아만 두고 **쓰지는 않는다.** 승인이 옮겨 오기 전까지 이 창고는 창고일 뿐,
// 화면은 승인된 판에서 나간다.
self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    // `cache: "reload"` 이 없으면 **브라우저 자신의 HTTP 캐시** 가 옛 파일을
    // 그대로 내준다 — 정적 호스팅은 보통 Cache-Control 을 안 붙이므로,
    // 브라우저가 Last-Modified 만 보고 몇 분치 신선하다고 어림잡는다. 그러면
    // 새 창고에 옛 화면이 담기고, 승인해도 판이 안 바뀐 것처럼 보인다.
    // 실제로 그렇게 됐다.
    await cache.addAll(PRECACHE.map((url) => new Request(url, { cache: "reload" })));
    // 맨 처음 설치라면 승인할 사람도 없고 옛 판도 없다 — 이 판이 곧 승인된
    // 판이다. 이미 승인된 판이 있으면 건드리지 않는다.
    if (!(await approvedCache())) await approve(CACHE);
  })());
});

// 옛 창고는 새 일꾼이 실제로 넘겨받은 뒤에 지운다. **승인된 창고와 고정 자체는
// 절대 지우지 않는다** — 그것을 지우면 승인 전인 새 판이 화면으로 새어 나온다.
//
// `clients.claim()` 은 **처음 설치될 때** 필요하다. 이것이 없으면 첫 방문은
// 일꾼의 통제를 받지 않아, 그 방문에서 받은 런타임·휠(14MB)이 캐시에 하나도
// 담기지 않는다 — 곧바로 인터넷을 끊으면 화면만 뜨고 엔진이 없다. 처음 연
// 그 자리에서 오프라인 준비가 끝나야 한다.
self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keep = new Set([CACHE, PIN, await approvedCache()].filter(Boolean));
    const names = await caches.keys();
    await Promise.all(
      names.filter((name) => name.startsWith("pension-") && !keep.has(name))
           .map((name) => caches.delete(name)));
    await self.clients.claim();
  })());
});

// 화면이 다 뜨면 앱이 알려 준다 — 그때 런타임을 캐시에 채운다. 부팅 직후라
// 대개 브라우저 캐시에 남아 있어 회선을 다시 쓰지 않고 채워진다.
self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "warm") {
    event.waitUntil(warmUp());
  }
  // 사람이 [업데이트] 창에서 관리자 비밀번호를 넣고 확인을 눌렀을 때에만
  // 승인이 이 판으로 옮겨 온다. **여기가 유일한 통로다** — 새로고침도,
  // 앱을 껐다 켜는 것도, 브라우저가 알아서 일꾼을 바꾸는 것도 판을 못 옮긴다.
  if (event.data && event.data.type === "take-over") {
    event.waitUntil((async () => {
      await approve(CACHE);
      await self.skipWaiting();
    })());
  }
});

/** 한 요청에 무엇을 내줄지. */
async function serve(request) {
  // 새 판이 나왔는지 묻는 창구. 캐시를 태우면 옛 판이 자기를 최신이라 답한다.
  if (named(request, LIVE)) {
    try {
      return await fetch(request.url,
                         { cache: "no-store", credentials: "same-origin" });
    } catch (error) {
      // 인터넷이 없으면 확인할 방법이 없다. 빈 답을 주면 화면이 조용히 넘어간다.
      return new Response("{}",
                          { headers: { "content-type": "application/json" } });
    }
  }

  const name = await activeCache();
  const cache = await caches.open(name);

  // 주소창으로 들어온 경우(`/` · `/index.html` 둘 다). 승인된 화면만 내준다 —
  // 여기서 네트워크를 먼저 보면 새로고침 한 번에 새 판이 들어와 버린다.
  if (request.mode === "navigate") {
    const home = await cache.match("index.html");
    if (home) return home;
  }

  const hit = await cache.match(request, { ignoreSearch: isShell(request) });
  if (hit) return hit;

  // 창고에 없다. 받아서 그 창고에 채운다 — 휠·아이콘이 여기로 온다.
  //
  // 여기서는 ?v= 붙은 주소를 **그대로** 대조했다(위 `ignoreSearch`) — 휠 주소의
  // v 가 내용 해시라, 이걸 무시하면 옛 휠이 새 화면에 물리는 반쪽 업데이트가
  // 난다.
  try {
    const response = await fetch(request.url,
                                 { cache: "no-cache", credentials: "same-origin" });
    if (response.ok && response.type === "basic") {
      cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    return (await caches.match(request, { ignoreSearch: true }))
        || (await cache.match("index.html"))
        || Response.error();
  }
}

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  event.respondWith(serve(event.request));
});
