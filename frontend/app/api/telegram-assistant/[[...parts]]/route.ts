import { NextRequest, NextResponse } from "next/server";
import { sameOrigin } from "@/lib/request-security";

const uuid = "[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}";
const draft = new RegExp("^" + uuid + "$");
const sendFile = new RegExp("^" + uuid + "/document$");

async function forward(req: NextRequest, context: { params: Promise<{parts?: string[]}> }) {
  const path = ((await context.params).parts || []).join("/");
  const media = path === "media/audio" || path === "media/image";
  const allowed = (!path && ["GET", "POST"].includes(req.method))
    || (req.method === "DELETE" && draft.test(path))
    || (req.method === "POST" && (media || sendFile.test(path)));
  if (!allowed) return new Response(null, {status: 404});
  if (req.method !== "GET" && !sameOrigin(req)) return new Response(null, {status: 403});
  const signature = req.headers.get("X-Telegram-Init-Data") || "";
  if (!signature || signature.length > 8192) return new Response(null, {status: 401});
  const headers = new Headers({"X-Telegram-Init-Data": signature});
  const limit = media ? 4 * 1024 * 1024 : 32 * 1024;
  if (Number(req.headers.get("content-length")) > limit) return new Response(null, {status: 413});
  try {
    const chunks: Uint8Array<ArrayBuffer>[] = [];
    let length = 0;
    if (req.body) {
      const reader = req.body.getReader();
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        length += chunk.value.byteLength;
        if (length > limit) { await reader.cancel(); return new Response(null, {status: 413}); }
        chunks.push(new Uint8Array(chunk.value));
      }
      headers.set("Content-Type", media ? "application/octet-stream" : "application/json");
    }
    const api = process.env.API_INTERNAL_URL || "http://127.0.0.1:8000";
    const res = await fetch(api + "/telegram-mini/assistant" + (path ? "/" + path : ""), {
      method: req.method, headers, body: chunks.length ? new Blob(chunks) : undefined,
      signal: AbortSignal.timeout(media ? 130000 : 45000), cache: "no-store", redirect: "error",
    });
    return new NextResponse(res.body, {status: res.status, headers: {
      "Content-Type": "application/json", "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    }});
  } catch {
    return NextResponse.json({detail: "Помощник временно недоступен."}, {status: 503});
  }
}
export {forward as GET, forward as POST, forward as DELETE};
