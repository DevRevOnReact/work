import { NextRequest } from "next/server";

const item = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\/(?:status|file|answers)$/;
async function forward(req: NextRequest, context: {params: Promise<{parts?: string[]}>}) {
  const path = ((await context.params).parts || []).join("/");
  const media = path === "media/image" || path === "media/audio";
  if (!((req.method === "GET" && (!path || item.test(path))) || (req.method === "POST" && (!path || media))))
    return new Response(null, {status:404});
  const credential = req.headers.get("Authorization") || "";
  if (!/^Bearer jp1_[A-Za-z0-9_-]{43}$/.test(credential)) return new Response(null, {status:401});
  if (req.headers.has("origin")) return new Response(null, {status:403});
  const headers = new Headers({Authorization:credential});
  let body: ArrayBuffer | undefined;
  try {
    if (req.method === "POST") {
      const size = req.headers.get("content-length");
      const limit = media ? 4 * 1024 * 1024 : 48 * 1024;
      if (!size || !/^\d+$/.test(size)) return new Response(null, {status:411});
      if (Number(size) > limit) return new Response(null, {status:413});
      body = await req.arrayBuffer();
      if (body.byteLength !== Number(size) || body.byteLength > limit) return new Response(null,{status:400});
      headers.set("Content-Type", media ? "application/octet-stream" : "application/json");
    }
    const base = process.env.API_INTERNAL_URL || "http://127.0.0.1:8000";
    const response = await fetch(base + "/mobile-assistant" + (path ? "/"+path : ""), {
      headers, method:req.method, body, redirect:"error", cache:"no-store", signal:AbortSignal.timeout(media ? 130000 : 35000),
    });
    return new Response(response.body,{status:response.status,headers:{
      "Content-Type":response.headers.get("Content-Type") || "application/octet-stream",
      "Cache-Control":"private, no-store", "X-Content-Type-Options":"nosniff",
    }});
  } catch {return new Response(null,{status:503});}
}
export {forward as GET, forward as POST};
