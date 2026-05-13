// Port of frontend/lib/bus.ts. Sticky-on-subscribe pub/sub for block-to-block
// (and server-via-SSE-to-block) messaging.

type Handler = (value: unknown) => void;
type AllHandler = () => void;

export class BlockBus {
  private topics = new Map<string, unknown>();
  private subs = new Map<string, Set<Handler>>();
  private allSubs = new Set<AllHandler>();
  private cachedSnapshot: Readonly<Record<string, unknown>> = Object.freeze({});

  publish(topic: string, value: unknown): void {
    this.topics.set(topic, value);
    this.cachedSnapshot = Object.freeze({ ...this.cachedSnapshot, [topic]: value });
    this.subs.get(topic)?.forEach((h) => {
      try { h(value); } catch (e) { console.error("[bus] handler threw", e); }
    });
    this.allSubs.forEach((h) => {
      try { h(); } catch (e) { console.error("[bus] all-sub handler threw", e); }
    });
  }

  subscribe(topic: string, handler: Handler): () => void {
    let set = this.subs.get(topic);
    if (!set) { set = new Set(); this.subs.set(topic, set); }
    set.add(handler);
    if (this.topics.has(topic)) {
      try { handler(this.topics.get(topic)); } catch (e) { console.error("[bus] sticky handler threw", e); }
    }
    return () => { set!.delete(handler); };
  }

  getValue<T = unknown>(topic: string): T | undefined {
    return this.topics.get(topic) as T | undefined;
  }

  snapshot(): Readonly<Record<string, unknown>> {
    return this.cachedSnapshot;
  }

  subscribeAll(handler: AllHandler): () => void {
    this.allSubs.add(handler);
    return () => { this.allSubs.delete(handler); };
  }
}

export type Bus = BlockBus;

export const bus: BlockBus = new BlockBus();
