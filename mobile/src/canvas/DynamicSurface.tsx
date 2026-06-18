// The canvas. Renders a grid of blocks for the current device class. Holds
// the mounted-block list. Subscribes to /api/dynamic/stream and applies
// UIUpdate events (mount/replace/unmount).
//
// Phase 1 auto-mounts ambient_mic on first render — mirroring the web's
// lets_begin empty-canvas auto-mount. Server-side mounts via UIUpdate also
// work, just nothing currently triggers them.

import React, { useEffect, useMemo, useRef, useState } from "react";
import { LayoutChangeEvent, StyleSheet, View } from "react-native";
import { Block, type BlockInstance } from "./Block";
import { blockRegistry, resolveBlock } from "./blockRegistry";
import { gridForDevice, scaleGridForDevice } from "../lib/grid/gridConfig";
import { getDeviceClass } from "../lib/device/deviceClass";
import { subscribeToDynamicStream, type DynamicEvent } from "../lib/api/dynamic";
import { bus } from "../lib/bus/bus";
import { useAppStore } from "../state/store";
import { parseBlockSource, templateFromBlockId } from "./parseBlockSource";

interface DynamicSurfaceProps {
  // If true, auto-mount the Phase 1 default block (ambient_mic) on first
  // render. Defaults to true; set false in tests.
  autoMountDefault?: boolean;
}

// The canvas's initial state: just the ambient mic (mobile has no launcher
// feed, so this doubles as "home" — what go_home resets back to).
function initialBlocks(autoMountDefault: boolean): BlockInstance[] {
  if (!autoMountDefault) return [];
  const entry = blockRegistry.ambient_mic;
  if (!entry) return [];
  const desktopGrid = entry.mobileGrid ?? { x: 0, y: 0, w: 4, h: 9 };
  return [{ id: "ambient_mic_default", template: "ambient_mic", grid: desktopGrid }];
}

export function DynamicSurface({ autoMountDefault = true }: DynamicSurfaceProps): React.ReactElement {
  const device = getDeviceClass();
  const gridSize = gridForDevice(device);

  const [blocks, setBlocks] = useState<BlockInstance[]>(() => initialBlocks(autoMountDefault));

  const [size, setSize] = useState({ w: 0, h: 0 });
  const onLayout = (e: LayoutChangeEvent) => {
    const { width, height } = e.nativeEvent.layout;
    setSize({ w: width, h: height });
  };

  const streamAbort = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    streamAbort.current = controller;

    subscribeToDynamicStream(handleEvent, controller.signal).catch((err) => {
      if (controller.signal.aborted) return;
      console.warn("[DynamicSurface] dynamic stream error:", err);
    });

    return () => { controller.abort(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleEvent(event: DynamicEvent): void {
    if (event.type === "ui-update") {
      const { action, block } = event;
      if (action === "unmount") {
        setBlocks((prev) => prev.filter((b) => b.id !== block.id));
        return;
      }
      // mount or replace: pull template + params + grid out of the rendered
      // source. mount_template.py embeds these as JSON literals at the top of
      // the object expression so we don't have to eval the body.
      const parsed = parseBlockSource(block.source);
      const template = parsed.template ?? templateFromBlockId(block.id);
      const grid = parsed.grid ?? { x: 0, y: 0, w: 4, h: 9 };
      const entry = resolveBlock(template);
      // Registry-level mobileGrid wins on phone — persona-authored coords
      // are sized for 12×9 desktop and don't always scale cleanly (notably:
      // notes at h:8 would overlap the mic's bottom strip). Blocks without
      // an override fall through to the scaled desktop grid.
      const useMobileOverride = device === "phone" && entry?.mobileGrid;
      const mobileGrid = useMobileOverride ? entry!.mobileGrid! : scaleGridForDevice(grid, device);
      setBlocks((prev) => {
        const existing = prev.findIndex((b) => b.id === block.id);
        const next = { id: block.id, template, grid: mobileGrid, params: parsed.params };
        if (existing >= 0) {
          const copy = prev.slice();
          copy[existing] = next;
          return copy;
        }
        return [...prev, next];
      });
      return;
    }
    if (event.type === "block-data") {
      bus.publish(event.topic, event.value);
      return;
    }
    if (event.type === "block-error") {
      console.warn("[DynamicSurface] block-error", event.block_id, event.error);
      return;
    }
    if (event.type === "app-action") {
      // The teacher's end_session tool (and app_operator's go_home) emit this.
      // Mobile has no launcher feed, so "home" = reset the canvas to its
      // initial state and start a fresh session. switch_user has no account
      // picker on mobile yet, so it's ignored.
      if (event.action === "go_home") {
        useAppStore.getState().newSession();
        setBlocks(initialBlocks(autoMountDefault));
      }
      return;
    }
    // voice-play, block-action, teacher-thinking: routed to bus for whoever
    // subscribes. Phase 1's AmbientMicBlock doesn't consume them.
    bus.publish(`__dynamic.${event.type}`, event);
  }

  const memoBlocks = useMemo(() => blocks, [blocks]);

  return (
    <View style={styles.surface} onLayout={onLayout}>
      {size.w > 0 && size.h > 0 && memoBlocks.map((b) => (
        <Block
          key={b.id}
          instance={b}
          gridSize={gridSize}
          canvasWidth={size.w}
          canvasHeight={size.h}
        />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  surface: {
    flex: 1,
    backgroundColor: "#0a0a0f",
    position: "relative",
  },
});
