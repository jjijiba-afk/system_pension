/* 오프라인 캐시.
 *
 * 처음 접속할 때 앱 파일 전부(런타임 포함)를 캐시해 두면, 이후에는 네트워크
 * 없이 돈다. 캐시 이름에 빌드 해시가 들어 있어 앱을 고치면 이전 캐시는
 * 통째로 버려진다 — 반쯤 섞인 버전이 돌아다니는 것이 최악이다.
 */
"use strict";
const CACHE = "pension-__VERSION__";
const PRECACHE = __PRECACHE__;

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
  event.respondWith(
    caches.match(event.request, { ignoreSearch: true })
      .then((hit) => hit || fetch(event.request))
  );
});
