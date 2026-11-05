import { NextRequest, NextResponse } from "next/server";
export async function GET(req: NextRequest) {
  try {
    const offset = req.nextUrl.searchParams.get("offset") || "0";
    if (!/^\d{1,8}$/.test(offset)) return NextResponse.json({detail: "Invalid offset"}, {status: 400});
    const upstream = await fetch(`${process.env.API_INTERNAL_URL || "http://127.0.0.1:8000"}/telegram-mini/top-vacancies?offset=${offset}`, {
      headers: {"X-Telegram-Init-Data": req.headers.get("X-Telegram-Init-Data") || ""}, cache: "no-store", signal: AbortSignal.timeout(15000),
    });
    return new NextResponse(upstream.body, {status: upstream.status, headers: {"Content-Type": "application/json", "Cache-Control": "no-store"}});
  } catch { return NextResponse.json({detail: "Backend unavailable"}, {status: 503}); }
}
