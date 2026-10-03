// Catches what a build step can't: things that should never reach a commit.
// Runs over `git ls-files` (tracked files only) so untracked local scratch
// (data/, podcli-clips/, .env) never trips it.
import { execSync } from "child_process";
import { readFileSync, statSync } from "fs";
import { join, dirname, extname } from "path";
import { fileURLToPath, pathToFileURL } from "url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

// Real source recordings/renders have no business in git history. They
// belong in data/ or podcli-clips/, both gitignored. Icons, logos, and other
// still-image UI assets are handled by the size check below instead, since
// those are legitimately tracked.
const MEDIA_EXTENSIONS = new Set([
  ".mp4", ".mov", ".mkv", ".avi", ".webm", ".wmv", ".flv",
  ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg",
]);

// Files over 1MB that are legitimately tracked. Keep this list short, add
// to it only when the file truly belongs in history (a model weight, a brand
// asset), not as a workaround for a one-off mistake.
const LARGE_FILE_ALLOWLIST = new Set([
  "backend/models/yamnet.onnx", // audio-energy model analyze_energy depends on
  "public/promo.gif", // README hero asset
  "remotion/public/style/detail-kraft.png", // riso pack texture
]);

const MAX_SIZE_BYTES = 1024 * 1024;

const SECRET_PATTERNS = [
  { name: "AWS access key", re: /\bAKIA[0-9A-Z]{16}\b/ },
  { name: "private key block", re: /-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----/ },
  { name: "GitHub token", re: /\bghp_[A-Za-z0-9]{36}\b/ },
  { name: "Slack token", re: /\bxox[baprs]-[A-Za-z0-9-]{10,}\b/ },
  { name: "OpenAI-style secret key", re: /\bsk-[A-Za-z0-9]{32,}\b/ },
];

// .env.example documents the shape of the file without real values, keep it.
const ENV_FILE_RE = /(^|\/)\.env(\..+)?$/;
const ENV_ALLOWLIST = new Set([".env.example"]);

// cwd is parameterized (default: this repo's root) so the test suite can
// point it at a throwaway repo instead of git ls-files-ing the real one.
export function trackedFiles(cwd = root) {
  // Plain `git ls-files` C-quotes any path with a non-ASCII byte (e.g.
  // "\346\226\207.mp4") instead of printing it raw, so a filename like that
  // never matches a real path on disk and every check below silently skips
  // it. `-z` prints paths NUL-separated with no quoting at all.
  return execSync("git ls-files -z", { cwd, encoding: "utf8" })
    .split("\0")
    .filter(Boolean);
}

export function checkMediaFiles(files) {
  return files
    .filter((f) => MEDIA_EXTENSIONS.has(extname(f).toLowerCase()))
    .map((f) => `tracked media file: ${f}`);
}

function checkLargeFiles(files) {
  const errors = [];
  for (const f of files) {
    if (LARGE_FILE_ALLOWLIST.has(f)) continue;
    let size;
    try {
      size = statSync(join(root, f)).size;
    } catch {
      continue; // deleted-but-staged path, or a submodule gitlink; not our concern here
    }
    if (size > MAX_SIZE_BYTES) {
      errors.push(`file over 1MB, not on the allowlist: ${f} (${(size / 1024 / 1024).toFixed(2)}MB)`);
    }
  }
  return errors;
}

export function checkEnvFiles(files) {
  return files
    .filter((f) => ENV_FILE_RE.test(f) && !ENV_ALLOWLIST.has(f))
    .map((f) => `tracked .env file: ${f}`);
}

// Binary files (images, the onnx model) throw on readFileSync(..., "utf8")
// often enough that it's simpler to just skip anything non-text-shaped.
const TEXT_EXTENSIONS = new Set([
  ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".go", ".sh",
  ".md", ".json", ".yml", ".yaml", ".toml", ".txt", ".html", ".css",
]);

export function matchSecretPatterns(content) {
  return SECRET_PATTERNS.filter(({ re }) => re.test(content)).map(({ name }) => name);
}

// Known fixture files that intentionally contain fake secrets shaped like
// the real thing, to test matchSecretPatterns itself. Everything else still
// gets scanned. A real secret pasted into some other test is still a leak.
const SECRET_SCAN_ALLOWLIST = new Set(["scripts/release-hygiene.test.mjs"]);

function checkSecretPatterns(files) {
  const errors = [];
  for (const f of files) {
    if (SECRET_SCAN_ALLOWLIST.has(f)) continue;
    if (!TEXT_EXTENSIONS.has(extname(f).toLowerCase())) continue;
    let content;
    try {
      content = readFileSync(join(root, f), "utf8");
    } catch {
      continue;
    }
    for (const name of matchSecretPatterns(content)) {
      errors.push(`possible ${name} in ${f}`);
    }
  }
  return errors;
}

// Guarded so src/release-hygiene.test.ts can import the pure check functions
// above without running the whole CLI (and without a stray process.exit).
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const files = trackedFiles();
  const errors = [
    ...checkMediaFiles(files),
    ...checkLargeFiles(files),
    ...checkEnvFiles(files),
    ...checkSecretPatterns(files),
  ];

  if (errors.length) {
    console.error("Release hygiene check failed:");
    for (const e of errors) console.error("  -", e);
    process.exit(1);
  }

  console.log(`Release hygiene check passed: ${files.length} tracked files checked.`);
}
