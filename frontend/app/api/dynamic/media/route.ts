export async function GET(request: Request) {
  const userIdHeader = request.headers.get("X-User-Id") || "";
  const deviceId = request.headers.get("X-Device-Id") || "";
  const deviceClass = request.headers.get("X-Device-Class") || "";
  const deviceCaps = request.headers.get("X-Device-Capabilities") || "";

  const fwd: Record<string, string> = { "X-User-Id": userIdHeader };
  if (deviceId) fwd["X-Device-Id"] = deviceId;
  if (deviceClass) fwd["X-Device-Class"] = deviceClass;
  if (deviceCaps) fwd["X-Device-Capabilities"] = deviceCaps;

  const backendBase = (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace("localhost", "127.0.0.1");
  const backendRes = await fetch(`${backendBase}/api/dynamic/media`, {
    method: "GET",
    headers: fwd,
  });
  const text = await backendRes.text();
  return new Response(text, {
    status: backendRes.status,
    headers: { "Content-Type": backendRes.headers.get("Content-Type") || "application/json" },
  });
}
