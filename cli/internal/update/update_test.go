package update

import (
	"bytes"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"podcli/internal/config"
	"podcli/internal/provision"
)

func TestSwapReplacesBinary(t *testing.T) {
	dir := t.TempDir()
	dest := filepath.Join(dir, "podcli")
	if err := os.WriteFile(dest, []byte("OLD"), 0o755); err != nil {
		t.Fatal(err)
	}
	staged := dest + ".new"
	if err := os.WriteFile(staged, []byte("NEW"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := swap(staged, dest); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(dest)
	if string(b) != "NEW" {
		t.Fatalf("swap left %q, want NEW", b)
	}
}

func TestNewer(t *testing.T) {
	cases := []struct {
		remote, current string
		want            bool
	}{
		{"2.0.1", "2.0.0", true},
		{"2.0.0", "2.0.0", false},
		{"1.9.9", "2.0.0", false},
		{"2.1.0", "2.0.9", true},
		{"v2.0.1", "2.0.0", true},
		{"3.0.0", "2.9.9", true},
	}
	for _, c := range cases {
		if got := newer(c.remote, c.current); got != c.want {
			t.Errorf("newer(%q, %q) = %v, want %v", c.remote, c.current, got, c.want)
		}
	}
}

func TestUpdateCheckCache(t *testing.T) {
	t.Setenv("PODCLI_HOME", t.TempDir())
	if _, ok := config.CachedUpdateCheck(24 * time.Hour); ok {
		t.Fatal("fresh config should have no cached check")
	}
	config.RecordUpdateCheck("9.9.9")
	tag, ok := config.CachedUpdateCheck(24 * time.Hour)
	if !ok || tag != "9.9.9" {
		t.Fatalf("cached check = %q, %v; want 9.9.9 within 24h", tag, ok)
	}
	if _, ok := config.CachedUpdateCheck(0); ok {
		t.Fatal("expired cache should force a re-check")
	}
}

func TestSelfUpdateFailureOmitsPackageManagerFallback(t *testing.T) {
	var out bytes.Buffer
	printSelfUpdateFailure(&out, errors.New("boom"))

	got := out.String()
	for _, unwanted := range []string{"npm", "bun", "package manager"} {
		if strings.Contains(got, unwanted) {
			t.Fatalf("failure output contains %q:\n%s", unwanted, got)
		}
	}
	if !strings.Contains(got, "Your installed podcli was left unchanged.") {
		t.Fatalf("failure output should say the install is unchanged:\n%s", got)
	}
}

// The provisioning allowlist also trusts the model and ffmpeg CDNs; a release
// asset URL that redirects to one of them must still be refused for the binary.
func TestDownloadFileRefusesRedirectOffGitHub(t *testing.T) {
	for _, host := range []string{"https://evermeet.cx/payload", "https://huggingface.co/payload"} {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, host, http.StatusFound)
		}))
		dest := filepath.Join(t.TempDir(), "podcli.new")
		err := downloadFile(srv.URL, dest)
		srv.Close()

		if !errors.Is(err, provision.ErrUntrustedRedirect) {
			t.Fatalf("redirect to %s should be refused, got %v", host, err)
		}
		if _, statErr := os.Stat(dest); !os.IsNotExist(statErr) {
			t.Fatalf("refused download left a staged binary behind: %v", statErr)
		}
	}
}

func TestDownloadFailureSuggestsRetry(t *testing.T) {
	var out bytes.Buffer
	printSelfUpdateFailure(&out, &phaseError{phase: phaseDownload, err: errors.New("network down")})

	got := out.String()
	if !strings.Contains(got, "Download failed.") {
		t.Fatalf("download failure output should identify the download failure:\n%s", got)
	}
	if strings.Contains(got, "latest release binary manually") {
		t.Fatalf("download failure output should not suggest manual install first:\n%s", got)
	}
}
