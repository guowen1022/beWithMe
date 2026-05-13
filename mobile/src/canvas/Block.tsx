// Generic block container. Resolves the registry entry by template name and
// renders the component. Computes pixel position from grid coords against the
// canvas's measured size.

import React, { useMemo } from "react";
import { StyleSheet, View } from "react-native";
import type { GridCoords, GridSize } from "../lib/grid/gridConfig";
import { resolveBlock } from "./blockRegistry";

export interface BlockInstance {
  id: string;
  template: string;
  grid: GridCoords;
  params?: Record<string, unknown>;
}

interface BlockProps {
  instance: BlockInstance;
  gridSize: GridSize;
  canvasWidth: number;
  canvasHeight: number;
}

export function Block({ instance, gridSize, canvasWidth, canvasHeight }: BlockProps): React.ReactElement | null {
  const entry = resolveBlock(instance.template);

  const style = useMemo(() => {
    const cellW = canvasWidth / gridSize.cols;
    const cellH = canvasHeight / gridSize.rows;
    return {
      position: "absolute" as const,
      left: instance.grid.x * cellW,
      top: instance.grid.y * cellH,
      width: instance.grid.w * cellW,
      height: instance.grid.h * cellH,
    };
  }, [instance.grid, gridSize.cols, gridSize.rows, canvasWidth, canvasHeight]);

  if (!entry) {
    return (
      <View style={[styles.missing, style]}>
        {/* No text — render a thin amber outline so the gap is visible but the screen stays UI-free. */}
      </View>
    );
  }

  const Component = entry.component;
  return (
    <View style={style}>
      <Component blockId={instance.id} grid={instance.grid} params={instance.params} />
    </View>
  );
}

const styles = StyleSheet.create({
  missing: {
    borderWidth: 1,
    borderColor: "#f59e0b",
    borderStyle: "dashed",
    opacity: 0.4,
  },
});
