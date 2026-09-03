#!/usr/bin/env node
/**
 * Remotion render script — called by Python backend.
 *
 * Caches the bundle under PODCLI_CACHE_DIR/remotion-bundle/ (default data/cache/)
 * so subsequent renders skip the ~15-20s bundling step.
 *
 * Usage:
 *   node remotion/render.mjs \
 *     --video /path/to/cropped.mp4 \
 *     --words /path/to/words.json \
 *     --style branded \
 *     --output /path/to/captioned.mp4 \
 *     [--logo /path/to/logo.png] \
 *     [--topic "Fitness"] [--topic-position top-left] \
 *     [--progress] [--progress-color '#3B9CFF'] \
 *     [--cards '[{"kind":"stat","start":2,"end":6,"value":"70%"}]'] \
 *     [--brand '{"accent":"#4C9DF5","ink":"#FFFFFF","surface":"#0A0D14"}'] \
 *     [--font-family "Inter"] \
 *     [--fps 30]
 *
 *   node remotion/render.mjs --prebundle   # Bundle only, no render
 */

import { ensureBrowser, renderMedia, selectComposition } from "@remotion/renderer";
import { getCachedBundle } from "./bundle-cache.mjs";
import path from "path";
import fs from "fs";
import os from "os";
import crypto from "crypto";


const BOOLEAN_FLAGS = new Set(["prebundle", "keep-overlay", "progress"]);

function parseArgs() {
  const args = process.argv.slice(2);
  const opts = {};
  for (let i = 0; i < args.length; i++) {
    const key = args[i].replace(/^--/, "");
    if (BOOLEAN_FLAGS.has(key)) {
      opts[key] = true;
      continue;
    }
    if (args[i].startsWith("--") && i + 1 < args.length) {
      opts[key] = args[i + 1];
      i++;
    }
  }
  return opts;
}

// Mirrors the 60-160 clamp in backend/services/caption_renderer.py so the ASS
// and Remotion caption paths render the same size for a given request.
function clampCaptionFontScale(raw) {
  const value = Number(raw);
  return Number.isFinite(value) ? Math.min(160, Math.max(60, value)) : 100;
}

function clampLogoScale(raw) {
  const value = Number(raw);
  return Number.isFinite(value) ? Math.min(1.5, Math.max(0.5, value)) : 1;
}


// Remotion's 30s default is measured against the font delayRender in Root.tsx,
// which competes with evaluating the whole bundle. That bundle has three
// compositions now, and the slowest CI runner does not finish inside 30s.
//
// A fixed cap on top of that was wrong in the other direction: a card-heavy
// clip renders roughly 13x slower than realtime on the container's actual CPU
// quota, not the few seconds a bundle load costs, and a 120s ceiling killed
// the render itself mid-flight, not just a slow font load — the "delayRender
// not cleared" it reported was a symptom of the whole page stalling under
// load, not a broken font. Scaled to the clip so a 3-second clip still fails
// fast and a full-length one gets the minutes it actually needs; overridable
// for a box whose real throughput has already been measured.
const timeoutFor = (durationSec) => {
  const override = parseInt(process.env.PODCLI_REMOTION_TIMEOUT_MS ?? "", 10);
  if (Number.isFinite(override)) return Math.max(1, override);
  // durationSec is measured by ffprobe and rarely lands on a whole number;
  // Remotion rejects a fractional timeoutInMilliseconds outright.
  return Math.round(Math.min(50 * 60_000, Math.max(120_000, 90_000 + durationSec * 15_000)));
};

async function main() {
  const opts = parseArgs();

  // Prebundle mode — just bundle and exit
  if (opts.prebundle) {
    const t0 = Date.now();
    const loc = await getCachedBundle({ onBundle: () => console.log("  Remotion: bundling (first run, or src/config changed)...") });
    console.log(`Remotion bundle ready at ${loc} (${Date.now() - t0}ms)`);
    return;
  }

  if (!opts.video || !opts.words || !opts.output) {
    console.error(
      "Usage: node render.mjs --video <path> --words <path> --style <name> --output <path>"
    );
    process.exit(1);
  }

  const wordsData = JSON.parse(fs.readFileSync(opts.words, "utf-8"));
  // Support both old format (array) and new format ({words, faceY})
  const words = Array.isArray(wordsData) ? wordsData : wordsData.words || [];
  const faceY = Array.isArray(wordsData) ? null : wordsData.faceY ?? null;
  const styleName = opts.style || "branded";
  const fps = parseInt(opts.fps || "30", 10);

  // Get cached bundle first (needed to copy assets into it)
  const t0 = Date.now();
  const bundleLocation = await getCachedBundle({ onBundle: () => console.log("  Remotion: bundling (first run, or src/config changed)...") });
  const bundleMs = Date.now() - t0;
  if (bundleMs > 1000) {
    console.log(`  bundled in ${(bundleMs / 1000).toFixed(1)}s`);
  }

  // Serve video and logo via a tiny local HTTP server
  // (Remotion's bundled server can't serve dynamically added files)
  const http = await import("http");
  const assetServer = http.createServer((req, res) => {
    let filePath = null;
    if (req.url === "/clip.mp4") filePath = path.resolve(opts.video);
    else if (req.url === "/logo.png" && opts.logo) filePath = path.resolve(opts.logo);

    if (filePath && fs.existsSync(filePath)) {
      const stat = fs.statSync(filePath);
      const ext = path.extname(filePath).slice(1);
      const mime = { mp4: "video/mp4", png: "image/png", jpg: "image/jpeg", webp: "image/webp" }[ext] || "application/octet-stream";
      res.writeHead(200, { "Content-Type": mime, "Content-Length": stat.size, "Access-Control-Allow-Origin": "*" });
      fs.createReadStream(filePath).pipe(res);
    } else {
      res.writeHead(404);
      res.end();
    }
  });
  await new Promise((resolve) => assetServer.listen(0, "127.0.0.1", resolve));
  const assetPort = assetServer.address().port;

  // Ensure the asset server is closed and the overlay temp file removed on any exit path
  let captionOverlay;
  const closeAssetServer = () => { try { assetServer.close(); } catch {} };
  const cleanupOnSignal = () => {
    closeAssetServer();
    if (captionOverlay && !opts["keep-overlay"]) {
      try { fs.unlinkSync(captionOverlay); } catch {}
    }
    process.exit(1);
  };
  process.on("SIGINT", cleanupOnSignal);
  process.on("SIGTERM", cleanupOnSignal);

  const videoSrc = `http://127.0.0.1:${assetPort}/clip.mp4`;
  const logoSrc = opts.logo ? `http://127.0.0.1:${assetPort}/logo.png` : undefined;

  // Probe video dimensions and duration
  let renderW = 1080;
  let renderH = 1920;
  let videoDuration = null;
  /*
   * The shape the overlay is drawn at, measured rather than assumed.
   *
   * PODCLI_FFPROBE for the same reason the composite uses PODCLI_FFMPEG:
   * nothing guarantees one on PATH beside a hermetic runtime. This used to
   * swallow the failure whole, which left the defaults below standing: a
   * horizontal clip then had a 1080x1920 overlay drawn over a 1920x1080
   * frame, so every caption and the logo came out the wrong size and in the
   * wrong place, and nothing anywhere said the measurement had not happened.
   */
  const { spawnSync } = await import("child_process");
  const ffprobe = process.env.PODCLI_FFPROBE || "ffprobe";
  const probeFor = (...entries) => {
    const r = spawnSync(
      ffprobe,
      ["-v", "error", ...entries, path.resolve(opts.video)],
      { encoding: "utf-8", timeout: 5000 },
    );
    if (r.error || r.status !== 0) return null;
    return String(r.stdout ?? "").trim();
  };

  const size = probeFor(
    "-select_streams", "v:0", "-show_entries", "stream=width,height",
    "-of", "csv=s=x:p=0",
  );
  if (size) {
    const [w, h] = size.split("x").map(Number);
    if (w > 0 && h > 0) {
      renderW = w;
      renderH = h;
    }
  } else {
    // Loud, because the fallback is a guess about the shape of the frame.
    process.stderr.write(
      `  could not measure the video with ${ffprobe}; drawing the overlay at `
      + `${renderW}x${renderH}, which is wrong for anything else\n`,
    );
  }

  const durStr = probeFor(
    "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1",
  );
  // Same silence as the shape, and the same cost: an overlay cut to the last
  // word rather than to the file stops before the clip does.
  const measured = Number.parseFloat(durStr ?? "");
  if (Number.isFinite(measured) && measured > 0) {
    videoDuration = measured;
  } else {
    process.stderr.write(
      `  could not measure the duration with ${ffprobe}; falling back to the `
      + `last word, so the overlay may end before the clip does\n`,
    );
  }

  // Calculate duration from video or word timing
  const lastWord = words[words.length - 1];
  const durationSec = videoDuration || (lastWord ? lastWord.end + 0.5 : 30);
  const durationInFrames = Math.ceil(durationSec * fps);

  // Who is speaking, for the lower third. Sent whole so the composition takes
  // one prop rather than six loose ones.
  const nameCard = opts["name-card"]
    ? {
        title: opts["name-card"],
        subtitle: opts["name-card-sub"] || undefined,
        seconds: opts["name-card-seconds"] ? parseFloat(opts["name-card-seconds"]) : undefined,
        accent: opts["name-card-accent"] || undefined,
      }
    : null;

  // How each part arrives and leaves. A JSON object rather than a flag per
  // property: it is one value in a template, and it travels as one.
  let motion = null;
  if (opts.motion) {
    try {
      motion = JSON.parse(opts.motion);
    } catch {
      console.error("Ignoring --motion: not valid JSON");
    }
  }

  /*
   * The parts a template switches on.
   *
   * Absent means null, and null draws nothing, so a caller that sends none of
   * these renders exactly what it rendered before they existed. A malformed
   * value is dropped with a line on stderr rather than failing the render: a
   * clip without its chip is worth more than no clip.
   */
  const json = (name) => {
    if (!opts[name]) return null;
    try {
      return JSON.parse(opts[name]);
    } catch {
      console.error(`Ignoring --${name}: not valid JSON`);
      return null;
    }
  };

  const cards = json("cards");
  const brand = json("brand");
  const topic = opts.topic
    ? {
        label: opts.topic,
        position: opts["topic-position"] || "top-left",
        ...(opts["topic-color"] ? { color: opts["topic-color"] } : {}),
        ...(opts["topic-background"] ? { background: opts["topic-background"] } : {}),
      }
    : null;
  const progress = opts.progress
    ? { ...(opts["progress-color"] ? { color: opts["progress-color"] } : {}) }
    : null;

  const inputProps = {
    videoSrc,
    words,
    styleName,
    logoSrc,
    faceY,
    nameCard,
    motion,
    durationInFrames,
    fps,
    captionPosition: opts["caption-position"] || "auto",
    captionFontScale: clampCaptionFontScale(opts["caption-font-scale"]),
    logoPosition: opts["logo-position"] || "top-left",
    logoScale: clampLogoScale(opts["logo-scale"]),
    topic,
    progress,
    cards: Array.isArray(cards) ? cards : null,
    brand: brand && typeof brand === "object" ? brand : null,
    fontFamily: opts["font-family"] || null,
  };

  console.log(
    `Remotion: ${words.length} words, ${styleName}, ${renderW}x${renderH}, ${durationInFrames}f`
  );

  // The browser the captions are drawn in. Left to itself Remotion downloads
  // one on first use, next to whatever directory the process was started in,
  // which on a container is a layer and not a volume: recreating it downloads
  // 120MB again. PODCLI_BROWSER names one the machine already has instead.
  //
  // Stated up front rather than left to the render. ensureBrowser fails
  // immediately and by name when the path is wrong, where the same mistake
  // inside renderMedia surfaces as a frame that would not draw.
  const browserExecutable = process.env.PODCLI_BROWSER || null;
  // headless-shell is Remotion's own build and is driven with --headless=old.
  // Chrome removed old headless in 132 and Debian trixie ships Chromium 151,
  // so a browser we were handed rather than downloaded gets the flag that
  // still exists.
  const chromeMode = browserExecutable ? "chrome-for-testing" : "headless-shell";
  await ensureBrowser({ browserExecutable, chromeMode });

  try {
    // Select composition
    const composition = await selectComposition({
      serveUrl: bundleLocation,
      id: "CaptionedClip",
      inputProps,
      timeoutInMilliseconds: timeoutFor(durationSec),
      browserExecutable,
      chromeMode,
    });

    // os.cpus() reads the host's core count, not the container's cgroup quota,
    // so on a boxed worker it sizes a tab count the container was never given
    // the CPU to actually run — the same mismatch PODCLI_WHISPER_THREADS
    // exists to correct for Whisper. PODCLI_REMOTION_CONCURRENCY lets deploy
    // config state the real number; unset, it falls back to the old guess for
    // anywhere still running bare-metal.
    const cpus = os.cpus().length;
    const concurrency = process.env.PODCLI_REMOTION_CONCURRENCY
      ? Math.max(1, parseInt(process.env.PODCLI_REMOTION_CONCURRENCY, 10))
      : Math.max(2, Math.min(cpus, 8));
    if (opts["keep-overlay"]) {
      const outBase = opts.output.replace(/\.[^.]+$/, "");
      captionOverlay = `${outBase}_captions.mov`;
    } else {
      const overlaySeed = `${path.resolve(opts.output)}:${process.pid}:${Date.now()}`;
      const overlayId = crypto.createHash("md5").update(overlaySeed).digest("hex").slice(0, 12);
      captionOverlay = path.join(os.tmpdir(), `remotion_overlay_${overlayId}.mov`);
    }

    let lastPct = -1;
    await renderMedia({
      composition: {
        ...composition,
        durationInFrames,
        fps,
        width: renderW,
        height: renderH,
      },
      serveUrl: bundleLocation,
      browserExecutable,
      chromeMode,
      codec: "prores",
      proResProfile: "4444",
      pixelFormat: "yuva444p10le",
      imageFormat: "png",
      outputLocation: captionOverlay,
      inputProps,
      concurrency,
      timeoutInMilliseconds: timeoutFor(durationSec),
      onProgress: ({ progress }) => {
        const pct = Math.round(progress * 100);
        if (pct > lastPct + 9) {
          lastPct = pct;
          process.stderr.write(`  captions: ${pct}%\n`);
        }
      },
    });

    // Composite: overlay transparent captions (ProRes 4444 with alpha) onto video
    //
    // PODCLI_FFMPEG, like every other module here. podcli provisions its own
    // ffmpeg and nothing guarantees one on PATH: a container that carries the
    // hermetic runtime and no system ffmpeg got "ffmpeg: not found" at this
    // one step, which reads as a failed Remotion render. Every clip on such a
    // box then fell back to burned-in ASS, looking nothing like the preview,
    // and the only word for it was on a stream nobody was reading.
    //
    // Argv rather than a shell string: these are paths from the caller, and
    // one with a space in it broke the quoting instead of the render.
    process.stderr.write("  compositing...\n");
    const ffmpeg = process.env.PODCLI_FFMPEG || "ffmpeg";
    const composite = spawnSync(
      ffmpeg,
      [
        "-y", "-hide_banner", "-loglevel", "warning",
        "-i", path.resolve(opts.video),
        "-i", captionOverlay,
        "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-map", "0:a", "-c:a", "copy",
        path.resolve(opts.output),
      ],
      { stdio: ["pipe", "pipe", "pipe"], timeout: 300000, encoding: "utf8" }
    );
    if (composite.error || composite.status !== 0) {
      const why = composite.error
        ? `${composite.error.code === "ENOENT" ? `${ffmpeg} not found` : composite.error.message}`
        : `exit ${composite.status}: ${(composite.stderr || "").trim().slice(-500)}`;
      throw new Error(`compositing the captions failed (${why})`);
    }

    if (opts["keep-overlay"]) {
      console.log(`PODCLI_OVERLAY_PATH=${captionOverlay}`);
    } else {
      try { fs.unlinkSync(captionOverlay); } catch {}
    }
    console.log(`Done: ${opts.output}`);
  } finally {
    closeAssetServer();
  }
}

main().catch((err) => {
  console.error("Remotion render error:", err.message);
  process.exit(1);
});
