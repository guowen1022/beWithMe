export async function POST(request: Request) {
  const body = await request.text();
  const userIdHeader = request.headers.get("X-User-Id") || "";

  // Use 127.0.0.1 instead of localhost to bypass system proxy.
  // BACKEND_URL lets a worktree dev server target an alternate port without
  // colliding with another backend on 8000.
  const backendBase = (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace("localhost", "127.0.0.1");
  const backendRes = await fetch(`${backendBase}/api/ask/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-User-Id": userIdHeader,
    },
    body,
  });

  // Stream the SSE response through without buffering
  return new Response(backendRes.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}
