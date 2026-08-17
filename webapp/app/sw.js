/* 오프라인 캐시.
 *
 * 처음 접속할 때 앱 파일 전부(런타임 포함)를 캐시해 두면, 이후에는 네트워크
 * 없이 돈다. 캐시 이름에 빌드 해시가 들어 있어 앱을 고치면 이전 캐시는
 * 통째로 버려진다 — 반쯤 섞인 버전이 돌아다니는 것이 최악이다.
 *
 * 다만 **전부를 캐시 우선으로 주면 새 판이 영영 안 뜬다.** 화면 파일
 * (index.html · app.js · app.css)은 주소에 빌드 값이 붙어 있지 않아서, 한 번
 * 캐시에 들어가면 그 뒤로는 네트워크를 보지 않는다. 새로 올려도 휴대폰은
 * 옛 화면을 계속 띄운다 — 실제로 그렇게 됐다.
 *
 * 그래서 두 갈래로 나눈다.
 *
 * 화면 파일 · 첫 진입
 *     네트워크 먼저. 받아 오면 그걸 쓰면서 캐시도 갈아 끼운다. 네트워크가
 *     없으면 캐시로 떨어진다 — 오프라인에서 도는 것은 그대로다.
 * 런타임 · 휠 · 아이콘
 *     캐시 먼저. 수십 MB짜리이고 빌드마다 캐시 이름이 바뀌므로, 새 빌드에서는
 *     어차피 새로 받는다.
 */
"use strict";
const CACHE = "pension-__VERSION__";
const PRECACHE = __PRECACHE__;

//: 런타임·휠. 설치 때 한꺼번에 받지 않고, 화면이 다 뜬 뒤에 **하나씩** 채운다.
//: 이것이 캐시에 없으면 인터넷이 끊겼을 때 화면만 뜨고 엔진이 없다.
const HEAVY = __HEAVY__;

/** 무거운 파일을 하나씩 캐시에 채운다. 실패해도 다음 것을 계속 받는다. */
async function warmUp() {
  const cache = await caches.open(CACHE);
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

//: 주소에 빌드 값이 붙지 않는 파일들. 이것만 네트워크를 먼저 본다.
//:
//: ``release.json`` 이 여기 있어야 한다. 새 판이 나왔는지를 이 파일 하나로
//: 판별하는데, 캐시 우선으로 주면 옛 판이 자기 자신을 최신이라고 말하게 된다.
const SHELL = ["index.html", "app.js", "app.css", "manifest.webmanifest",
               "release.json"];

function isShell(request) {
  if (request.mode === "navigate") return true;
  const path = new URL(request.url).pathname;
  return SHELL.some((name) => path.endsWith("/" + name) || path === "/" + name);
}

// 설치 때는 **화면 파일만** 받는다. 예전에는 런타임과 휠까지 한꺼번에 받았는데
// (14MB), 그중 하나라도 실패하면 `addAll` 이 통째로 실패해 새 일꾼이 아예 설치
// 되지 않는다 — 그러면 옛 일꾼이 그대로 남아 **새 판이 영영 안 뜬다.** 휴대폰
// 회선에서는 이것이 드물지 않다. 무거운 것은 처음 쓸 때 받아 두면 된다.
// 새 일꾼은 **기다린다.** `skipWaiting()` 으로 곧바로 넘겨받으면, 산출이 도는
// 중에 엔진(휠)만 새 판으로 바뀌어 화면과 엇갈릴 수 있다. 사람이 새로고침해
// 열려 있던 화면이 모두 닫힌 뒤에 바뀌는 것이 맞다.
self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)));
});

// 옛 캐시는 새 일꾼이 실제로 넘겨받은 뒤에 지운다.
//
// `clients.claim()` 은 **처음 설치될 때** 필요하다. 이것이 없으면 첫 방문은
// 일꾼의 통제를 받지 않아, 그 방문에서 받은 런타임·휠(14MB)이 캐시에 하나도
// 담기지 않는다 — 곧바로 인터넷을 끊으면 화면만 뜨고 엔진이 없다. 처음 연
// 그 자리에서 오프라인 준비가 끝나야 한다.
//
// 새 판으로 **갈아타는** 것과는 다른 이야기다. `skipWaiting()` 을 쓰지 않으므로
// 이미 떠 있는 화면은 자기가 받은 판 그대로 끝까지 돌고, 새 일꾼은 그 화면들이
// 닫힌 뒤에야 활성화된다. 그때 claim 이 불려도 빼앗을 화면이 없다.
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(
        names.filter((name) => name !== CACHE).map((name) => caches.delete(name))))
      .then(() => self.clients.claim())
  );
});

// 화면이 다 뜨면 앱이 알려 준다 — 그때 런타임을 캐시에 채운다. 부팅 직후라
// 대개 브라우저 캐시에 남아 있어 회선을 다시 쓰지 않고 채워진다.
self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "warm") {
    event.waitUntil(warmUp());
  }
  // 사람이 [업데이트] 창에서 확인을 눌렀을 때에만 자리를 넘겨받는다.
  //
  // `skipWaiting()` 을 설치 때 부르지 않는 이유가 여기 있다 — 산출이 도는
  // 중에 엔진(휠)만 새 판으로 바뀌면 화면과 엇갈린다. 이 신호는 "지금 산출을
  // 버려도 좋다" 는 사람의 대답이라, 그 순간에만 안전하다.
  if (event.data && event.data.type === "take-over") {
    self.skipWaiting();
  }
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;

  if (isShell(event.request)) {
    // `cache: "no-cache"` 로 받는다. 그냥 fetch 하면 **브라우저 자신의 HTTP
    // 캐시** 가 옛 파일을 그대로 내주기 때문이다 — 정적 호스팅은 보통
    // Cache-Control 을 안 붙이므로, 브라우저가 Last-Modified 만 보고 몇 분치
    // 신선하다고 어림잡는다. 서버에 한 번 물어보게 만들어야 한다(바뀐 게
    // 없으면 304 라 값싸다).
    event.respondWith(
      fetch(event.request.url, { cache: "no-cache", credentials: "same-origin" })
        .then((response) => {
          // 받아 온 것을 캐시에 넣어 둔다. 다음에 네트워크가 없어도 뜬다.
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
          return response;
        })
        .catch(() => caches.match(event.request, { ignoreSearch: true })
          .then((hit) => hit || caches.match("index.html")))
    );
    return;
  }

  // 런타임·휠·아이콘. 캐시에 있으면 그대로, 없으면 받아서 넣어 둔다. 설치 때
  // 미리 받지 않으므로 여기서 채워야 다음부터 네트워크 없이 돈다.
  //
  // 여기서는 ?v= 붙은 주소를 **그대로** 대조한다 — 휠 주소의 v 가 내용
  // 해시라, 이걸 무시하면 옛 휠이 새 화면에 물리는 반쪽 업데이트가 난다.
  event.respondWith(
    caches.match(event.request).then((hit) => {
      if (hit) return hit;
      return fetch(event.request).then((response) => {
        if (response.ok && response.type === "basic") {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        }
        return response;
      });
    })
  );
});
