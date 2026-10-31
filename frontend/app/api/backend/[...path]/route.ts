import { NextRequest, NextResponse } from "next/server";
import { sameOrigin } from "@/lib/request-security";
async function proxy(
  req: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  if (
    !["GET", "HEAD"].includes(req.method) &&
    !sameOrigin(req)
  )
    return NextResponse.json({ detail: "Invalid origin" }, { status: 403 });
  const { path } = await context.params;
  if (path.some((p) => !/^[a-zA-Z0-9_.-]+$/.test(p) || p === "." || p === ".."))
    return NextResponse.json({ detail: "Invalid path" }, { status: 400 });
  const headers = new Headers();
  if (process.env.API_TOKEN) headers.set("Authorization", `Bearer ${process.env.API_TOKEN}`);
  if (req.headers.get("content-type"))
    headers.set("content-type", req.headers.get("content-type")!);
  try {
    const length = Number(req.headers.get("content-length") || 0);
    if (length > 11 * 1024 * 1024)
      return NextResponse.json(
        { detail: "Request too large" },
        { status: 413 },
      );
    const body = ["GET", "HEAD"].includes(req.method)
      ? undefined
      : await req.arrayBuffer();
    if (body && body.byteLength > 11 * 1024 * 1024)
      return NextResponse.json(
        { detail: "Request too large" },
        { status: 413 },
      );
    const upstream = await fetch(
      `${process.env.API_INTERNAL_URL || "http://127.0.0.1:8000"}/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`,
      {
        method: req.method,
        headers,
        body,
        cache: "no-store",
        // Profile saves rescore pending applications in the API transaction.
        // On a populated installation this can legitimately take longer than
        // the old 45-second limit, which made a successful save look like a
        // backend outage to the user.
        signal: AbortSignal.timeout(120000),
        redirect: "error",
      },
    );
    return new NextResponse(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type":
          upstream.headers.get("content-type") || "application/json",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch {
    return NextResponse.json(
      { detail: "Backend unavailable. Check the API and database services." },
      { status: 503 },
    );
  }
}
export { proxy as GET, proxy as POST, proxy as PUT, proxy as DELETE };
