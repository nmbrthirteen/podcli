import React from "react";
import {
  getRemotionEnvironment, Img, OffthreadVideo, Video, staticFile, useVideoConfig,
} from "remotion";
import { FONT } from "../types";
import { context, muted, track } from "./brand";
import type { Brand } from "./brand";
import {
  GAP_SIZE, MEDIA_HEIGHT, MIN_FIT, SCENE_WIDTH, TYPE_SIZE, sceneHeight, sizeOf,
} from "../scene";
import type { Align, Block, Gap, Layout, Size, Tone } from "../scene";

/**
 * Blocks, drawn.
 *
 * Every preset card in this folder is a layout somebody chose once. This draws
 * whatever arrangement it is handed, which is the same set of marks with the
 * arrangement left to the plan. The one thing it does that a preset never had
 * to is fit: a preset knows how tall it is because a person sized it, and an
 * arrangement nobody has seen before does not.
 */

const WEIGHT: Record<Size, number> = {
  xs: 600, sm: 400, md: 500, lg: 500, xl: 600, xxl: 700,
};

const TRACKING: Record<Size, number> = {
  xs: 1.5, sm: 0, md: 0, lg: -0.5, xl: -1, xxl: -4,
};

const LINE_HEIGHT: Record<Size, number> = {
  xs: 1.3, sm: 1.3, md: 1.25, lg: 1.25, xl: 1.12, xxl: 0.95,
};

const MARK = { bar: 26, cap: 8, gap: 30, rule: 5, dot: 26 };

const FLEX: Record<Align, string> = {
  start: "flex-start", center: "center", end: "flex-end",
};

const toneOf = (brand: Brand, accent: string, tone: Tone | undefined, fallback: Tone) => {
  switch (tone ?? fallback) {
    case "accent": return accent;
    case "muted": return muted(brand.ink);
    case "context": return context(brand.ink);
    default: return brand.ink;
  }
};

const source = (src: string) => (src.startsWith("http") ? src : staticFile(src));

type Paint = {
  brand: Brand;
  accent: string;
  /** Reference pixels to device pixels, with the fit shrink already folded in. */
  unit: number;
  /** Device pixels, for anything measured against the width it was given. */
  width: number;
  /** Device pixels a media block may take, after the fit pass. */
  mediaHeight: number;
};

const Piece: React.FC<{ block: Block; paint: Paint }> = ({ block, paint }) => {
  const { brand, accent, unit } = paint;
  const { fps } = useVideoConfig();

  if (block.type === "group") {
    return <Stack blocks={block.blocks} layout={block.layout} gap={block.gap} paint={paint} />;
  }

  if (block.type === "rule") {
    return (
      <div
        style={{
          height: MARK.rule * unit,
          width: "100%",
          backgroundColor: toneOf(brand, accent, block.tone, "context"),
        }}
      />
    );
  }

  if (block.type === "text") {
    const size = sizeOf(block);
    return (
      <div
        style={{
          fontFamily: FONT,
          fontSize: TYPE_SIZE[size] * unit,
          fontWeight: WEIGHT[size],
          lineHeight: LINE_HEIGHT[size],
          letterSpacing: TRACKING[size] * unit,
          textTransform: block.caps ? "uppercase" : undefined,
          color: toneOf(brand, accent, block.tone, size === "xs" ? "muted" : "ink"),
          textAlign: block.align ?? "start",
        }}
      >
        {block.text}
        {block.emphasis && <span style={{ color: accent }}> {block.emphasis}</span>}
      </div>
    );
  }

  if (block.type === "quote") {
    const size = sizeOf(block);
    return (
      <>
        <div
          style={{
            fontFamily: FONT,
            fontSize: TYPE_SIZE[size] * unit,
            fontWeight: 500,
            lineHeight: LINE_HEIGHT[size],
            letterSpacing: -0.5 * unit,
            color: toneOf(brand, accent, block.tone, "ink"),
            /*
             * No hanging indent, unlike the quote card.
             *
             * A card is one quote and can hang its opening mark into the
             * frame's own padding. A block sits in a stack that is clipped at
             * the room it was given, so the same eighteen pixels put half the
             * mark outside the box and drew it as a single tick. Aligning with
             * the blocks above and below is what this one wants anyway.
             */
          }}
        >
          {"“"}{block.text}{"”"}
        </div>
        {block.attribution && (
          <div
            style={{
              fontFamily: FONT,
              fontSize: TYPE_SIZE.xs * unit,
              fontWeight: 600,
              letterSpacing: 1.5 * unit,
              color: muted(brand.ink),
              marginTop: GAP_SIZE.group * unit,
            }}
          >
            {block.attribution}
          </div>
        )}
      </>
    );
  }

  if (block.type === "list") {
    const size = sizeOf(block);
    return (
      <>
        {block.items.map((item, i) => (
          <div
            key={i}
            style={{
              display: "flex",
              alignItems: block.numbered ? "baseline" : "center",
              gap: 20 * unit,
              marginTop: i === 0 ? 0 : GAP_SIZE.tight * unit,
            }}
          >
            {block.numbered ? (
              <span
                style={{
                  fontFamily: FONT,
                  fontSize: TYPE_SIZE.xs * unit,
                  fontWeight: 700,
                  color: i === 0 ? accent : muted(brand.ink),
                  minWidth: 26 * unit,
                }}
              >
                {i + 1}
              </span>
            ) : (
              <div
                style={{
                  width: 26 * unit,
                  height: 3 * unit,
                  backgroundColor: i === 0 ? accent : muted(brand.ink),
                  flexShrink: 0,
                }}
              />
            )}
            <div
              style={{
                fontFamily: FONT,
                fontSize: TYPE_SIZE[size] * unit,
                fontWeight: 500,
                lineHeight: LINE_HEIGHT[size],
                color: toneOf(brand, accent, block.tone, "ink"),
              }}
            >
              {item}
            </div>
          </div>
        ))}
      </>
    );
  }

  if (block.type === "bars") {
    const top = Math.max(...block.rows.map((row) => row.value)) || 1;
    return (
      <>
        {block.rows.map((row, i) => (
          <div key={i} style={{ marginTop: i === 0 ? 0 : MARK.gap * unit }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "baseline",
                marginBottom: 8 * unit,
              }}
            >
              <span
                style={{ fontFamily: FONT, fontSize: TYPE_SIZE.xs * unit, color: muted(brand.ink) }}
              >
                {row.label}
              </span>
              <span
                style={{
                  fontFamily: FONT,
                  fontSize: TYPE_SIZE.sm * unit,
                  fontWeight: 700,
                  color: row.subject ? brand.ink : muted(brand.ink),
                }}
              >
                {row.display ?? row.value}
              </span>
            </div>
            <div
              style={{
                width: `${Math.max(4, (row.value / top) * 100)}%`,
                height: MARK.bar * unit,
                backgroundColor: row.subject ? accent : context(brand.ink),
                borderRadius: `0 ${MARK.cap * unit}px ${MARK.cap * unit}px 0`,
              }}
            />
          </div>
        ))}
      </>
    );
  }

  if (block.type === "meter") {
    const share = Math.max(0, Math.min(1, block.value));
    return (
      <>
        <div
          style={{
            fontFamily: FONT,
            fontSize: TYPE_SIZE.xxl * 0.72 * unit,
            fontWeight: 700,
            lineHeight: 1,
            letterSpacing: -3 * unit,
            color: accent,
            marginBottom: GAP_SIZE.tight * unit,
          }}
        >
          {block.display ?? `${Math.round(share * 100)}%`}
        </div>
        <div
          style={{
            width: "100%",
            height: MARK.bar * unit,
            backgroundColor: track(accent),
            borderRadius: MARK.cap * unit,
            overflow: "hidden",
          }}
        >
          <div style={{ width: `${share * 100}%`, height: "100%", backgroundColor: accent }} />
        </div>
      </>
    );
  }

  if (block.type === "steps") {
    const last = block.points.length - 1;
    return (
      <div style={{ display: "flex", alignItems: "center" }}>
        {block.points.map((point, i) => (
          <React.Fragment key={i}>
            {i > 0 && (
              <div
                style={{
                  flex: 1,
                  height: MARK.rule * unit,
                  background: i === last
                    ? `linear-gradient(90deg, ${context(brand.ink)}, ${accent})`
                    : context(brand.ink),
                  margin: `0 ${18 * unit}px`,
                  transform: `translateY(${-14 * unit}px)`,
                }}
              />
            )}
            <div style={{ textAlign: "center" }}>
              <div
                style={{
                  width: MARK.dot * unit,
                  height: MARK.dot * unit,
                  borderRadius: "50%",
                  backgroundColor: i === last ? accent : context(brand.ink),
                  margin: "0 auto",
                }}
              />
              <div
                style={{
                  fontFamily: FONT,
                  fontSize: TYPE_SIZE.md * unit,
                  fontWeight: 700,
                  color: brand.ink,
                  marginTop: 12 * unit,
                  lineHeight: 1,
                }}
              >
                {point.value}
              </div>
              {point.note && (
                <div
                  style={{
                    fontFamily: FONT,
                    fontSize: TYPE_SIZE.xs * unit,
                    color: muted(brand.ink),
                    marginTop: 6 * unit,
                  }}
                >
                  {point.note}
                </div>
              )}
            </div>
          </React.Fragment>
        ))}
      </div>
    );
  }

  if (block.type === "chip") {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 28 * unit }}>
        {block.src && (
          <Img
            src={source(block.src)}
            style={{
              width: 132 * unit,
              height: 132 * unit,
              objectFit: "cover",
              borderRadius: 20 * unit,
              flexShrink: 0,
              outline: `${1 * unit}px solid rgba(255,255,255,0.1)`,
              outlineOffset: `${-1 * unit}px`,
            }}
          />
        )}
        <div>
          <div
            style={{
              fontFamily: FONT,
              fontSize: TYPE_SIZE.md * unit,
              fontWeight: 700,
              lineHeight: 1.15,
              color: brand.ink,
            }}
          >
            {block.name}
          </div>
          {block.note && (
            <div
              style={{
                fontFamily: FONT,
                fontSize: TYPE_SIZE.sm * unit,
                color: muted(brand.ink),
                lineHeight: 1.3,
                marginTop: 6 * unit,
              }}
            >
              {block.note}
            </div>
          )}
        </div>
      </div>
    );
  }

  if (!block.src) return null;

  const Frame = getRemotionEnvironment().isRendering ? OffthreadVideo : Video;
  return (
    <>
      {block.media === "video" ? (
        <Frame
          src={source(block.src)}
          startFrom={Math.max(0, Math.round((block.startAt ?? 0) * fps))}
          muted
          style={{
            width: "100%",
            height: paint.mediaHeight,
            backgroundColor: "#000",
            objectFit: block.fit === "fit" ? "contain" : "cover",
          }}
        />
      ) : (
        <Img
          src={source(block.src)}
          style={{
            width: "100%",
            height: paint.mediaHeight,
            objectFit: block.fit === "fill" ? "cover" : "contain",
            borderRadius: 16 * unit,
            outline: `${1 * unit}px solid rgba(255,255,255,0.1)`,
            outlineOffset: `${-1 * unit}px`,
          }}
        />
      )}
      {block.caption && (
        <div
          style={{
            fontFamily: FONT,
            fontSize: TYPE_SIZE.xs * unit,
            lineHeight: 1.3,
            color: muted(brand.ink),
            marginTop: GAP_SIZE.tight * unit,
          }}
        >
          {block.caption}
        </div>
      )}
    </>
  );
};

const Stack: React.FC<{
  blocks: Block[];
  layout?: Layout;
  gap?: Gap;
  paint: Paint;
}> = ({ blocks, layout = "stack", gap = "group", paint }) => {
  const drawn = blocks.filter((block) => block.type !== "group" || block.blocks.length);
  if (!drawn.length) return null;

  const gutter = GAP_SIZE[gap] * paint.unit;
  const columns = layout === "grid" ? 2 : drawn.length;
  const share = layout === "stack"
    ? paint.width
    : Math.max(1, (paint.width - gutter * (columns - 1)) / columns);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: layout === "stack" ? "column" : "row",
        flexWrap: layout === "grid" ? "wrap" : "nowrap",
        gap: gutter,
        width: "100%",
        minWidth: 0,
        alignItems: layout === "stack" ? "stretch" : "flex-start",
      }}
    >
      {drawn.map((block, i) => (
        <div
          key={i}
          style={{
            display: "flex",
            flexDirection: "column",
            minWidth: 0,
            alignItems: block.align ? FLEX[block.align] : undefined,
            ...(layout === "stack"
              ? {}
              : {
                flexGrow: block.grow ?? 1,
                flexBasis: layout === "grid" ? `calc(50% - ${gutter / 2}px)` : 0,
              }),
          }}
        >
          <Piece block={block} paint={{ ...paint, width: layout === "stack" ? paint.width : share }} />
        </div>
      ))}
    </div>
  );
};

/**
 * How tall a media block is drawn, and how much the type has to give.
 *
 * Two passes rather than one, because the two shrink at different costs. A
 * picture cropped a little tighter is still the picture; type shrunk to fit a
 * picture that kept its full height is a card nobody reads at arm's length.
 * So the media band gives up its room first, down to the height below which
 * it stops saying anything, and only then does the type scale.
 */
export function fitScene(
  blocks: Block[],
  { layout = "stack", gap = "group", room, width = SCENE_WIDTH }: {
    layout?: Layout; gap?: Gap; room: number; width?: number;
  },
): { fit: number; mediaHeight: number } {
  const asked = MEDIA_HEIGHT.max;
  const at = (height: number) =>
    sceneHeight(withMediaHeight(blocks, height), layout, gap, width);

  const wanted = at(asked);
  if (!(room > 0) || wanted <= room) return { fit: 1, mediaHeight: asked };

  const mediaHeight = Math.max(MEDIA_HEIGHT.min, asked - (wanted - room));
  const left = at(mediaHeight);
  return {
    fit: left <= room ? 1 : Math.max(MIN_FIT, room / left),
    mediaHeight,
  };
}

const withMediaHeight = (blocks: Block[], height: number): Block[] =>
  blocks.map((block) => {
    if (block.type === "media") return { ...block, height };
    if (block.type === "group") {
      return { ...block, blocks: withMediaHeight(block.blocks, height) };
    }
    return block;
  });

export const Scene: React.FC<{
  blocks: Block[];
  layout?: Layout;
  gap?: Gap;
  brand: Brand;
  accent: string;
  /** Reference pixels to device pixels, before any fit shrink. */
  scale: number;
  /** Device pixels the scene has to sit inside. */
  room: number;
}> = ({ blocks, layout = "stack", gap = "group", brand, accent, scale, room }) => {
  const { fit, mediaHeight } = fitScene(blocks, { layout, gap, room: room / scale });
  const unit = scale * fit;

  return (
    /*
     * Clipped at the room it was given, because the shrink has a floor.
     *
     * An arrangement that cannot fit even at the smallest size worth reading
     * is a plan somebody has to fix. What this decides is which way it fails:
     * cut off at the top, or drawn up across the speaker's face and down
     * behind the caption pill. The first reads as a card that ran long; the
     * second reads as a broken render.
     */
    <div style={{ maxHeight: room, overflow: "hidden", width: "100%", minWidth: 0 }}>
      <Stack
        blocks={blocks}
        layout={layout}
        gap={gap}
        paint={{
          brand,
          accent,
          unit,
          width: SCENE_WIDTH * scale,
          mediaHeight: mediaHeight * scale,
        }}
      />
    </div>
  );
};
