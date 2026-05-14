// Next.js handler that forwards client event-log POSTs to the backend
// (knowledge sidecar via the shell gateway). Symmetric with the /api/ask/stream
// proxy: rewrite localhost→127.0.0.1 to bypass the system proxy.
export async function POST(request: Request) {
  const body = await request.text();
  const userIdHeader = request.headers.get("X-User-Id") || "";
  const backendBase = (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace("localhost", "127.0.0.1");

  try {
    const backendRes = await fetch(`${backendBase}/api/events`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-User-Id": userIdHeader,
      },
      body,
    });
    return new Response(backendRes.body, { status: backendRes.status });
  } catch {
    // Observability must never break the page. Pretend it worked.
    return new Response(JSON.stringify({ ok: false }), {
      status: 202,
      headers: { "Content-Type": "application/json" },
    });
  }
}
