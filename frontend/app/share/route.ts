import { randomBytes } from "node:crypto";
import { NextRequest } from "next/server";

// Web Share Target is a navigation initiated by the OS. It only prefills the
// editor; generation is a separate authenticated, same-origin user action.
export async function POST(req: NextRequest) {
  if (req.headers.get("sec-fetch-site") === "cross-site") return new Response(null, {status:403});
  if (req.headers.get("content-type")?.split(";")[0] !== "application/x-www-form-urlencoded")
    return new Response(null,{status:415});
  const limit = 48 * 1024;
  if (Number(req.headers.get("content-length")) > limit || !req.body) return new Response(null,{status:413});
  const chunks: Uint8Array<ArrayBuffer>[] = [];
  const reader = req.body.getReader();
  let size = 0;
  while (true) {
    const part = await reader.read();
    if (part.done) break;
    size += part.value.length;
    if (size > limit) {await reader.cancel(); return new Response(null,{status:413});}
    chunks.push(new Uint8Array(part.value));
  }
  const fields = new URLSearchParams(await new Blob(chunks).text());
  const text = [...new Set(["title","text","url"].map(k => fields.get(k)?.trim()).filter(Boolean))].join("\n").slice(0,4000);
  const safe = JSON.stringify(text).replace(/</g,"\\u003c");
  const nonce = randomBytes(18).toString("base64");
  return new Response('<!doctype html><html lang="ru"><meta charset="utf-8"><title>JobPilot</title><script nonce="'+nonce+'">try{sessionStorage.setItem("jobpilot-share",'+safe+')}finally{location.replace("/?view=assistant")}</script></html>',{
    headers:{"Content-Type":"text/html;charset=utf-8","Cache-Control":"private, no-store","Referrer-Policy":"no-referrer",
      "Content-Security-Policy":"default-src 'none'; frame-ancestors 'none'; base-uri 'none'; script-src 'nonce-"+nonce+"'"}
  });
}
