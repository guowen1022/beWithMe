export async function POST(
  request: Request,
  { params }: { params: Promise<{ block_id: string }> },
) {
  const { block_id } = await params;
  const body = await request.text();
  const userIdHeader = request.headers.get("X-User-Id") || "";
  const backendBase = (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace("localhost", "127.0.0.1");
  const backendRes = await fetch(
    `${backendBase}/api/dynamic/error/${encodeURIComponent(block_id)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": userIdHeader },
      body,
    },
  );
  const text = await backendRes.text();
  return new Response(text, {
    status: backendRes.status,
    headers: { "Content-Type": backendRes.headers.get("Content-Type") || "application/json" },
  });
}
