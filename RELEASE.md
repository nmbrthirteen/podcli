# Releasing podcli

Distribution is fully automated by `.github/workflows/release.yml`: pushing a
`v*` tag builds the launcher per platform, builds whisper.cpp, bundles the studio
and Remotion, generates `checksums.txt`, and publishes a GitHub release. End users
install with no prerequisites:

```bash
curl -fsSL https://raw.githubusercontent.com/nmbrthirteen/podcli/main/install.sh | sh
# Windows: irm https://raw.githubusercontent.com/nmbrthirteen/podcli/main/install.ps1 | iex
```

Platforms: macOS arm64, Linux x64/arm64, Windows x64. (npm isn't used — the
unscoped name `podcli` is blocked by npm as too similar to `pod-cli`.)

## Cutting a release

1. Pick the version `X.Y.Z` and tag with it. The installers resolve the latest
   release automatically.
2. Merge to `main` and make sure CI is green.
3. Tag and push:
   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
4. Watch the `release` workflow. It produces these assets (the names the
   installers, self-update, and provisioner expect):
   - `podcli-{darwin,linux,windows}-{amd64,arm64}[.exe]` — static launchers
   - `whisper-cli-<os>-<arch>[.exe]`
   - `studio-bundle.tar.gz`
   - `remotion-<os>-<arch>.tar.gz`
   - `checksums.txt`
5. Verify the release is published with all assets, then run the smoke test below.

## Smoke test (ideally one machine per OS)

```bash
curl -fsSL .../install.sh | sh      # Windows: irm .../install.ps1 | iex
podcli doctor                        # paths + engine resolution
podcli process sample.mp4 --top 1    # transcribe -> suggest -> export a clip
podcli                               # interactive menu -> Open Web UI
```

First run downloads the hermetic runtimes (Python, Node, FFmpeg, model) once.
Needs outbound HTTPS to github.com, huggingface.co, nodejs.org, and the ffmpeg
hosts. glibc Linux only (Alpine/musl is unsupported).
