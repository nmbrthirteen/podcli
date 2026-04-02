#!/usr/bin/env node

const path = require("node:path");
const rendererRoot = path.resolve(__dirname, "..", "..", "node_modules", "@remotion", "renderer", "dist");
const {openBrowser} = require("@remotion/renderer");
const {screenshot} = require(path.join(rendererRoot, "puppeteer-screenshot.js"));

const [htmlPath, outputPath, widthArg, heightArg, waitMsArg] = process.argv.slice(2);

if (!htmlPath || !outputPath || !widthArg || !heightArg) {
  console.error(
    "Usage: remotion_screenshot.cjs <html_path> <output_path> <width> <height> [wait_ms]",
  );
  process.exit(1);
}

const width = Number(widthArg);
const height = Number(heightArg);
const waitMs = Number(waitMsArg || "1500");

const waitForAssets = async (page) => {
  await page
    .evaluate(async () => {
      const images = Array.from(document.images || []);
      await Promise.all(
        images.map((img) => {
          if (img.complete) {
            return Promise.resolve();
          }

          return new Promise((resolve) => {
            img.addEventListener("load", resolve, {once: true});
            img.addEventListener("error", resolve, {once: true});
          });
        }),
      );

      if (document.fonts?.ready) {
        try {
          await document.fonts.ready;
        } catch {
        }
      }
    })
    .catch(() => undefined);
};

(async () => {
  const browser = await openBrowser("chrome", {logLevel: "error"});
  try {
    const page = await browser.newPage({
      context: () => null,
      logLevel: "error",
      indent: false,
      pageIndex: 0,
      onBrowserLog: () => undefined,
      onLog: () => undefined,
    });
    await page.setViewport({width, height, deviceScaleFactor: 1});
    await page.goto({
      url: `file://${path.resolve(htmlPath)}`,
      timeout: Math.max(30000, waitMs + 10000),
      options: {
        waitUntil: "load",
      },
    });
    await waitForAssets(page);
    if (waitMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, waitMs));
    }
    await screenshot({
      page,
      path: outputPath,
      type: "png",
      width,
      height,
      scale: 1,
    });
    await page.close();
  } finally {
    await browser.close({silent: true});
  }
})().catch((err) => {
  console.error(err && (err.stack || err.message) ? err.stack || err.message : String(err));
  process.exit(1);
});
