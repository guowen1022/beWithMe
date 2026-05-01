export async function GET(request: Request) {
  const userIdHeader = request.headers.get("X-User-Id") || "";
  const backendBase = (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace("localhost", "127.0.0.1");
  const backendRes = await fetch(`${backendBase}/api/dynamic/canvas`, {
    method: "GET",
    headers: { "X-User-Id": userIdHeader },
  });
  const text = await backendRes.text();
  return new Response(text, {
    status: backendRes.status,
    headers: { "Content-Type": backendRes.headers.get("Content-Type") || "application/json" },
  });
}
