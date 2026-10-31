import { createHash, timingSafeEqual } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";

function equal(a: string, b: string) {
  return timingSafeEqual(createHash("sha256").update(a).digest(), createHash("sha256").update(b).digest());
}

export function proxy(request: NextRequest) {
  if (request.nextUrl.pathname === "/health") return NextResponse.next();
  const password = process.env.LOGIN_PASSWORD || "";
  const hosted = process.env.HOSTED_MODE === "true";
  if (!password && !hosted) return NextResponse.next();
  if (password.length < 16)
    return new NextResponse("Access is not configured", {status: 503});
  const authorization = request.headers.get("authorization") || "";
  let credentials = "";
  try {
    if (authorization.startsWith("Basic ")) credentials = Buffer.from(authorization.slice(6), "base64").toString("utf8");
  } catch { /* Invalid credentials are denied below. */ }
  if (!equal(credentials, `${process.env.LOGIN_USER || "jobpilot"}:${password}`))
    return new NextResponse("Authentication required", {
      status: 401, headers: {"WWW-Authenticate": 'Basic realm="JobPilot", charset="UTF-8"', "Cache-Control": "no-store"},
    });
  return NextResponse.next();
}

export const config = {matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"]};
