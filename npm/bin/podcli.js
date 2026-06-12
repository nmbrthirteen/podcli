#!/usr/bin/env node
'use strict';

const fs = require('fs');
const { spawnSync } = require('child_process');
const { binPath, ensure } = require('../scripts/install');

(async () => {
  let bin = binPath();
  if (!fs.existsSync(bin)) {
    try {
      bin = await ensure();
    } catch (e) {
      console.error('podcli: failed to fetch native binary:', e.message);
      process.exit(1);
    }
  }
  const r = spawnSync(bin, process.argv.slice(2), { stdio: 'inherit' });
  if (r.error) {
    console.error('podcli:', r.error.message);
    process.exit(1);
  }
  process.exit(r.status == null ? 1 : r.status);
})();
