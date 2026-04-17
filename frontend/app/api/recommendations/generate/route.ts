export async function POST(request: Request) {
  const userIdHeader = request.headers.get("X-User-Id") || "";
  const backendBase = (
    process.env.BACKEND_URL || "http://127.0.0.1:8000"
  ).replace("localhost", "127.0.0.1");

  const backendRes = await fetch(`${backendBase}/api/recommendations/generate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-User-Id": userIdHeader,
    },
  });

  const data = await backendRes.text();
  return new Response(data, {
    status: backendRes.status,
    headers: { "Content-Type": "application/json" },
  });
}
