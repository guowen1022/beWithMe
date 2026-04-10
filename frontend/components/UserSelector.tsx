"use client";

import { useEffect, useState } from "react";
import { listUsers, createUser, setCurrentUserId, type User } from "@/lib/api";

interface UserSelectorProps {
  onUserSelected: (userId: string) => void;
}

export default function UserSelector({ onUserSelected }: UserSelectorProps) {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [newUsername, setNewUsername] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listUsers()
      .then((u) => {
        setUsers(u);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  function selectUser(userId: string) {
    setCurrentUserId(userId);
    onUserSelected(userId);
  }

  async function handleCreate() {
    if (!newUsername.trim()) return;
    setError(null);
    setCreating(true);
    try {
      const user = await createUser(newUsername.trim());
      setUsers((prev) => [...prev, user]);
      selectUser(user.id);
    } catch (err: any) {
      setError(err.message || "Failed to create user");
    } finally {
      setCreating(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <p className="text-gray-400">Loading users...</p>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-center h-screen bg-gray-50">
      <div className="w-full max-w-sm p-6 bg-white rounded-xl shadow-lg">
        <h2 className="text-xl font-semibold text-gray-800 mb-4">
          Who are you?
        </h2>

        {users.length > 0 && (
          <div className="mb-4">
            <p className="text-sm text-gray-500 mb-2">Select your profile:</p>
            <div className="space-y-2">
              {users.map((u) => (
                <button
                  key={u.id}
                  onClick={() => selectUser(u.id)}
                  className="w-full text-left px-4 py-2 rounded-lg border border-gray-200 hover:border-blue-400 hover:bg-blue-50 transition-colors"
                >
                  <span className="font-medium text-gray-700">{u.username}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="border-t pt-4">
          <p className="text-sm text-gray-500 mb-2">
            {users.length > 0 ? "Or create a new profile:" : "Create your profile:"}
          </p>
          <div className="flex gap-2">
            <input
              type="text"
              value={newUsername}
              onChange={(e) => setNewUsername(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleCreate()}
              placeholder="Enter your name"
              className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              disabled={creating}
            />
            <button
              onClick={handleCreate}
              disabled={creating || !newUsername.trim()}
              className="px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              {creating ? "..." : "Go"}
            </button>
          </div>
          {error && <p className="text-red-500 text-xs mt-1">{error}</p>}
        </div>
      </div>
    </div>
  );
}
