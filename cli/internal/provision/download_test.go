package provision

import (
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestDownloadRequiresPinnedChecksum(t *testing.T) {
	err := download("https://example.invalid/x", filepath.Join(t.TempDir(), "x"), "", "x")
	if err == nil || !strings.Contains(err.Error(), "no pinned checksum") {
		t.Fatalf("download with empty checksum should hard-error, got %v", err)
	}
}

func TestAllModelsPinned(t *testing.T) {
	for size, m := range models {
		if len(m.SHA256) != 64 {
			t.Errorf("model %s has no pinned sha256", size)
		}
	}
}

func TestFFmpegSpecsPinnedAndVersioned(t *testing.T) {
	for platform, specs := range ffmpegSpecs {
		for _, a := range specs {
			if len(a.SHA256) != 64 {
				t.Errorf("%s: %s has no pinned sha256", platform, a.URL)
			}
			for _, mutable := range []string{"latest", "getrelease", "ffmpeg-release-"} {
				if strings.Contains(a.URL, mutable) {
					t.Errorf("%s: %s is a mutable URL (%q)", platform, a.URL, mutable)
				}
			}
		}
	}
}

func TestVerifyDownloadFailsClosed(t *testing.T) {
	archive := filepath.Join(t.TempDir(), "a.bin")
	if err := os.WriteFile(archive, []byte("payload"), 0o644); err != nil {
		t.Fatal(err)
	}

	missing := httptest.NewServer(http.NotFoundHandler())
	defer missing.Close()
	if err := verifyDownload(archive, missing.URL, "a.bin"); err == nil {
		t.Fatal("missing checksums manifest should fail closed")
	}

	noEntry := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintln(w, "abc123  other.bin")
	}))
	defer noEntry.Close()
	if err := verifyDownload(archive, noEntry.URL, "a.bin"); err == nil {
		t.Fatal("missing checksum entry should fail closed")
	}
}

func TestVerifyReleaseAssetFailsClosedWithoutManifest(t *testing.T) {
	err := verifyReleaseAsset(map[string]string{}, "whisper-cli-linux-amd64", "/nonexistent")
	if err == nil || !strings.Contains(err.Error(), "checksums.txt") {
		t.Fatalf("release without checksums.txt should fail closed, got %v", err)
	}
}

func TestAcquireLockReleaseAndStaleTakeover(t *testing.T) {
	dest := filepath.Join(t.TempDir(), "artifact")
	unlock, err := acquireLock(dest)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(dest + ".lock"); err != nil {
		t.Fatalf("lock file missing while held: %v", err)
	}
	unlock()
	if _, err := os.Stat(dest + ".lock"); !os.IsNotExist(err) {
		t.Fatalf("lock file should be gone after release, got %v", err)
	}

	if err := os.WriteFile(dest+".lock", []byte("999999"), 0o644); err != nil {
		t.Fatal(err)
	}
	stale := time.Now().Add(-2 * time.Hour)
	if err := os.Chtimes(dest+".lock", stale, stale); err != nil {
		t.Fatal(err)
	}
	unlock, err = acquireLock(dest)
	if err != nil {
		t.Fatalf("stale lock should be taken over, got %v", err)
	}
	unlock()
}

func TestDownloadOnceSendsIfRangeAndRestartsOnChange(t *testing.T) {
	var gotIfRange, gotRange string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotIfRange = r.Header.Get("If-Range")
		gotRange = r.Header.Get("Range")
		w.Header().Set("ETag", `"v2"`)
		w.WriteHeader(http.StatusOK)
		io.WriteString(w, "FRESH")
	}))
	defer srv.Close()

	tmp := filepath.Join(t.TempDir(), "f.part")
	if err := os.WriteFile(tmp, []byte("OLDDATA"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(validatorPath(tmp), []byte(`"v1"`), 0o644); err != nil {
		t.Fatal(err)
	}

	done, err := downloadOnce(srv.URL, tmp, "test", downloadHTTPClient())
	if err != nil || !done {
		t.Fatalf("downloadOnce = %v, %v", done, err)
	}
	if gotRange == "" {
		t.Fatal("resume should send Range")
	}
	if gotIfRange != `"v1"` {
		t.Fatalf("If-Range = %q, want stored validator", gotIfRange)
	}
	b, _ := os.ReadFile(tmp)
	if string(b) != "FRESH" {
		t.Fatalf("full response should truncate stale part file, got %q", b)
	}
	v, _ := os.ReadFile(validatorPath(tmp))
	if string(v) != `"v2"` {
		t.Fatalf("validator = %q, want new ETag", v)
	}
}

func TestStallGuardAbortsIdleBody(t *testing.T) {
	pr, pw := io.Pipe()
	defer pw.Close()
	g := newStallGuard(pr, 50*time.Millisecond)
	defer g.stop()

	_, err := g.Read(make([]byte, 1))
	if err == nil || !strings.Contains(err.Error(), "stalled") {
		t.Fatalf("idle body should surface a stall error, got %v", err)
	}
}

func TestGithubAPIGetRateLimitAndToken(t *testing.T) {
	limited := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
	}))
	defer limited.Close()
	_, err := githubAPIGet(limited.URL)
	if err == nil || !strings.Contains(err.Error(), "GITHUB_TOKEN") {
		t.Fatalf("403 should report the rate limit and suggest GITHUB_TOKEN, got %v", err)
	}

	var auth string
	ok := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		auth = r.Header.Get("Authorization")
		io.WriteString(w, "{}")
	}))
	defer ok.Close()
	t.Setenv("GITHUB_TOKEN", "tok123")
	resp, err := githubAPIGet(ok.URL)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if auth != "Bearer tok123" {
		t.Fatalf("Authorization = %q, want bearer token", auth)
	}
}
