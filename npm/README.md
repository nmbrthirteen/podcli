# podcli

AI podcast clip generator — transcribe, find viral moments, and export vertical
short-form clips with burned captions.

```sh
npm i -g podcli      # or: bun add -g podcli
podcli setup         # one-time: provisions a self-contained runtime
podcli process episode.mp4 --top 5
```

Installing this package downloads the native `podcli` binary for your platform
into a managed directory; the `podcli` command is a thin shim that runs it. The
binary is self-contained — `podcli setup` provisions its own Python, ffmpeg,
whisper.cpp, and models, so there is nothing else to install.

Updates: `podcli update` checks for a newer release. Disable auto-update checks
with `podcli config set update.auto off` (or `PODCLI_NO_UPDATE=1`).
