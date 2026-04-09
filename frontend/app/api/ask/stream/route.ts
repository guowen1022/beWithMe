export async function POST(request: Request) {
  const body = await request.text();

  const backendRes = await fetch("http://localhost:8000/api/ask/stream", {
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
