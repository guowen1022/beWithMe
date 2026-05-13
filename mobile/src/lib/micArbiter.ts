// Port of frontend/lib/micArbiter.ts. Two holders ("ptt" and "ambient"); PTT
// preempts ambient, matching the desktop spacebar-wins rule. The arbiter
// only routes who consumes the AudioRecord frame stream — it never starts
// or stops the underlying mic session.

export type MicHolder = "ptt" | "ambient";

type Listener = (active: MicHolder | null) => void;

const HOLDER_PRIORITY: Record<MicHolder, number> = { ptt: 2, ambient: 1 };

class MicArbiter {
  private active: MicHolder | null = null;
  private listeners = new Set<Listener>();

  acquire(holder: MicHolder): boolean {
    if (this.active === null || HOLDER_PRIORITY[holder] > HOLDER_PRIORITY[this.active]) {
      this.active = holder;
      this.notify();
      return true;
    }
    return this.active === holder;
  }

  release(holder: MicHolder): void {
    if (this.active === holder) {
      this.active = null;
      this.notify();
    }
  }

  current(): MicHolder | null { return this.active; }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    listener(this.active);
    return () => { this.listeners.delete(listener); };
  }

  private notify(): void {
    for (const l of this.listeners) {
      try { l(this.active); } catch (e) { console.error("[micArbiter] listener threw", e); }
    }
  }
}

export const micArbiter = new MicArbiter();
