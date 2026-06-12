// Package update checks GitHub Releases for a newer podcli and (once releases
// publish per-platform binaries) applies it. For now a manual update points the
// user at their package manager, matching the npm/bun reinstall fallback.
package update

import (
	"encoding/json"
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
)

const repo = "nmbrthirteen/podcli"

func exeExt() string {
	if runtime.GOOS == "windows" {
		return ".exe"
	}
	return ""
}

// managedBin is the binary the npm shim and direct installs both exec, so
// replacing it updates podcli regardless of how it was installed.
func managedBin() string {
	return filepath.Join(paths.BinDir(), "podcli"+exeExt())
}

func assetURL(tag string) string {
	return fmt.Sprintf("https://github.com/%s/releases/download/v%s/podcli-%s-%s%s",
		repo, tag, runtime.GOOS, runtime.GOARCH, exeExt())
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
		fmt.Fprintf(os.Stderr, "  ↑ podcli %s available (you have %s) — run `podcli update`\n", tag, current)
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
	fmt.Printf("Updating podcli %s → %s ...\n", current, tag)
	if err := apply(tag); err != nil {
		fmt.Fprintf(os.Stderr, "podcli: self-update failed (%v).\n", err)
		fmt.Fprintln(os.Stderr, "Reinstall via your package manager:  npm i -g podcli   (or: bun add -g podcli)")
		return 1
	}
	fmt.Printf("Updated to podcli %s.\n", tag)
	return 0
}

// apply downloads the release binary for this platform and swaps the managed
// binary atomically.
func apply(tag string) error {
	dest := managedBin()
	if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
		return err
	}
	staged := dest + ".new"
	if err := downloadFile(assetURL(tag), staged); err != nil {
		return err
	}
	if runtime.GOOS != "windows" {
		if err := os.Chmod(staged, 0o755); err != nil {
			return err
		}
	}
	return swap(staged, dest)
}

// swap replaces dest with staged. On Windows a running .exe can't be overwritten,
// so the old binary is moved aside first; on Unix the rename is atomic.
func swap(staged, dest string) error {
	if runtime.GOOS == "windows" {
		old := dest + ".old"
		os.Remove(old)
		if _, err := os.Stat(dest); err == nil {
			if err := os.Rename(dest, old); err != nil {
				return err
			}
		}
	}
	return os.Rename(staged, dest)
}

func downloadFile(url, dest string, redirects ...int) error {
	depth := 0
	if len(redirects) > 0 {
		depth = redirects[0]
	}
	resp, err := http.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if loc := resp.Header.Get("Location"); loc != "" && resp.StatusCode/100 == 3 && depth < 6 {
		return downloadFile(loc, dest, depth+1)
	}
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
