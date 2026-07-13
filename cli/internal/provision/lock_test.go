package provision

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func shortLockTimings(t *testing.T, wait time.Duration) {
	heartbeat, stale, poll, prevWait := lockHeartbeat, lockStale, lockPoll, lockWait
	t.Cleanup(func() { lockHeartbeat, lockStale, lockPoll, lockWait = heartbeat, stale, poll, prevWait })
	lockHeartbeat, lockStale, lockPoll, lockWait = 10*time.Millisecond, 100*time.Millisecond, 5*time.Millisecond, wait
}

func TestHeldLockIsNotStolenAfterStaleWindow(t *testing.T) {
	shortLockTimings(t, 200*time.Millisecond)
	dest := filepath.Join(t.TempDir(), "artifact")
	lock := dest + ".lock"

	unlock, err := acquireLock(dest)
	if err != nil {
		t.Fatal(err)
	}
	time.Sleep(5 * lockStale)

	if dropStaleLock(lock) {
		t.Fatal("a heartbeated lock was treated as abandoned")
	}
	if _, err := os.Stat(lock); err != nil {
		t.Fatalf("lock file gone while held: %v", err)
	}
	if _, err := acquireLock(dest); err == nil || !strings.Contains(err.Error(), "timed out") {
		t.Fatalf("second process should wait out a live lock, got %v", err)
	}

	unlock()
	unlock2, err := acquireLock(dest)
	if err != nil {
		t.Fatalf("lock should be free after release, got %v", err)
	}
	unlock2()
}

func TestStaleLockTakeoverIsExclusive(t *testing.T) {
	shortLockTimings(t, 5*time.Second)
	dest := filepath.Join(t.TempDir(), "artifact")
	lock := dest + ".lock"
	if err := os.WriteFile(lock, []byte("999999"), 0o644); err != nil {
		t.Fatal(err)
	}
	stale := time.Now().Add(-time.Hour)
	if err := os.Chtimes(lock, stale, stale); err != nil {
		t.Fatal(err)
	}

	var live, maxLive, failures atomic.Int32
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			unlock, err := acquireLock(dest)
			if err != nil {
				failures.Add(1)
				return
			}
			defer unlock()
			n := live.Add(1)
			for {
				m := maxLive.Load()
				if n <= m || maxLive.CompareAndSwap(m, n) {
					break
				}
			}
			time.Sleep(20 * time.Millisecond)
			live.Add(-1)
		}()
	}
	wg.Wait()

	if got := maxLive.Load(); got != 1 {
		t.Fatalf("%d processes held the lock at once; the stale takeover is not exclusive", got)
	}
	if got := failures.Load(); got != 0 {
		t.Fatalf("%d waiters never acquired the lock", got)
	}
	if _, err := os.Stat(lock); !os.IsNotExist(err) {
		t.Fatalf("lock file should be gone after the last release, got %v", err)
	}
	if _, err := os.Stat(lock + ".takeover"); !os.IsNotExist(err) {
		t.Fatalf("takeover token should not outlive the takeover, got %v", err)
	}
}

func TestDownloadPathIsUniquePerURL(t *testing.T) {
	t.Setenv("PODCLI_HOME", t.TempDir())
	const name = "studio-bundle.tar.gz"
	v1, err := downloadPath("https://github.com/o/r/releases/download/v2.4.7/"+name, name)
	if err != nil {
		t.Fatal(err)
	}
	v2, err := downloadPath("https://github.com/o/r/releases/download/v2.4.8/"+name, name)
	if err != nil {
		t.Fatal(err)
	}
	if v1 == v2 {
		t.Fatal("an archive cached for one release must not be reused for the next")
	}
	if !strings.HasSuffix(v1, name) || !strings.HasSuffix(v2, name) {
		t.Fatalf("archive names should stay recognizable: %s / %s", v1, v2)
	}
	if filepath.Dir(v1) != filepath.Dir(v2) {
		t.Fatalf("archives should share the downloads dir: %s / %s", v1, v2)
	}
}

func TestFetchClearsResumeSidecarsWhenDestExists(t *testing.T) {
	dir := t.TempDir()
	dest := filepath.Join(dir, "archive.tar.gz")
	for _, f := range []string{dest, dest + ".part", validatorPath(dest + ".part")} {
		if err := os.WriteFile(f, []byte("x"), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Error("fetch hit the network for an artifact already on disk")
	}))
	defer srv.Close()

	if err := fetch(srv.URL, dest, "archive", downloadHTTPClient()); err != nil {
		t.Fatal(err)
	}
	for _, f := range []string{dest + ".part", validatorPath(dest + ".part"), dest + ".lock"} {
		if _, err := os.Stat(f); !os.IsNotExist(err) {
			t.Errorf("%s should not survive a completed fetch", filepath.Base(f))
		}
	}
}

func TestFetchRefusesUntrustedRedirectWithoutRetrying(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		http.Redirect(w, r, "https://attacker.example/payload", http.StatusFound)
	}))
	defer srv.Close()

	dest := filepath.Join(t.TempDir(), "artifact")
	err := fetch(srv.URL, dest, "artifact", downloadHTTPClient())
	if err == nil || !strings.Contains(err.Error(), "untrusted host") {
		t.Fatalf("redirect off the allowlist should fail, got %v", err)
	}
	if hits.Load() != 1 {
		t.Fatalf("a refused redirect is permanent, but it was retried %d times", hits.Load())
	}
	if have(dest) {
		t.Fatal("nothing should be written for a refused download")
	}
}
