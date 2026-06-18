// Long-lived SSE connection that carries canvas events: block mounts, sticky
// block-data messages, errors. Mirrors frontend/lib/api.ts:DynamicEvent and
// :subscribeToDynamicStream.

import { streamSse } from "../sse/sseStream";

export type DynamicEvent =
  | { type: "open"; device_id?: string }
  | {
      type: "ui-update";
      action: "mount" | "replace" | "unmount";
      block: { id: string; source: string; design_doc?: string | null };
    }
  | { type: "block-data"; block_id: string; topic: string; value: unknown }
  | { type: "block-error"; block_id: string; error: string }
  | { type: "block-action"; block_id: string; action: string; options?: Record<string, unknown> }
  | { type: "voice-play"; text: string; voice?: string | null; speed?: number | null; lang?: string | null }
  | { type: "teacher-thinking"; phase: "start" | "end"; trigger: string; summary?: string }
  | { type: "app-action"; action: "switch_user" | "go_home"; target?: string | null; options?: Record<string, unknown> };

export async function subscribeToDynamicStream(
  onEvent: (event: DynamicEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  await streamSse<DynamicEvent>(
    { path: "/api/dynamic/stream", method: "GET", signal },
    onEvent,
  );
}
