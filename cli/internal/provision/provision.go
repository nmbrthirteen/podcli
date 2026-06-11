// Package provision fetches pinned models into the global managed dir.
package provision

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
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

// download resumes via HTTP Range across transient stalls rather than
// restarting, then verifies the pinned checksum and renames atomically.
func download(url, dest, wantSHA, label string) error {
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

	if wantSHA != "" {
		got, err := sha256file(tmp)
		if err != nil {
			return err
		}
		if got != wantSHA {
			os.Remove(tmp)
			return fmt.Errorf("checksum mismatch for %s: got %s want %s", label, got, wantSHA)
		}
	} else {
		fmt.Fprintf(os.Stderr, "  (no pinned checksum for %s — skipped verification)\n", label)
	}
	return os.Rename(tmp, dest)
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
