// Package provision fetches pinned models into the global managed dir.
package provision

import (
	"archive/tar"
	"archive/zip"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"podcli/internal/paths"
)

type model struct {
	URL    string
	SHA256 string
}

var models = map[string]model{
	"base": {
		URL:    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin",
		SHA256: "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
	},
	"tiny.en": {
		URL:    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin",
		SHA256: "921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f",
	},
	"small": {
		URL:    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin",
		SHA256: "1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b",
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

// Fetch downloads url into dest with resume, progress, and stall detection.
// Exported so self-update reuses the same download path.
func Fetch(url, dest, label string) error { return fetch(url, dest, label, downloadHTTPClient()) }

// FetchGuarded is Fetch with a caller-supplied redirect allowlist. Self-update
// passes its GitHub-only allowlist: provisioning also trusts the model and
// ffmpeg CDNs, and the podcli binary must not be redirectable to any of them.
func FetchGuarded(url, dest, label string, allowHost func(string) bool) error {
	return fetch(url, dest, label, guardedHTTPClient(allowHost, 60*time.Second))
}

// fetch resumes via HTTP Range across transient stalls rather than restarting,
// writing to dest atomically. A per-destination lock file serializes concurrent
// podcli processes so they don't append to the same .part file.
func fetch(url, dest, label string, client *http.Client) error {
	if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
		return err
	}
	unlock, err := acquireLock(dest)
	if err != nil {
		return err
	}
	defer unlock()
	tmp := dest + ".part"
	if have(dest) {
		// Another process finished it while we waited; its .part is gone, but a
		// resume file from an earlier interrupted attempt may still sit here.
		os.Remove(tmp)
		os.Remove(validatorPath(tmp))
		return nil
	}
	var lastErr error
	for attempt := 1; attempt <= maxAttempts; attempt++ {
		done, err := downloadOnce(url, tmp, label, client)
		if err == nil && done {
			lastErr = nil
			break
		}
		if errors.Is(err, ErrUntrustedRedirect) {
			os.Remove(tmp)
			os.Remove(validatorPath(tmp))
			return err
		}
		lastErr = err
		fmt.Fprintf(os.Stderr, "\n  %s interrupted (attempt %d/%d): %v - resuming\n", label, attempt, maxAttempts, err)
		time.Sleep(time.Duration(attempt) * time.Second)
	}
	if lastErr != nil {
		os.Remove(tmp)
		os.Remove(validatorPath(tmp))
		return lastErr
	}
	os.Remove(validatorPath(tmp))
	return os.Rename(tmp, dest)
}

// downloadPath places transient archives inside the managed dir rather than the
// world-shared temp dir, where a predictable name could be pre-planted by
// another local user. The dir persists across runs, so name derives from the
// source URL: a completed archive is only reused for the exact release it came
// from, never for the next one published under the same asset name.
func downloadPath(url, name string) (string, error) {
	dir := filepath.Join(paths.RuntimeDir(), "downloads")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	sum := sha256.Sum256([]byte(url))
	return filepath.Join(dir, "podcli-"+hex.EncodeToString(sum[:8])+"-"+name), nil
}

// removeArchive drops an extracted archive and the sidecars fetch leaves beside
// it. The downloads dir is persistent, so anything left there outlives the
// release it belongs to.
func removeArchive(path string) {
	os.Remove(path)
	os.Remove(path + ".part")
	os.Remove(validatorPath(path + ".part"))
}

var (
	lockHeartbeat = 20 * time.Second
	lockStale     = 2 * time.Minute
	lockPoll      = time.Second
	lockWait      = 30 * time.Minute
)

// acquireLock serializes provisioning of one artifact across processes via an
// O_EXCL sentinel. The holder heartbeats the lock while it works, so a healthy
// but slow download (ggml-small.bin on a thin link) is never mistaken for a
// crashed one and stolen mid-flight.
func acquireLock(dest string) (func(), error) {
	lock := dest + ".lock"
	deadline := time.Now().Add(lockWait)
	for {
		f, err := os.OpenFile(lock, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o644)
		if err == nil {
			fmt.Fprintf(f, "%d", os.Getpid())
			f.Close()
			return holdLock(lock), nil
		}
		if !os.IsExist(err) {
			return nil, err
		}
		if dropStaleLock(lock) {
			continue
		}
		if time.Now().After(deadline) {
			return nil, fmt.Errorf("timed out waiting for %s (another podcli may be provisioning; delete the file if not)", lock)
		}
		time.Sleep(lockPoll)
	}
}

// holdLock keeps the lock's mtime fresh until the returned unlock runs.
func holdLock(lock string) func() {
	done := make(chan struct{})
	go func() {
		t := time.NewTicker(lockHeartbeat)
		defer t.Stop()
		for {
			select {
			case <-t.C:
				now := time.Now()
				os.Chtimes(lock, now, now)
			case <-done:
				return
			}
		}
	}()
	var once sync.Once
	return func() {
		once.Do(func() {
			close(done)
			os.Remove(lock)
		})
	}
}

// dropStaleLock removes a lock whose holder stopped heartbeating, reporting
// whether it did. Removal is itself serialized by an O_EXCL takeover token:
// otherwise two processes seeing the same stale lock both remove and recreate
// it, the second deleting the first's fresh lock, and both then append to the
// same .part file.
func dropStaleLock(lock string) bool {
	token := lock + ".takeover"
	if fi, err := os.Stat(token); err == nil && time.Since(fi.ModTime()) > lockStale {
		os.Remove(token) // a process died mid-takeover; retry the takeover next pass
		return false
	}
	f, err := os.OpenFile(token, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o644)
	if err != nil {
		return false // another process is taking over; wait for its lock instead
	}
	f.Close()
	defer os.Remove(token)
	fi, err := os.Stat(lock)
	if err != nil || time.Since(fi.ModTime()) <= lockStale {
		return false
	}
	return os.Remove(lock) == nil
}

func download(url, dest, wantSHA, label string) error {
	if wantSHA == "" {
		return fmt.Errorf("no pinned checksum for %s - refusing unverified download", label)
	}
	if err := fetch(url, dest, label, downloadHTTPClient()); err != nil {
		return err
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

// verifyDownload checks a downloaded archive against an upstream sha256sum-style
// manifest. Fails closed: a manifest that can't be fetched or has no entry for
// the asset means the download cannot be trusted.
func verifyDownload(archive, sumsURL, name string) error {
	resp, err := downloadHTTPClient().Get(sumsURL)
	if err != nil {
		return fmt.Errorf("cannot verify %s: fetching checksums failed: %w", name, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("cannot verify %s: HTTP %d fetching checksums", name, resp.StatusCode)
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if err != nil {
		return err
	}
	want := ParseChecksums(data)[name]
	if want == "" {
		return fmt.Errorf("cannot verify %s: no entry in upstream checksums", name)
	}
	got, err := sha256file(archive)
	if err != nil {
		return err
	}
	if got != want {
		os.Remove(archive)
		return fmt.Errorf("checksum mismatch for %s: got %s want %s", name, got, want)
	}
	return nil
}

func validatorPath(tmp string) string { return tmp + ".validator" }

func downloadOnce(url, tmp, label string, client *http.Client) (bool, error) {
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
		// If-Range makes the server ignore Range when the file changed upstream,
		// so a resumed .part can't end up stitched from two different builds.
		if v, err := os.ReadFile(validatorPath(tmp)); err == nil && len(v) > 0 {
			req.Header.Set("If-Range", strings.TrimSpace(string(v)))
		}
	}
	resp, err := client.Do(req)
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()

	switch resp.StatusCode {
	case http.StatusRequestedRangeNotSatisfiable:
		return true, nil // already complete on disk
	case http.StatusOK:
		if start > 0 { // server ignored Range or the file changed — restart cleanly
			os.Truncate(tmp, 0)
			start = 0
		}
		if v := resp.Header.Get("ETag"); v != "" {
			os.WriteFile(validatorPath(tmp), []byte(v), 0o644)
		} else if v := resp.Header.Get("Last-Modified"); v != "" {
			os.WriteFile(validatorPath(tmp), []byte(v), 0o644)
		} else {
			os.Remove(validatorPath(tmp))
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

	body := newStallGuard(resp.Body, 60*time.Second)
	defer body.stop()
	pw := &progress{label: label, total: start + resp.ContentLength, written: start}
	_, copyErr := io.Copy(io.MultiWriter(out, pw), body)
	if copyErr != nil {
		return false, copyErr
	}
	pw.done()
	return true, nil
}

// stallGuard aborts a body read that stops delivering bytes: the timer closes
// the connection, which surfaces here as a stall error and triggers the retry
// loop in fetch.
type stallGuard struct {
	body    io.ReadCloser
	timeout time.Duration
	timer   *time.Timer
	stalled atomic.Bool
}

func newStallGuard(body io.ReadCloser, timeout time.Duration) *stallGuard {
	g := &stallGuard{body: body, timeout: timeout}
	g.timer = time.AfterFunc(timeout, func() {
		g.stalled.Store(true)
		body.Close()
	})
	return g
}

func (g *stallGuard) Read(p []byte) (int, error) {
	n, err := g.body.Read(p)
	if n > 0 {
		g.timer.Reset(g.timeout)
	}
	if err != nil && err != io.EOF && g.stalled.Load() {
		return n, fmt.Errorf("stalled: no data for %s", g.timeout)
	}
	return n, err
}

func (g *stallGuard) stop() { g.timer.Stop() }

// symlinkTargetInside reports whether a symlink at linkPath pointing to linkname
// resolves within root (root has a trailing separator). Absolute targets and
// any path that escapes via .. are rejected — this is the symlink half of the
// zip-slip defense, since the path guard alone only validates the link's own
// location, not where it points.
func symlinkTargetInside(linkPath, linkname, root string) bool {
	if rootedAnywhere(linkname) {
		return false
	}
	resolved := filepath.Clean(filepath.Join(filepath.Dir(linkPath), linkname))
	return strings.HasPrefix(resolved+string(os.PathSeparator), root)
}

// rootedAnywhere reports whether p is rooted on any host, not merely this one.
// Archive members carry POSIX paths while filepath.IsAbs answers for the running
// OS: on Windows it calls "/etc/passwd" relative, so a rooted symlink target
// would pass the guard and then resolve against the drive root.
func rootedAnywhere(p string) bool {
	if p == "" {
		return false
	}
	if filepath.IsAbs(p) || p[0] == '/' || p[0] == '\\' {
		return true
	}
	// "C:/x", and drive-relative "C:x", both leave the destination on Windows.
	if len(p) >= 2 && p[1] == ':' {
		c := p[0] | 0x20
		return c >= 'a' && c <= 'z'
	}
	return false
}

// ParseChecksums parses sha256sum-style "<hex>  <name>" lines into a name->hash
// map, keyed by basename so it matches the asset filenames consumers request.
func ParseChecksums(data []byte) map[string]string {
	out := map[string]string{}
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 2 {
			continue
		}
		out[filepath.Base(fields[len(fields)-1])] = strings.ToLower(fields[0])
	}
	return out
}

// Sha256File returns the lowercase hex SHA-256 of the file at path.
func Sha256File(path string) (string, error) { return sha256file(path) }

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
	URL    string
	SHA256 string
	Bins   []string
}

// Pinned, versioned upstream archives, verified by SHA-256 before extraction.
// darwin/arm64 is absent on purpose: evermeet.cx only builds x86_64, so Apple
// Silicon is served by our own release asset (see releaseFFmpeg).
var ffmpegSpecs = map[string][]ffArchive{
	"darwin/amd64": {
		{
			URL:    "https://evermeet.cx/ffmpeg/ffmpeg-8.1.2.zip",
			SHA256: "e91df72a1ee7c26606f90dd2dd4dcccc6a75140ff9ea6fdd50faae828b82ba69",
			Bins:   []string{"ffmpeg"},
		},
		{
			URL:    "https://evermeet.cx/ffmpeg/ffprobe-8.1.2.zip",
			SHA256: "399b93f0b9862f69767afa343e90c2f48d7e7958cadbb6deb76a012d0e3b7ce3",
			Bins:   []string{"ffprobe"},
		},
	},
	"linux/amd64": {
		{
			URL:    "https://johnvansickle.com/ffmpeg/old-releases/ffmpeg-6.0.1-amd64-static.tar.xz",
			SHA256: "28268bf402f1083833ea269331587f60a242848880073be8016501d864bd07a5",
			Bins:   []string{"ffmpeg", "ffprobe"},
		},
	},
	"linux/arm64": {
		{
			URL:    "https://johnvansickle.com/ffmpeg/old-releases/ffmpeg-6.0.1-arm64-static.tar.xz",
			SHA256: "7dbd8e2f47bd83de591b9d6ea70e67d32d9aa97e7d47ae402b60c2fe3fd4d0ab",
			Bins:   []string{"ffmpeg", "ffprobe"},
		},
	},
	"windows/amd64": {
		{
			URL:    "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-07-12-13-16/ffmpeg-n8.1.2-22-g94138f6973-win64-gpl-8.1.zip",
			SHA256: "cb11f1a2628555d1ce3de984ae1dd488a26d85092d23219c574de76efbe2a62e",
			Bins:   []string{"ffmpeg.exe", "ffprobe.exe"},
		},
	},
}

const podcliRepo = "nmbrthirteen/podcli"

func WhisperCLIBin() string {
	return filepath.Join(paths.RuntimeDir(), "whisper", "whisper-cli"+paths.ExeSuffix())
}

// githubAPIGet fetches a GitHub API URL, authenticating with GITHUB_TOKEN when
// set so provisioning doesn't burn the 60/hour unauthenticated quota.
func githubAPIGet(url string) (*http.Response, error) {
	req, _ := http.NewRequest(http.MethodGet, url, nil)
	req.Header.Set("Accept", "application/vnd.github+json")
	if tok := os.Getenv("GITHUB_TOKEN"); tok != "" {
		req.Header.Set("Authorization", "Bearer "+tok)
	}
	resp, err := releaseHTTPClient().Do(req)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode == http.StatusForbidden || resp.StatusCode == http.StatusTooManyRequests {
		resp.Body.Close()
		return nil, fmt.Errorf("GitHub rate limit (HTTP %d) - retry later or set GITHUB_TOKEN", resp.StatusCode)
	}
	return resp, nil
}

var releaseAssetsOnce sync.Once
var releaseAssetsMap map[string]string
var releaseAssetsErr error

// latestReleaseAssets is fetched once per process: setup asks for it per
// artifact, and unauthenticated GitHub API calls are rate limited.
func latestReleaseAssets() (map[string]string, error) {
	releaseAssetsOnce.Do(func() {
		releaseAssetsMap, releaseAssetsErr = fetchLatestReleaseAssets()
	})
	return releaseAssetsMap, releaseAssetsErr
}

func fetchLatestReleaseAssets() (map[string]string, error) {
	resp, err := githubAPIGet("https://api.github.com/repos/" + podcliRepo + "/releases/latest")
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("no published release (HTTP %d)", resp.StatusCode)
	}
	var rel struct {
		Assets []struct {
			Name string `json:"name"`
			URL  string `json:"browser_download_url"`
		} `json:"assets"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&rel); err != nil {
		return nil, err
	}
	out := make(map[string]string, len(rel.Assets))
	for _, a := range rel.Assets {
		out[a.Name] = a.URL
	}
	return out, nil
}

func latestReleaseAssetURL(name string) (string, error) {
	assets, err := latestReleaseAssets()
	if err != nil {
		return "", err
	}
	if u, ok := assets[name]; ok {
		return u, nil
	}
	return "", fmt.Errorf("asset %s not in latest release", name)
}

// allowedReleaseHost pins binary downloads to GitHub's own hosts so a redirect
// can't divert a download to an attacker-controlled host.
func allowedReleaseHost(h string) bool {
	h = strings.ToLower(h)
	switch h {
	case "github.com", "api.github.com", "objects.githubusercontent.com", "codeload.github.com":
		return true
	}
	return strings.HasSuffix(h, ".githubusercontent.com")
}

// ErrUntrustedRedirect marks a download diverted off its allowlist. Callers that
// wrap Fetch in a retry loop must not treat it as a transient failure.
var ErrUntrustedRedirect = errors.New("refusing redirect to untrusted host")

func guardedHTTPClient(allowHost func(string) bool, headerTimeout time.Duration) *http.Client {
	return &http.Client{
		Transport: &http.Transport{
			Proxy:                 http.ProxyFromEnvironment,
			ResponseHeaderTimeout: headerTimeout,
		},
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) >= 10 {
				return fmt.Errorf("too many redirects")
			}
			if !allowHost(req.URL.Hostname()) {
				return fmt.Errorf("%w %q", ErrUntrustedRedirect, req.URL.Hostname())
			}
			return nil
		},
	}
}

func releaseHTTPClient() *http.Client {
	return guardedHTTPClient(allowedReleaseHost, 30*time.Second)
}

// allowedDownloadHost pins large-binary/model downloads to their known source
// hosts and CDNs. Several of these payloads (Node, CPython, ffmpeg) are not
// checksum-verified, so blocking redirects to unknown hosts is the main defense
// against a diverted download. Initial request URLs are hardcoded (trusted);
// this only constrains where a redirect may land.
func allowedDownloadHost(h string) bool {
	h = strings.ToLower(h)
	for _, base := range []string{
		"github.com",            // release assets
		"githubusercontent.com", // objects.* / release-assets.* CDN
		"huggingface.co",        // whisper.cpp models + cdn-lfs*.huggingface.co
		"hf.co",                 // HF CDN redirects: us.aws.cdn.hf.co, cas-bridge.xethub.hf.co
		"nodejs.org",            // hermetic Node
		"evermeet.cx",           // macOS ffmpeg
		"johnvansickle.com",     // linux ffmpeg
	} {
		if h == base || strings.HasSuffix(h, "."+base) {
			return true
		}
	}
	return false
}

func downloadHTTPClient() *http.Client {
	return guardedHTTPClient(allowedDownloadHost, 60*time.Second)
}

func httpGetBytes(url string) ([]byte, error) {
	resp, err := releaseHTTPClient().Get(url)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HTTP %d for %s", resp.StatusCode, url)
	}
	return io.ReadAll(io.LimitReader(resp.Body, 1<<20))
}

// verifyReleaseAsset checks the file at path against the release's checksums.txt.
// Fails closed: checksums.txt is published with every release, so a missing
// manifest, a failed fetch, or a missing entry all mean the asset can't be
// trusted.
func verifyReleaseAsset(assets map[string]string, assetName, path string) error {
	sumsURL, ok := assets["checksums.txt"]
	if !ok {
		return fmt.Errorf("cannot verify %s: release has no checksums.txt (it is published with every release - retry later)", assetName)
	}
	data, err := httpGetBytes(sumsURL)
	if err != nil {
		return fmt.Errorf("cannot verify %s: fetching checksums.txt failed: %w", assetName, err)
	}
	want, ok := ParseChecksums(data)[assetName]
	if !ok {
		return fmt.Errorf("cannot verify %s: no entry in checksums.txt", assetName)
	}
	got, err := sha256file(path)
	if err != nil {
		return err
	}
	if !strings.EqualFold(got, want) {
		os.Remove(path)
		return fmt.Errorf("checksum mismatch for %s: got %s want %s", assetName, got, want)
	}
	return nil
}

func EnsureWhisperCpp() (string, error) {
	bin := WhisperCLIBin()
	if nativeBin(bin) {
		return bin, nil
	}
	os.Remove(bin)
	name := fmt.Sprintf("whisper-cli-%s-%s%s", runtime.GOOS, runtime.GOARCH, paths.ExeSuffix())
	assets, err := latestReleaseAssets()
	if err != nil {
		return "", err
	}
	url, ok := assets[name]
	if !ok {
		return "", fmt.Errorf("asset %s not in latest release", name)
	}
	if err := os.MkdirAll(filepath.Dir(bin), 0o755); err != nil {
		return "", err
	}
	if err := fetch(url, bin, "whisper-cli", downloadHTTPClient()); err != nil {
		return "", err
	}
	if err := verifyReleaseAsset(assets, name, bin); err != nil {
		return "", err
	}
	if runtime.GOOS != "windows" {
		os.Chmod(bin, 0o755)
	}
	return bin, nil
}

func FFmpegBin() string {
	return filepath.Join(paths.RuntimeDir(), "ffmpeg", "ffmpeg"+paths.ExeSuffix())
}

func FFprobeBin() string {
	return filepath.Join(paths.RuntimeDir(), "ffmpeg", "ffprobe"+paths.ExeSuffix())
}

func EnsureFFmpeg() (string, error) {
	bin := FFmpegBin()
	if nativeBin(bin) && nativeBin(FFprobeBin()) {
		return bin, nil
	}
	dir := filepath.Join(paths.RuntimeDir(), "ffmpeg")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	if runtime.GOOS == "darwin" && runtime.GOARCH == "arm64" {
		return bin, releaseFFmpeg()
	}
	specs, ok := ffmpegSpecs[runtime.GOOS+"/"+runtime.GOARCH]
	if !ok {
		return "", fmt.Errorf("no ffmpeg build for %s/%s", runtime.GOOS, runtime.GOARCH)
	}
	for _, a := range specs {
		archive, err := downloadPath(a.URL, "ffmpeg-archive")
		if err != nil {
			return "", err
		}
		if err := download(a.URL, archive, a.SHA256, "ffmpeg-archive"); err != nil {
			return "", err
		}
		err = extractBins(archive, a.Bins, dir)
		removeArchive(archive)
		if err != nil {
			return "", err
		}
	}
	if !have(bin) {
		return "", fmt.Errorf("ffmpeg missing after extraction in %s", dir)
	}
	return bin, nil
}

// Apple Silicon builds ship with the release, checksum-verified like whisper-cli.
// No upstream publishes an arm64 macOS static, so ffmpeg used to arrive as an
// x86_64 binary that ran under Rosetta at roughly a third of native encode speed.
func releaseFFmpeg() error {
	assets, err := latestReleaseAssets()
	if err != nil {
		return err
	}
	for tool, bin := range map[string]string{"ffmpeg": FFmpegBin(), "ffprobe": FFprobeBin()} {
		name := fmt.Sprintf("%s-%s-%s", tool, runtime.GOOS, runtime.GOARCH)
		url, ok := assets[name]
		if !ok {
			return fmt.Errorf("asset %s not in latest release", name)
		}
		os.Remove(bin)
		if err := fetch(url, bin, tool, downloadHTTPClient()); err != nil {
			return err
		}
		if err := verifyReleaseAsset(assets, name, bin); err != nil {
			return err
		}
		if err := os.Chmod(bin, 0o755); err != nil {
			return fmt.Errorf("chmod %s: %w", bin, err)
		}
	}
	return nil
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
	listing, err := exec.Command("tar", "-tf", archive).Output()
	if err != nil {
		return fmt.Errorf("tar list (is tar installed?): %w", err)
	}
	for _, name := range strings.Split(string(listing), "\n") {
		name = strings.TrimSpace(name)
		if name == "" {
			continue
		}
		clean := filepath.Clean(name)
		if filepath.IsAbs(clean) || clean == ".." || strings.HasPrefix(clean, ".."+string(os.PathSeparator)) {
			return fmt.Errorf("refusing archive with unsafe path: %q", name)
		}
	}
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

var pyTriples = map[string]string{
	"darwin/amd64":  "x86_64-apple-darwin",
	"darwin/arm64":  "aarch64-apple-darwin",
	"linux/amd64":   "x86_64-unknown-linux-gnu",
	"linux/arm64":   "aarch64-unknown-linux-gnu",
	"windows/amd64": "x86_64-pc-windows-msvc",
}

func PythonBin() string {
	if runtime.GOOS == "windows" {
		return filepath.Join(paths.RuntimeDir(), "python", "python.exe")
	}
	return filepath.Join(paths.RuntimeDir(), "python", "bin", "python3")
}

func pythonHealthy(bin string) bool {
	if !nativeBin(bin) {
		return false
	}
	root := pythonRoot(bin)
	if runtime.GOOS == "windows" {
		return have(filepath.Join(root, "Lib", "encodings", "__init__.py"))
	}
	matches, err := filepath.Glob(filepath.Join(root, "lib", "python*", "encodings", "__init__.py"))
	return err == nil && len(matches) > 0
}

func pythonRoot(bin string) string {
	if runtime.GOOS == "windows" {
		return filepath.Dir(bin)
	}
	return filepath.Dir(filepath.Dir(bin))
}

// pythonAssetURL resolves a python-build-standalone install_only tarball for
// this platform via the GitHub latest-release API, so it tracks upstream
// without a hardcoded version that rots.
func pythonAssetURL() (url, name, sumsURL string, err error) {
	triple, ok := pyTriples[runtime.GOOS+"/"+runtime.GOARCH]
	if !ok {
		return "", "", "", fmt.Errorf("no python build for %s/%s", runtime.GOOS, runtime.GOARCH)
	}
	resp, err := githubAPIGet("https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest")
	if err != nil {
		return "", "", "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", "", "", fmt.Errorf("github api: HTTP %d", resp.StatusCode)
	}
	var rel struct {
		Assets []struct {
			Name string `json:"name"`
			URL  string `json:"browser_download_url"`
		} `json:"assets"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&rel); err != nil {
		return "", "", "", err
	}
	sums := ""
	for _, a := range rel.Assets {
		if a.Name == "SHA256SUMS" {
			sums = a.URL
			break
		}
	}
	match := func(prefer string) (string, string) {
		for _, a := range rel.Assets {
			if strings.Contains(a.Name, triple) && strings.HasSuffix(a.Name, "install_only.tar.gz") && strings.Contains(a.Name, prefer) {
				return a.URL, a.Name
			}
		}
		return "", ""
	}
	if u, n := match("cpython-3.12."); u != "" {
		return u, n, sums, nil
	}
	if u, n := match("cpython-3."); u != "" {
		return u, n, sums, nil
	}
	return "", "", "", fmt.Errorf("no install_only python asset for %s", triple)
}

func EnsurePython(requirements string) (string, error) {
	bin := PythonBin()
	if !pythonHealthy(bin) {
		root := pythonRoot(bin)
		if err := os.RemoveAll(root); err != nil {
			return "", fmt.Errorf("remove corrupted Python runtime %s: %w (close any running podcli or Python subprocesses)", root, err)
		}
		url, name, sumsURL, err := pythonAssetURL()
		if err != nil {
			return "", err
		}
		archive, err := downloadPath(url, "cpython.tar.gz")
		if err != nil {
			return "", err
		}
		if err := fetch(url, archive, "cpython", downloadHTTPClient()); err != nil {
			return "", err
		}
		if sumsURL == "" {
			return "", fmt.Errorf("cannot verify %s: release has no SHA256SUMS asset", name)
		}
		if err := verifyDownload(archive, sumsURL, name); err != nil {
			return "", err
		}
		err = extractTarGz(archive, paths.RuntimeDir())
		removeArchive(archive)
		if err != nil {
			return "", err
		}
		if !pythonHealthy(bin) {
			return "", fmt.Errorf("python runtime missing stdlib encodings after extraction")
		}
	}
	if requirements != "" {
		if err := ensureDeps(bin, requirements); err != nil {
			return "", err
		}
	}
	return bin, nil
}

func ensureDeps(pybin, requirements string) error {
	sum, err := sha256file(requirements)
	if err == nil && sum != "" && depsInstalled(pybin, sum) {
		return nil
	}
	if err := pipInstall(pybin, requirements); err != nil {
		return err
	}
	if sum != "" {
		os.WriteFile(depsStamp(pybin), []byte(sum), 0o644)
	}
	return nil
}

func depsStamp(pybin string) string {
	return filepath.Join(pythonRoot(pybin), ".podcli-deps")
}

func depsInstalled(pybin, sum string) bool {
	b, err := os.ReadFile(depsStamp(pybin))
	return err == nil && strings.TrimSpace(string(b)) == sum
}

func pipInstall(pybin, requirements string) error {
	if err := ensurePip(pybin); err != nil {
		return err
	}
	fmt.Fprintf(os.Stderr, "  installing python deps (%s) - pulls ~80MB, first run takes a minute\n", filepath.Base(requirements))
	cmd := exec.Command(pybin, "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar=on", "-r", requirements)
	cmd.Stdout, cmd.Stderr = os.Stderr, os.Stderr
	cmd.Env = append(os.Environ(), "PYTHONUNBUFFERED=1")
	return cmd.Run()
}

func ensurePip(pybin string) error {
	if exec.Command(pybin, "-m", "pip", "--version").Run() == nil {
		return nil
	}
	cmd := exec.Command(pybin, "-m", "ensurepip", "--upgrade")
	cmd.Stdout, cmd.Stderr = os.Stderr, os.Stderr
	return cmd.Run()
}

// EnsureSpeakerDeps installs the speaker-diarization stack (pyannote.audio pulls
// torch) into the hermetic Python. Opt-in because it's a large download (~2GB)
// most users don't need.
func EnsureSpeakerDeps() error {
	bin := PythonBin()
	if !have(bin) {
		return fmt.Errorf("python not provisioned - run `podcli setup` first")
	}
	fmt.Fprintln(os.Stderr, "  installing speaker deps (pyannote.audio + torch) - large download (~2GB), several minutes")
	cmd := exec.Command(bin, "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar=on", "pyannote.audio>=3.1.0", "speechbrain")
	cmd.Stdout, cmd.Stderr = os.Stderr, os.Stderr
	cmd.Env = append(os.Environ(), "PYTHONUNBUFFERED=1")
	return cmd.Run()
}

func extractTarGz(archive, dest string) error {
	f, err := os.Open(archive)
	if err != nil {
		return err
	}
	defer f.Close()
	gz, err := gzip.NewReader(f)
	if err != nil {
		return err
	}
	defer gz.Close()
	tr := tar.NewReader(gz)
	root := filepath.Clean(dest) + string(os.PathSeparator)
	for {
		h, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return err
		}
		target := filepath.Join(dest, h.Name)
		// Allow the archive's own root entry ("./" from `tar -C dir .`), which
		// resolves to dest itself; reject only paths that escape dest.
		if target != filepath.Clean(dest) && !strings.HasPrefix(target, root) {
			return fmt.Errorf("unsafe path in archive: %s", h.Name)
		}
		switch h.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
		case tar.TypeReg:
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			out, err := os.OpenFile(target, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, os.FileMode(h.Mode))
			if err != nil {
				return err
			}
			_, err = io.Copy(out, tr)
			out.Close()
			if err != nil {
				return err
			}
		case tar.TypeSymlink:
			if !symlinkTargetInside(target, h.Linkname, root) {
				return fmt.Errorf("unsafe symlink %s -> %s in archive", h.Name, h.Linkname)
			}
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			os.Remove(target)
			if err := os.Symlink(h.Linkname, target); err != nil {
				return err
			}
		case tar.TypeLink:
			linkTarget := filepath.Join(dest, h.Linkname)
			if !strings.HasPrefix(linkTarget, root) {
				return fmt.Errorf("unsafe hardlink %s -> %s in archive", h.Name, h.Linkname)
			}
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			os.Remove(target)
			if err := os.Link(linkTarget, target); err != nil {
				return err
			}
		}
	}
	return nil
}

// stderrIsTTY gates \r-style in-place progress: piped to a file or CI log, the
// carriage returns would pile up as one unreadable line, so non-TTY output gets
// periodic full lines instead.
var stderrIsTTY = func() bool {
	fi, err := os.Stderr.Stat()
	return err == nil && fi.Mode()&os.ModeCharDevice != 0
}()

type progress struct {
	label    string
	total    int64
	written  int64
	lastPct  int
	lastTick time.Time
	lastLine time.Time
}

func (p *progress) Write(b []byte) (int, error) {
	n := len(b)
	p.written += int64(n)
	now := time.Now()
	if !stderrIsTTY {
		if now.Sub(p.lastLine) >= 5*time.Second {
			p.lastLine = now
			if p.total > 0 {
				fmt.Fprintf(os.Stderr, "  fetching %s ... %d%% (%d/%d MB)\n", p.label, int(p.written*100/p.total), p.written>>20, p.total>>20)
			} else {
				fmt.Fprintf(os.Stderr, "  fetching %s ... %d MB\n", p.label, p.written>>20)
			}
		}
		return n, nil
	}
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
	if !stderrIsTTY {
		fmt.Fprintf(os.Stderr, "  fetching %s ... done (%d MB)\n", p.label, p.written>>20)
		return
	}
	fmt.Fprintf(os.Stderr, "\r  fetching %s ... done (%d MB)%s\n", p.label, p.written>>20, "          ")
}
