// Pure HTML → React Native mapper for the note block.
//
// Persona authors HTML in the note grammar (see
// infra/render/note_grammar.py); the backend pre-renders Mermaid
// diagrams to inline <svg> and sanitizes the payload before either
// surface sees it. This mapper walks the resulting tree and produces a
// React Native element tree built from View / Text / Image / SvgXml —
// no WebView, no HTML renderer dep beyond htmlparser2 + react-native-svg.
//
// Layout rules that come from RN, not the grammar:
//   * <Text> can nest <Text>, but a <View> can't be inside a <Text>.
//     So inline-only children of a block element are wrapped in a
//     single <Text>; block children become sibling <View>/<Text> blocks.
//   * `style` arrays are how we compose class allowlists onto a node.

import { parseDocument } from "htmlparser2";
import type { ChildNode, Element, Text as DhText } from "domhandler";
import React from "react";
import { Image, Linking, Text, View } from "react-native";
import { SvgXml } from "react-native-svg";

import { STYLES, TAG_STYLES } from "./NoteBlock.styles";

type StyleObj = Record<string, unknown>;

const INLINE_TAGS = new Set([
  "span", "strong", "em", "code", "mark", "ins", "del", "a", "br",
]);

function isElement(n: ChildNode): n is Element { return n.type === "tag"; }
function isText(n: ChildNode):    n is DhText  { return n.type === "text"; }

function classList(node: Element): string[] {
  const c = node.attribs?.class;
  if (!c) return [];
  return c.split(/\s+/).filter(Boolean);
}

function lookupClassStyles(classes: string[]): StyleObj[] {
  const out: StyleObj[] = [];
  for (const c of classes) {
    const s = (STYLES as Record<string, StyleObj>)[c];
    if (s) out.push(s);
  }
  return out;
}

// Serialize an <svg> subtree back to an XML string for SvgXml. We can't
// just hand the SvgXml component an htmlparser2 node — it wants raw XML.
// The subtree only contains element + text nodes (mermaid output, run
// through our SVG safety scrub) so a tiny recursive serializer is enough.
function serializeSvg(node: Element): string {
  const attrs = Object.entries(node.attribs || {})
    .map(([k, v]) => ` ${k}="${escapeAttr(String(v))}"`)
    .join("");
  let inner = "";
  for (const c of node.children || []) {
    if (isElement(c))     inner += serializeSvg(c);
    else if (isText(c))   inner += escapeText(c.data);
  }
  return `<${node.name}${attrs}>${inner}</${node.name}>`;
}

function escapeAttr(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}
function escapeText(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
}

// Counter for stable keys within a single render. Reset at the entry
// point so re-renders produce the same keys for the same input.
let _key = 0;
function nextKey(): string { return `r${_key++}`; }

// ---- inline rendering ----------------------------------------------------
// Inline nodes always go inside a <Text> parent. Result is a React node
// suitable for use as a child of <Text>.

function renderInline(node: Element): React.ReactNode {
  const k = nextKey();
  const cls = classList(node);
  const css = lookupClassStyles(cls);
  switch (node.name) {
    case "br":
      return <Text key={k}>{"\n"}</Text>;
    case "a": {
      const href = node.attribs?.href || "";
      return (
        <Text
          key={k}
          style={[TAG_STYLES.a, ...css]}
          onPress={() => { if (href) Linking.openURL(href).catch(() => {}); }}
        >
          {renderInlineChildren(node)}
        </Text>
      );
    }
    case "strong": return <Text key={k} style={[TAG_STYLES.strong, ...css]}>{renderInlineChildren(node)}</Text>;
    case "em":     return <Text key={k} style={[TAG_STYLES.em,     ...css]}>{renderInlineChildren(node)}</Text>;
    case "code":   return <Text key={k} style={[TAG_STYLES.code,   ...css]}>{renderInlineChildren(node)}</Text>;
    case "mark":   return <Text key={k} style={[TAG_STYLES.mark,   ...css]}>{renderInlineChildren(node)}</Text>;
    case "ins":    return <Text key={k} style={[TAG_STYLES.ins,    ...css]}>{renderInlineChildren(node)}</Text>;
    case "del":    return <Text key={k} style={[TAG_STYLES.del,    ...css]}>{renderInlineChildren(node)}</Text>;
    case "span":   return <Text key={k} style={css}>{renderInlineChildren(node)}</Text>;
    default:       return null;
  }
}

function renderInlineChildren(parent: Element): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  for (const c of parent.children || []) {
    if (isText(c)) out.push(c.data);
    else if (isElement(c) && INLINE_TAGS.has(c.name)) out.push(renderInline(c));
    // Block elements inside an inline context shouldn't appear per the
    // grammar; silently drop if they do.
  }
  return out;
}

// ---- block rendering -----------------------------------------------------

function renderBlock(node: Element): React.ReactElement | null {
  const k = nextKey();
  const cls = classList(node);
  const css = lookupClassStyles(cls);

  switch (node.name) {
    case "h1": case "h2": case "h3": case "h4": {
      const tagStyle = TAG_STYLES[node.name as "h1" | "h2" | "h3" | "h4"];
      return <Text key={k} style={[tagStyle, ...css]}>{renderInlineChildren(node)}</Text>;
    }
    case "p":
      return <Text key={k} style={[TAG_STYLES.p, ...css]}>{renderInlineChildren(node)}</Text>;

    case "hr":
      return <View key={k} style={TAG_STYLES.hr} />;

    case "blockquote":
      return <View key={k} style={[TAG_STYLES.blockquote, ...css]}>{renderBlockChildren(node)}</View>;

    case "pre": {
      // Fenced code block. <pre><code>…</code></pre>; preserve the raw text
      // (newlines intact) in a single monospace Text.
      return (
        <View key={k} style={TAG_STYLES.pre}>
          <Text style={TAG_STYLES.preCode}>{extractText(node)}</Text>
        </View>
      );
    }

    case "table":
      return renderTable(node, k);

    case "ul":
    case "ol": {
      const items: React.ReactElement[] = [];
      let i = 1;
      for (const c of node.children || []) {
        if (isElement(c) && c.name === "li") {
          const marker = node.name === "ul" ? "•" : `${i++}.`;
          items.push(
            <View key={nextKey()} style={TAG_STYLES.li}>
              <Text style={TAG_STYLES.liMarker}>{marker}</Text>
              <View style={TAG_STYLES.liContent}>{renderBlockChildren(c)}</View>
            </View>
          );
        }
      }
      return <View key={k} style={[TAG_STYLES.ul, ...css]}>{items}</View>;
    }

    case "img": {
      const src = node.attribs?.src;
      if (!src || !src.startsWith("https://")) return null;
      // The aspect-* classes drive sizing through the style cascade.
      return <Image key={k} source={{ uri: src }} style={css.length ? css : [STYLES["bw-image"]]} />;
    }

    case "div":
    case "section":
    case "article":
    case "aside":
    case "header":
    case "footer": {
      // bw-diagram wrapper: pull the inner <svg>, hand the XML to SvgXml.
      if (cls.includes("bw-diagram")) {
        const svgEl = (node.children || []).find(
          (c) => isElement(c) && c.name === "svg",
        ) as Element | undefined;
        if (!svgEl) return null;
        const xml = serializeSvg(svgEl);
        // Parse viewBox → aspectRatio so the View has a real height.
        // Also: pin width to the parent (alignSelf:stretch + width:100%)
        // and clip overflow — Android RN's default overflow is "visible",
        // which lets SvgXml draw past its bounds when its intrinsic width
        // exceeds the screen. preserveAspectRatio="xMidYMid meet" tells
        // the SVG to scale into the box.
        const vb = (svgEl.attribs?.viewBox || "").trim().split(/[\s,]+/).map(Number);
        const aspect = vb.length === 4 && vb[2] > 0 && vb[3] > 0
          ? { aspectRatio: vb[2] / vb[3] }
          : null;
        return (
          <View
            key={k}
            style={[
              STYLES["bw-diagram"],
              ...css.filter((s) => s !== STYLES["bw-diagram"]),
              { width: "100%", alignSelf: "stretch", overflow: "hidden" },
              aspect,
            ]}
          >
            <SvgXml
              xml={xml}
              width="100%"
              height="100%"
              preserveAspectRatio="xMidYMid meet"
            />
          </View>
        );
      }
      return <View key={k} style={css}>{renderBlockChildren(node)}</View>;
    }

    default:
      return null;
  }
}

// Walk a block element's children, grouping consecutive inline children
// into a single wrapping <Text>. Block children pass through as siblings.
function renderBlockChildren(parent: Element): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  let inlineBuf: React.ReactNode[] = [];
  const flush = () => {
    if (inlineBuf.length > 0) {
      out.push(<Text key={nextKey()} style={TAG_STYLES.p}>{inlineBuf}</Text>);
      inlineBuf = [];
    }
  };
  for (const child of parent.children || []) {
    if (isText(child)) {
      // Skip pure whitespace text nodes between block elements (otherwise
      // every newline between <div>s becomes its own paragraph).
      if (child.data.trim() === "") continue;
      inlineBuf.push(child.data);
    } else if (isElement(child)) {
      if (INLINE_TAGS.has(child.name)) {
        inlineBuf.push(renderInline(child));
      } else {
        flush();
        const block = renderBlock(child);
        if (block) out.push(block);
      }
    }
  }
  flush();
  return out;
}

// Concatenate all descendant text — used to recover the raw source of a
// <pre> code block (newlines included) for a single monospace <Text>.
function extractText(node: Element): string {
  let s = "";
  for (const c of node.children || []) {
    if (isText(c)) s += c.data;
    else if (isElement(c)) s += extractText(c);
  }
  return s;
}

// Render a GFM <table> as stacked rows of flex cells. RN has no native
// table, so each <tr> is a flexDirection:row View and each cell flexes
// equally. Header cells (<th> or anything under <thead>) get the mono
// uppercase treatment that mirrors `.bw-card thead th` on web.
function renderTable(node: Element, key: string): React.ReactElement {
  const rows: React.ReactElement[] = [];
  const pushRow = (tr: Element, isHead: boolean) => {
    const cells: React.ReactNode[] = [];
    for (const cell of tr.children || []) {
      if (!isElement(cell) || (cell.name !== "td" && cell.name !== "th")) continue;
      const head = isHead || cell.name === "th";
      cells.push(
        <View key={nextKey()} style={TAG_STYLES.tableCell}>
          <Text style={head ? TAG_STYLES.tableHeadCellText : TAG_STYLES.tableCellText}>
            {renderInlineChildren(cell)}
          </Text>
        </View>
      );
    }
    rows.push(
      <View key={nextKey()} style={[TAG_STYLES.tableRow, isHead ? TAG_STYLES.tableHeadRow : null]}>
        {cells}
      </View>
    );
  };
  for (const section of node.children || []) {
    if (!isElement(section)) continue;
    if (section.name === "thead" || section.name === "tbody") {
      const isHead = section.name === "thead";
      for (const tr of section.children || []) {
        if (isElement(tr) && tr.name === "tr") pushRow(tr, isHead);
      }
    } else if (section.name === "tr") {
      pushRow(section, false);
    }
  }
  return <View key={key} style={TAG_STYLES.table}>{rows}</View>;
}

/** Entry point: parse a sanitized note HTML string into a RN tree. */
export function renderNoteHtml(html: string): React.ReactNode[] {
  _key = 0;
  if (!html || !html.trim()) return [];
  const doc = parseDocument(html, { lowerCaseTags: false });
  const out: React.ReactNode[] = [];
  for (const c of doc.children) {
    if (isElement(c)) {
      const el = renderBlock(c);
      if (el) out.push(el);
    } else if (isText(c) && c.data.trim() !== "") {
      out.push(<Text key={nextKey()} style={TAG_STYLES.p}>{c.data}</Text>);
    }
  }
  return out;
}
