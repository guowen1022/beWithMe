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

export async function listUsers(): Promise<User[]> {
  const res = await fetch(url("/api/users"), { method: "GET" });
  if (!res.ok) throw new Error(`listUsers failed (${res.status})`);
  return res.json();
}

/**
 * Create OR reuse a user by username. The backend rejects duplicate
 * usernames with 409; on that we fetch the existing user from the list
 * and return it — same behavior as the desktop frontend's UserSelector.
 */
export async function ensureUser(username: string): Promise<User> {
  try {
    return await createUser(username);
  } catch (e) {
    if (!(e instanceof Error) || !/409|already exists/i.test(e.message)) throw e;
    const users = await listUsers();
    const match = users.find((u) => u.username === username);
    if (!match) throw new Error(`user "${username}" exists but not in list`);
    return match;
  }
}
