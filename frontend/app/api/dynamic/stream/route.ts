export async function GET(request: Request) {
  const userIdHeader = request.headers.get("X-User-Id") || "";
  const backendBase = (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace("localhost", "127.0.0.1");
  const backendRes = await fetch(`${backendBase}/api/dynamic/stream`, {
    method: "GET",
    headers: { "X-User-Id": userIdHeader },
    signal: request.signal,
  });

  return new Response(backendRes.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}
