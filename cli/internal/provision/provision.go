// Package provision fetches pinned models into the global managed dir.
package provision

import (
	"archive/tar"
	"archive/zip"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
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
		fmt.Fprintf(os.Stderr, "\n  %s interrupted (attempt %d/%d): %v - resuming\n", label, attempt, maxAttempts, err)
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
		fmt.Fprintf(os.Stderr, "  (no pinned checksum for %s - skipped verification)\n", label)
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

// verifyDownload checks a downloaded archive against an upstream sha256sum-style
// manifest. Fails closed on a mismatch (removes the file); fails open with a
// warning when the manifest or its entry can't be fetched, so a transient
// network issue on the sums file doesn't block provisioning.
func verifyDownload(archive, sumsURL, name string) error {
	resp, err := downloadHTTPClient().Get(sumsURL)
	if err != nil {
		fmt.Fprintf(os.Stderr, "  (could not fetch checksums for %s - skipped verification)\n", name)
		return nil
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		fmt.Fprintf(os.Stderr, "  (no checksums for %s - skipped verification)\n", name)
		return nil
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if err != nil {
		return err
	}
	want := ParseChecksums(data)[name]
	if want == "" {
		fmt.Fprintf(os.Stderr, "  (no checksum entry for %s - skipped verification)\n", name)
		return nil
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
	resp, err := downloadHTTPClient().Do(req)
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

// symlinkTargetInside reports whether a symlink at linkPath pointing to linkname
// resolves within root (root has a trailing separator). Absolute targets and
// any path that escapes via .. are rejected — this is the symlink half of the
// zip-slip defense, since the path guard alone only validates the link's own
// location, not where it points.
func symlinkTargetInside(linkPath, linkname, root string) bool {
	if filepath.IsAbs(linkname) {
		return false
	}
	resolved := filepath.Clean(filepath.Join(filepath.Dir(linkPath), linkname))
	return strings.HasPrefix(resolved+string(os.PathSeparator), root)
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
	URL  string
	Bins []string
}

// Static ffmpeg sources are not yet pinned by checksum (upstream "latest" URLs).
// darwin/arm64 is absent on purpose: evermeet.cx only builds x86_64, so Apple
// Silicon is served by our own release asset (see releaseFFmpeg).
var ffmpegSpecs = map[string][]ffArchive{
	"darwin/amd64": {
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

const podcliRepo = "nmbrthirteen/podcli"

func WhisperCLIBin() string {
	return filepath.Join(paths.RuntimeDir(), "whisper", "whisper-cli"+paths.ExeSuffix())
}

func latestReleaseAssets() (map[string]string, error) {
	req, _ := http.NewRequest(http.MethodGet, "https://api.github.com/repos/"+podcliRepo+"/releases/latest", nil)
	req.Header.Set("Accept", "application/vnd.github+json")
	resp, err := releaseHTTPClient().Do(req)
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

func releaseHTTPClient() *http.Client {
	return &http.Client{
		Transport: &http.Transport{ResponseHeaderTimeout: 30 * time.Second},
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) >= 10 {
				return fmt.Errorf("too many redirects")
			}
			if !allowedReleaseHost(req.URL.Hostname()) {
				return fmt.Errorf("refusing redirect to untrusted host %q", req.URL.Hostname())
			}
			return nil
		},
	}
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
	return &http.Client{
		Transport: &http.Transport{ResponseHeaderTimeout: 60 * time.Second},
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) >= 10 {
				return fmt.Errorf("too many redirects")
			}
			if !allowedDownloadHost(req.URL.Hostname()) {
				return fmt.Errorf("refusing redirect to untrusted host %q", req.URL.Hostname())
			}
			return nil
		},
	}
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
// It fails closed on a real checksum mismatch, but fails open (with a warning)
// when checksums.txt or the asset's entry is absent — so a release published
// before checksum manifests, or a CI hiccup, never bricks an install.
func verifyReleaseAsset(assets map[string]string, assetName, path string) error {
	sumsURL, ok := assets["checksums.txt"]
	if !ok {
		fmt.Fprintf(os.Stderr, "  (no checksums.txt in release - skipped verification of %s)\n", assetName)
		return nil
	}
	data, err := httpGetBytes(sumsURL)
	if err != nil {
		fmt.Fprintf(os.Stderr, "  (could not fetch checksums.txt: %v - skipped verification of %s)\n", err, assetName)
		return nil
	}
	want, ok := ParseChecksums(data)[assetName]
	if !ok {
		fmt.Fprintf(os.Stderr, "  (no checksum entry for %s - skipped verification)\n", assetName)
		return nil
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
	if err := fetch(url, bin, "whisper-cli"); err != nil {
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
		if err := fetch(url, bin, tool); err != nil {
			return err
		}
		if err := verifyReleaseAsset(assets, name, bin); err != nil {
			return err
		}
		os.Chmod(bin, 0o755)
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
	req, _ := http.NewRequest(http.MethodGet, "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest", nil)
	req.Header.Set("Accept", "application/vnd.github+json")
	resp, err := releaseHTTPClient().Do(req)
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
		sum := sha256.Sum256([]byte(url))
		archive := filepath.Join(os.TempDir(), "podcli-py-"+hex.EncodeToString(sum[:8])+".tar.gz")
		if err := fetch(url, archive, "cpython"); err != nil {
			return "", err
		}
		if sumsURL != "" {
			if err := verifyDownload(archive, sumsURL, name); err != nil {
				return "", err
			}
		}
		err = extractTarGz(archive, paths.RuntimeDir())
		os.Remove(archive)
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
