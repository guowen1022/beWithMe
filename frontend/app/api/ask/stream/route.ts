export async function POST(request: Request) {
  const body = await request.text();

  // Use 127.0.0.1 instead of localhost to bypass system proxy
  const backendRes = await fetch("http://127.0.0.1:8000/api/ask/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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
