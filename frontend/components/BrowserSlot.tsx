"use client";

import { useEffect, useRef } from "react";
import { getBrowserBridge, isDesktop } from "@/lib/desktopBridge";

export default function BrowserSlot({ url }: { url: string }) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!isDesktop() || !url) return;
    getBrowserBridge()
      ?.navigate(url)
      .catch((err) => console.warn("browser.navigate failed", err));
  }, [url]);

  useEffect(() => {
    const el = ref.current;
    const bridge = getBrowserBridge();
    if (!el || !bridge) return;

    let raf = 0;
    const push = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const r = el.getBoundingClientRect();
        bridge
          .setBounds({
            x: Math.round(r.left),
            y: Math.round(r.top),
            width: Math.round(r.width),
            height: Math.round(r.height),
          })
          .catch(() => {});
      });
    };

    const ro = new ResizeObserver(push);
    ro.observe(el);
    window.addEventListener("resize", push);
    window.addEventListener("scroll", push, { passive: true, capture: true });
    // Tree/drawer toggles change center-panel margin classes, which shifts
    // this div laterally without a ResizeObserver trigger. Observe class/style
    // changes to re-push bounds.
    const mo = new MutationObserver(push);
    mo.observe(document.body, {
      attributes: true,
      subtree: true,
      attributeFilter: ["class", "style"],
    });
    push();

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      mo.disconnect();
      window.removeEventListener("resize", push);
      window.removeEventListener("scroll", push, { capture: true });
      bridge.hide().catch(() => {});
    };
  }, []);

  if (!isDesktop()) return null;

  // 56px top strip keeps the existing fixed "End Session" button visible,
  // since the native WebContentsView composites above any DOM elements.
  return (
    <div className="flex-1 w-full pt-14 flex">
      <div ref={ref} className="flex-1 w-full bg-gray-900/20" />
    </div>
  );
}
