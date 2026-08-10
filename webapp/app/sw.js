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

//: 주소에 빌드 값이 붙지 않는 파일들. 이것만 네트워크를 먼저 본다.
const SHELL = ["index.html", "app.js", "app.css", "manifest.webmanifest"];

function isShell(request) {
  if (request.mode === "navigate") return true;
  const path = new URL(request.url).pathname;
  return SHELL.some((name) => path.endsWith("/" + name) || path === "/" + name);
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(
        names.filter((name) => name !== CACHE).map((name) => caches.delete(name))))
      .then(() => self.clients.claim())
  );
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

  event.respondWith(
    caches.match(event.request, { ignoreSearch: true })
      .then((hit) => hit || fetch(event.request))
  );
});
