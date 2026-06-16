# Releasing podcli

Distribution is fully automated by `.github/workflows/release.yml`: pushing a
`v*` tag builds the launcher for all five platforms, builds whisper.cpp, bundles
the studio and Remotion, generates `checksums.txt`, publishes a GitHub release,
and publishes the npm package. End users then install with no prerequisites:

```bash
curl -fsSL https://raw.githubusercontent.com/nmbrthirteen/podcli/main/install.sh | sh
# or: npm install -g podcli   (Windows: install.ps1)
```

## One-time setup

- Set the **`NPM_TOKEN`** repository secret (an npm automation token with publish
  rights). Without it the GitHub release still publishes; the npm job fails and
  can be re-run after the secret is added.

## Cutting a release

1. Pick the version `X.Y.Z`. Set it in **`npm/package.json`** (`install.js` fetches
   the binary from `releases/download/vX.Y.Z/`, so the npm version must equal the
   tag). Optionally align the root `package.json`.
2. Merge to `main` and make sure CI is green.
3. Tag and push:
   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
4. Watch the `release` workflow. It produces these assets (the names the npm
   wrapper, self-update, and provisioner expect):
   - `podcli-{darwin,linux,windows}-{amd64,arm64}[.exe]` — static launchers
   - `whisper-cli-<os>-<arch>[.exe]`
   - `studio-bundle.tar.gz`
   - `remotion-<os>-<arch>.tar.gz`
   - `checksums.txt`
5. Verify the npm publish succeeded (`npm view podcli version`).

## Smoke test (ideally one machine per OS)

```bash
curl -fsSL .../install.sh | sh      # or npm i -g podcli
podcli doctor                        # paths + engine resolution
podcli process sample.mp4 --top 1    # transcribe -> suggest -> export a clip
podcli                               # interactive menu -> Open Web UI
```

First run downloads the hermetic runtimes (Python, Node, FFmpeg, model) once.
Needs outbound HTTPS to github.com, huggingface.co, nodejs.org, and the ffmpeg
hosts. glibc Linux only (Alpine/musl is unsupported).
