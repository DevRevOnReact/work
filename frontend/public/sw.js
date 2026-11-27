// Private workspace responses always go to the network, including after sign-out.
self.addEventListener("fetch", event => {
  if (event.request.mode === "navigate" && event.request.method === "GET") {
    event.respondWith(fetch(event.request).catch(() => new Response(
      "<!doctype html><html lang=ru><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>JobPilot</title><main><h1>Нет соединения</h1><p>Подключитесь к сети и обновите страницу. Принятые запросы обрабатываются на сервере.</p></main></html>",
      {headers: {"Content-Type":"text/html;charset=utf-8","Cache-Control":"no-store"}})));
  }
});
