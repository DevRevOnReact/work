export async function GET() {
  try {
    const response = await fetch(`${process.env.API_INTERNAL_URL || "http://127.0.0.1:8000"}/health/ready`,
      {cache: "no-store", signal: AbortSignal.timeout(10000)});
    return Response.json({status: response.ok ? "ready" : "unavailable", commit: process.env.RENDER_GIT_COMMIT || "local"}, {status: response.ok ? 200 : 503});
  } catch {
    return Response.json({status: "unavailable"}, {status: 503});
  }
}
