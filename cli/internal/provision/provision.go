// Package provision fetches pinned models into the global managed dir.
package provision

import (
	"archive/zip"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"time"

	"podcli/internal/paths"
)

type model struct {
	URL    string
	SHA256 string // empty: verification skipped
}

var models = map[string]model{
	"base": {
		URL:    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin",
		SHA256: "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
	},
	"tiny.en": {
		URL: "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin",
	},
	"small": {
		URL: "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin",
	},
}

const vadURL = "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin"
const vadSHA = "29940d98d42b91fbd05ce489f3ecf7c72f0a42f027e4875919a28fb4c04ea2cf"

func ModelPath(size string) string {
	return filepath.Join(paths.ModelsDir(), "ggml-"+size+".bin")
}

func VADModelPath() string {
	return filepath.Join(paths.ModelsDir(), "ggml-silero-v5.1.2.bin")
}

func have(p string) bool {
	if fi, err := os.Stat(p); err == nil && fi.Size() > 0 {
		return true
	}
	return false
}

func EnsureModel(size string) (string, error) {
	dest := ModelPath(size)
	if have(dest) {
		return dest, nil
	}
	m, ok := models[size]
	if !ok {
		return "", fmt.Errorf("unknown model size %q (known: base, tiny.en, small)", size)
	}
	if err := download(m.URL, dest, m.SHA256, "ggml-"+size); err != nil {
		return "", err
	}
	return dest, nil
}

func EnsureVADModel() (string, error) {
	dest := VADModelPath()
	if have(dest) {
		return dest, nil
	}
	if err := download(vadURL, dest, vadSHA, "silero-vad"); err != nil {
		return "", err
	}
	return dest, nil
}

const maxAttempts = 6

// fetch resumes via HTTP Range across transient stalls rather than restarting,
// writing to dest atomically.
func fetch(url, dest, label string) error {
	if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
		return err
	}
	tmp := dest + ".part"
	var lastErr error
	for attempt := 1; attempt <= maxAttempts; attempt++ {
		done, err := downloadOnce(url, tmp, label)
		if err == nil && done {
			lastErr = nil
			break
		}
		lastErr = err
		fmt.Fprintf(os.Stderr, "\n  %s interrupted (attempt %d/%d): %v — resuming\n", label, attempt, maxAttempts, err)
		time.Sleep(time.Duration(attempt) * time.Second)
	}
	if lastErr != nil {
		os.Remove(tmp)
		return lastErr
	}
	return os.Rename(tmp, dest)
}

func download(url, dest, wantSHA, label string) error {
	if err := fetch(url, dest, label); err != nil {
		return err
	}
	if wantSHA == "" {
		fmt.Fprintf(os.Stderr, "  (no pinned checksum for %s — skipped verification)\n", label)
		return nil
	}
	got, err := sha256file(dest)
	if err != nil {
		return err
	}
	if got != wantSHA {
		os.Remove(dest)
		return fmt.Errorf("checksum mismatch for %s: got %s want %s", label, got, wantSHA)
	}
	return nil
}

func downloadOnce(url, tmp, label string) (bool, error) {
	var start int64
	if fi, err := os.Stat(tmp); err == nil {
		start = fi.Size()
	}

	req, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		return false, err
	}
	if start > 0 {
		req.Header.Set("Range", fmt.Sprintf("bytes=%d-", start))
	}
	client := &http.Client{Transport: &http.Transport{ResponseHeaderTimeout: 60 * time.Second}}
	resp, err := client.Do(req)
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()

	switch resp.StatusCode {
	case http.StatusRequestedRangeNotSatisfiable:
		return true, nil // already complete on disk
	case http.StatusOK:
		if start > 0 { // server ignored Range — restart cleanly
			os.Truncate(tmp, 0)
			start = 0
		}
	case http.StatusPartialContent:
	default:
		return false, fmt.Errorf("HTTP %d", resp.StatusCode)
	}

	out, err := os.OpenFile(tmp, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return false, err
	}
	defer out.Close()

	pw := &progress{label: label, total: start + resp.ContentLength, written: start}
	_, copyErr := io.Copy(io.MultiWriter(out, pw), resp.Body)
	if copyErr != nil {
		return false, copyErr
	}
	pw.done()
	return true, nil
}

func sha256file(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

type ffArchive struct {
	URL  string
	Bins []string
}

// Static ffmpeg sources are not yet pinned by checksum (upstream "latest" URLs);
// they get our own pinned builds once podcli hosts releases.
var ffmpegSpecs = map[string][]ffArchive{
	"darwin/amd64": {
		{URL: "https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip", Bins: []string{"ffmpeg"}},
		{URL: "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip", Bins: []string{"ffprobe"}},
	},
	"darwin/arm64": {
		{URL: "https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip", Bins: []string{"ffmpeg"}},
		{URL: "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip", Bins: []string{"ffprobe"}},
	},
	"linux/amd64": {
		{URL: "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz", Bins: []string{"ffmpeg", "ffprobe"}},
	},
	"linux/arm64": {
		{URL: "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-arm64-static.tar.xz", Bins: []string{"ffmpeg", "ffprobe"}},
	},
	"windows/amd64": {
		{URL: "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip", Bins: []string{"ffmpeg.exe", "ffprobe.exe"}},
	},
}

func exeSuffix() string {
	if runtime.GOOS == "windows" {
		return ".exe"
	}
	return ""
}

func FFmpegBin() string {
	return filepath.Join(paths.RuntimeDir(), "ffmpeg", "ffmpeg"+exeSuffix())
}

func EnsureFFmpeg() (string, error) {
	bin := FFmpegBin()
	if have(bin) {
		return bin, nil
	}
	specs, ok := ffmpegSpecs[runtime.GOOS+"/"+runtime.GOARCH]
	if !ok {
		return "", fmt.Errorf("no ffmpeg build for %s/%s", runtime.GOOS, runtime.GOARCH)
	}
	dir := filepath.Join(paths.RuntimeDir(), "ffmpeg")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	for _, a := range specs {
		sum := sha256.Sum256([]byte(a.URL))
		archive := filepath.Join(os.TempDir(), "podcli-ff-"+hex.EncodeToString(sum[:8]))
		if err := fetch(a.URL, archive, "ffmpeg-archive"); err != nil {
			return "", err
		}
		err := extractBins(archive, a.Bins, dir)
		os.Remove(archive)
		if err != nil {
			return "", err
		}
	}
	if !have(bin) {
		return "", fmt.Errorf("ffmpeg missing after extraction in %s", dir)
	}
	return bin, nil
}

func extractBins(archive string, bins []string, dest string) error {
	f, err := os.Open(archive)
	if err != nil {
		return err
	}
	magic := make([]byte, 6)
	io.ReadFull(f, magic)
	f.Close()
	switch {
	case magic[0] == 'P' && magic[1] == 'K':
		return extractZip(archive, bins, dest)
	case magic[0] == 0xFD && string(magic[1:6]) == "7zXZ\x00":
		return extractTarXz(archive, bins, dest)
	default:
		return fmt.Errorf("unrecognized archive format")
	}
}

func wantSet(bins []string) map[string]bool {
	m := make(map[string]bool, len(bins))
	for _, b := range bins {
		m[b] = true
	}
	return m
}

func extractZip(archive string, bins []string, dest string) error {
	zr, err := zip.OpenReader(archive)
	if err != nil {
		return err
	}
	defer zr.Close()
	want := wantSet(bins)
	for _, zf := range zr.File {
		if zf.FileInfo().IsDir() || !want[filepath.Base(zf.Name)] {
			continue
		}
		rc, err := zf.Open()
		if err != nil {
			return err
		}
		err = writeBin(rc, filepath.Join(dest, filepath.Base(zf.Name)))
		rc.Close()
		if err != nil {
			return err
		}
	}
	return nil
}

func extractTarXz(archive string, bins []string, dest string) error {
	tmp, err := os.MkdirTemp("", "podcli-ffx-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(tmp)
	cmd := exec.Command("tar", "-xf", archive, "-C", tmp)
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("tar extract (is tar installed?): %w", err)
	}
	want := wantSet(bins)
	return filepath.WalkDir(tmp, func(p string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() || !want[filepath.Base(p)] {
			return err
		}
		in, err := os.Open(p)
		if err != nil {
			return err
		}
		defer in.Close()
		return writeBin(in, filepath.Join(dest, filepath.Base(p)))
	})
}

func writeBin(r io.Reader, dest string) error {
	out, err := os.OpenFile(dest, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o755)
	if err != nil {
		return err
	}
	defer out.Close()
	_, err = io.Copy(out, r)
	return err
}

type progress struct {
	label    string
	total    int64
	written  int64
	lastPct  int
	lastTick time.Time
}

func (p *progress) Write(b []byte) (int, error) {
	n := len(b)
	p.written += int64(n)
	now := time.Now()
	if now.Sub(p.lastTick) < 200*time.Millisecond {
		return n, nil
	}
	p.lastTick = now
	if p.total > 0 {
		pct := int(p.written * 100 / p.total)
		if pct != p.lastPct {
			p.lastPct = pct
			fmt.Fprintf(os.Stderr, "\r  fetching %s ... %d%% (%d/%d MB)", p.label, pct, p.written>>20, p.total>>20)
		}
	} else {
		fmt.Fprintf(os.Stderr, "\r  fetching %s ... %d MB", p.label, p.written>>20)
	}
	return n, nil
}

func (p *progress) done() {
	fmt.Fprintf(os.Stderr, "\r  fetching %s ... done (%d MB)%s\n", p.label, p.written>>20, "          ")
}
