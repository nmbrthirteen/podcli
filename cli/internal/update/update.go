// Package update checks GitHub Releases for a newer podcli and (once releases
// publish per-platform binaries) applies it. For now a manual update points the
// user at their package manager, matching the npm/bun reinstall fallback.
package update

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"podcli/internal/config"
)

const repo = "nmbrthirteen/podcli"

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
	fmt.Printf("podcli %s available (you have %s).\n", tag, current)
	fmt.Println("Reinstall via your package manager:  npm i -g podcli   (or: bun add -g podcli)")
	return 0
}
