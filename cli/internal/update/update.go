// Package update checks GitHub Releases for a newer podcli and applies the
// release binary for this platform.
package update

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"

	"podcli/internal/config"
	"podcli/internal/paths"
	"podcli/internal/provision"
)

const repo = "nmbrthirteen/podcli"

type updatePhase string

const (
	phaseDownload updatePhase = "download"
	phaseVerify   updatePhase = "verify"
	phaseInstall  updatePhase = "install"
)

type phaseError struct {
	phase updatePhase
	err   error
}

func (e *phaseError) Error() string {
	return e.err.Error()
}

func (e *phaseError) Unwrap() error {
	return e.err
}

func exeExt() string {
	if runtime.GOOS == "windows" {
		return ".exe"
	}
	return ""
}

// managedBin is the binary direct installs exec, so replacing it updates podcli.
func managedBin() string {
	return filepath.Join(paths.BinDir(), "podcli"+exeExt())
}

func assetName() string {
	return fmt.Sprintf("podcli-%s-%s%s", runtime.GOOS, runtime.GOARCH, exeExt())
}

func assetURL(tag string) string {
	return fmt.Sprintf("https://github.com/%s/releases/download/v%s/%s", repo, tag, assetName())
}

func checksumsURL(tag string) string {
	return fmt.Sprintf("https://github.com/%s/releases/download/v%s/checksums.txt", repo, tag)
}

func allowedHost(h string) bool {
	h = strings.ToLower(h)
	switch h {
	case "github.com", "api.github.com", "objects.githubusercontent.com", "codeload.github.com":
		return true
	}
	return strings.HasSuffix(h, ".githubusercontent.com")
}

func guardedClient() *http.Client {
	return &http.Client{
		Transport: &http.Transport{ResponseHeaderTimeout: 30 * time.Second},
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) >= 10 {
				return fmt.Errorf("too many redirects")
			}
			if !allowedHost(req.URL.Hostname()) {
				return fmt.Errorf("refusing redirect to untrusted host %q", req.URL.Hostname())
			}
			return nil
		},
	}
}

func latestTag(timeout time.Duration) (string, error) {
	client := &http.Client{Timeout: timeout}
	req, _ := http.NewRequest(http.MethodGet, "https://api.github.com/repos/"+repo+"/releases/latest", nil)
	req.Header.Set("Accept", "application/vnd.github+json")
	resp, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("no published release (HTTP %d)", resp.StatusCode)
	}
	var rel struct {
		Tag string `json:"tag_name"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&rel); err != nil {
		return "", err
	}
	return strings.TrimPrefix(rel.Tag, "v"), nil
}

func parseVer(v string) [3]int {
	v = strings.TrimPrefix(v, "v")
	v = strings.SplitN(v, "-", 2)[0] // drop -dev / pre-release
	var out [3]int
	for i, p := range strings.SplitN(v, ".", 3) {
		out[i], _ = strconv.Atoi(p)
	}
	return out
}

func newer(remote, current string) bool {
	r, c := parseVer(remote), parseVer(current)
	for i := 0; i < 3; i++ {
		if r[i] != c[i] {
			return r[i] > c[i]
		}
	}
	return false
}

// NotifyIfOutdated prints a one-line notice when a newer release exists. Fast,
// silent on any error, and respects the off-switch.
func NotifyIfOutdated(current string) {
	if !config.AutoUpdate() {
		return
	}
	tag, err := latestTag(1500 * time.Millisecond)
	if err != nil {
		return
	}
	if newer(tag, current) {
		fmt.Fprintf(os.Stderr, "  podcli %s available (you have %s) - run `podcli update`\n", tag, current)
	}
}

func Run(current string) int {
	tag, err := latestTag(10 * time.Second)
	if err != nil {
		fmt.Fprintf(os.Stderr, "podcli: update check failed: %v\n", err)
		return 1
	}
	if !newer(tag, current) {
		fmt.Printf("podcli %s is up to date.\n", current)
		return 0
	}
	fmt.Printf("Updating podcli %s -> %s ...\n", current, tag)
	if err := apply(tag); err != nil {
		printSelfUpdateFailure(os.Stderr, err)
		return 1
	}
	fmt.Printf("Updated to podcli %s.\n", tag)
	return 0
}

func printSelfUpdateFailure(w io.Writer, err error) {
	fmt.Fprintf(w, "podcli: self-update failed (%v).\n", err)
	fmt.Fprintln(w, "Your installed podcli was left unchanged.")

	if phaseOf(err) == phaseDownload {
		fmt.Fprintln(w, "Download failed. Check your network connection, then run `podcli update` again.")
		return
	}

	fmt.Fprintln(w, "Run `podcli update` again. If it keeps failing, install the latest release binary manually.")
}

func phaseOf(err error) updatePhase {
	var pe *phaseError
	if errors.As(err, &pe) {
		return pe.phase
	}
	return ""
}

// apply downloads the release binary for this platform and swaps the managed
// binary atomically.
func apply(tag string) error {
	dest := managedBin()
	if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
		return &phaseError{phase: phaseInstall, err: err}
	}
	staged := dest + ".new"
	if err := downloadFile(assetURL(tag), staged); err != nil {
		return &phaseError{phase: phaseDownload, err: err}
	}
	if err := verifyStaged(tag, staged); err != nil {
		os.Remove(staged)
		return &phaseError{phase: phaseVerify, err: err}
	}
	if runtime.GOOS != "windows" {
		if err := os.Chmod(staged, 0o755); err != nil {
			return &phaseError{phase: phaseInstall, err: err}
		}
	}
	if err := swap(staged, dest); err != nil {
		return &phaseError{phase: phaseInstall, err: err}
	}
	return nil
}

// verifyStaged checks the downloaded binary against the release's checksums.txt.
// Fails closed on a mismatch; fails open (warning) only when checksums.txt is
// absent, so an older release without a manifest still updates.
func verifyStaged(tag, staged string) error {
	resp, err := guardedClient().Get(checksumsURL(tag))
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNotFound {
		fmt.Fprintln(os.Stderr, "  (no checksums.txt in release - skipped verification)")
		return nil
	}
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d fetching checksums.txt", resp.StatusCode)
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return err
	}
	want, ok := provision.ParseChecksums(data)[assetName()]
	if !ok {
		fmt.Fprintf(os.Stderr, "  (no checksum entry for %s - skipped verification)\n", assetName())
		return nil
	}
	got, err := provision.Sha256File(staged)
	if err != nil {
		return err
	}
	if !strings.EqualFold(got, want) {
		return fmt.Errorf("checksum mismatch: got %s want %s", got, want)
	}
	return nil
}

// swap replaces dest with staged. On Windows a running .exe can't be overwritten,
// so the old binary is moved aside first; on Unix the rename is atomic.
func swap(staged, dest string) error {
	if runtime.GOOS == "windows" {
		old := dest + ".old"
		os.Remove(old)
		moved := false
		if _, err := os.Stat(dest); err == nil {
			if err := os.Rename(dest, old); err != nil {
				return err
			}
			moved = true
		}
		if err := os.Rename(staged, dest); err != nil {
			if moved {
				os.Rename(old, dest) // restore the original so the CLI isn't bricked
			}
			return err
		}
		return nil
	}
	return os.Rename(staged, dest)
}

func downloadFile(url, dest string) error {
	resp, err := guardedClient().Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d for %s", resp.StatusCode, url)
	}
	tmp := dest + ".part"
	f, err := os.Create(tmp)
	if err != nil {
		return err
	}
	_, err = io.Copy(f, resp.Body)
	f.Close()
	if err != nil {
		os.Remove(tmp)
		return err
	}
	return os.Rename(tmp, dest)
}
