import type { NextRequest } from "next/server";

export function sameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return false;
  try {
    const parsed = new URL(origin);
    if (parsed.origin !== origin || !["http:", "https:"].includes(parsed.protocol)) return false;
    if (process.env.APP_ORIGIN) return origin === new URL(process.env.APP_ORIGIN).origin;
    // Next standalone's internal URL may use 0.0.0.0. Compare the browser's
    // origin against the actual request Host, never a forwarded arbitrary host.
    if (parsed.host !== request.headers.get("host")) return false;
    return true;
  } catch {
    return false;
  }
}
