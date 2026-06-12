'use strict';

// Fetches the native podcli binary for this platform into the managed dir. Runs
// on npm/bun postinstall, and again lazily from the bin shim if the binary is
// missing. PODCLI_BINARY_SRC=<path> copies a local binary instead of downloading
// (used for testing and from the release build before publishing).

const os = require('os');
const fs = require('fs');
const path = require('path');
const https = require('https');
const { version } = require('../package.json');

const REPO = 'nmbrthirteen/podcli';

const TARGETS = {
  'darwin-x64': 'darwin-amd64',
  'darwin-arm64': 'darwin-arm64',
  'linux-x64': 'linux-amd64',
  'linux-arm64': 'linux-arm64',
  'win32-x64': 'windows-amd64',
};

function defaultHome() {
  const h = os.homedir();
  if (process.platform === 'darwin') return path.join(h, 'Library', 'Application Support', 'podcli');
  if (process.platform === 'win32') return process.env.LOCALAPPDATA || path.join(h, 'AppData', 'Local', 'podcli');
  return process.env.XDG_DATA_HOME ? path.join(process.env.XDG_DATA_HOME, 'podcli') : path.join(h, '.local', 'share', 'podcli');
}

function binPath() {
  const home = process.env.PODCLI_HOME || defaultHome();
  return path.join(home, 'bin', process.platform === 'win32' ? 'podcli.exe' : 'podcli');
}

function target() {
  const key = `${process.platform}-${process.arch}`;
  const t = TARGETS[key];
  if (!t) throw new Error(`unsupported platform ${key}`);
  return t;
}

function download(url, dest, redirects) {
  redirects = redirects || 0;
  return new Promise((resolve, reject) => {
    https
      .get(url, { headers: { 'User-Agent': 'podcli-install' } }, (res) => {
        if ([301, 302, 307, 308].includes(res.statusCode) && res.headers.location && redirects < 6) {
          res.resume();
          return resolve(download(res.headers.location, dest, redirects + 1));
        }
        if (res.statusCode !== 200) {
          res.resume();
          return reject(new Error(`HTTP ${res.statusCode} for ${url}`));
        }
        const tmp = dest + '.part';
        const file = fs.createWriteStream(tmp);
        res.pipe(file);
        file.on('finish', () => file.close(() => {
          fs.renameSync(tmp, dest);
          resolve();
        }));
        file.on('error', reject);
      })
      .on('error', reject);
  });
}

async function ensure() {
  const dest = binPath();
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  const src = process.env.PODCLI_BINARY_SRC;
  if (src) {
    fs.copyFileSync(src, dest);
  } else {
    const ext = process.platform === 'win32' ? '.exe' : '';
    const url = `https://github.com/${REPO}/releases/download/v${version}/podcli-${target()}${ext}`;
    await download(url, dest);
  }
  if (process.platform !== 'win32') fs.chmodSync(dest, 0o755);
  return dest;
}

module.exports = { binPath, ensure };

if (require.main === module) {
  ensure()
    .then((d) => console.log(`podcli binary ready: ${d}`))
    .catch((e) => {
      // Don't hard-fail the install — the bin shim fetches it on first run.
      console.error(`podcli: could not pre-fetch binary (${e.message}); it will be fetched on first run.`);
      process.exit(0);
    });
}
