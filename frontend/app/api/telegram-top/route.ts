import { NextRequest, NextResponse } from "next/server";
export async function GET(req: NextRequest) {
  try {
    const offset = req.nextUrl.searchParams.get("offset") || "0";
    const favorites = req.nextUrl.searchParams.get("favorites") === "true";
    if (!/^\d{1,8}$/.test(offset)) return NextResponse.json({detail: "Invalid offset"}, {status: 400});
    const upstream = await fetch(`${process.env.API_INTERNAL_URL || "http://127.0.0.1:8000"}/telegram-mini/top-vacancies?offset=${offset}&favorites=${favorites}`, {
      headers: {"X-Telegram-Init-Data": req.headers.get("X-Telegram-Init-Data") || ""}, cache: "no-store", signal: AbortSignal.timeout(15000),
    });
    return new NextResponse(upstream.body, {status: upstream.status, headers: {"Content-Type": "application/json", "Cache-Control": "no-store"}});
  } catch { return NextResponse.json({detail: "Backend unavailable"}, {status: 503}); }
}

export async function PUT(req: NextRequest) {
  try {
    const id = req.nextUrl.searchParams.get("id") || "";
    if (!/^[a-f0-9]{64}$/.test(id)) return NextResponse.json({detail: "Invalid vacancy"}, {status: 400});
    const body = await req.json();
    if (typeof body?.favorite !== "boolean") return NextResponse.json({detail: "Invalid state"}, {status: 400});
    const upstream = await fetch(`${process.env.API_INTERNAL_URL || "http://127.0.0.1:8000"}/telegram-mini/top-vacancies/${id}/favorite`, {
      method: "PUT", headers: {"Content-Type": "application/json", "X-Telegram-Init-Data": req.headers.get("X-Telegram-Init-Data") || ""},
      body: JSON.stringify({favorite: body.favorite}), cache: "no-store", signal: AbortSignal.timeout(15000),
    });
    return new NextResponse(upstream.body, {status: upstream.status, headers: {"Content-Type": "application/json", "Cache-Control": "no-store"}});
  } catch { return NextResponse.json({detail: "Backend unavailable"}, {status: 503}); }
}
