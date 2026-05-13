import { url } from "./client";

export interface User {
  id: string;
  username: string;
  created_at: string;
}

// createUser is public (no auth header). Doesn't go through `api()`.
export async function createUser(username: string): Promise<User> {
  const res = await fetch(url("/api/users"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({} as { detail?: string }));
    throw new Error(err.detail || `Failed to create user (${res.status})`);
  }
  return res.json();
}
