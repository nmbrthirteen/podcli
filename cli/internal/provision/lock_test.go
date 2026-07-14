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

// A loaded CI runner can starve the heartbeat goroutine for tens of
// milliseconds, so keep the stale window far larger than the heartbeat rather
// than scaling both down together: a tight ratio makes a healthy lock look
// abandoned, which is the very thing these tests are meant to catch.
func shortLockTimings(t *testing.T, wait, stale time.Duration) {
	heartbeat, prevStale, poll, prevWait := lockHeartbeat, lockStale, lockPoll, lockWait
	t.Cleanup(func() { lockHeartbeat, lockStale, lockPoll, lockWait = heartbeat, prevStale, poll, prevWait })
	lockHeartbeat, lockStale, lockPoll, lockWait = 20*time.Millisecond, stale, 5*time.Millisecond, wait
}

func TestHeldLockIsNotStolenAfterStaleWindow(t *testing.T) {
	shortLockTimings(t, 400*time.Millisecond, 500*time.Millisecond)
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

func TestSupersededOwnerCannotHeartbeatOrReleaseReplacement(t *testing.T) {
	shortLockTimings(t, 400*time.Millisecond, 500*time.Millisecond)
	dest := filepath.Join(t.TempDir(), "artifact")
	lock := dest + ".lock"

	unlock, err := acquireLock(dest)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(lock); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(lock, []byte("replacement-owner"), 0o644); err != nil {
		t.Fatal(err)
	}
	replacementTime := time.Now().Add(-time.Hour)
	if err := os.Chtimes(lock, replacementTime, replacementTime); err != nil {
		t.Fatal(err)
	}

	time.Sleep(3 * lockHeartbeat)
	info, err := os.Stat(lock)
	if err != nil {
		t.Fatal(err)
	}
	if !info.ModTime().Equal(replacementTime) {
		t.Fatalf("superseded owner heartbeated replacement lock: got %v want %v", info.ModTime(), replacementTime)
	}
	unlock()
	got, err := os.ReadFile(lock)
	if err != nil {
		t.Fatalf("superseded owner removed replacement lock: %v", err)
	}
	if string(got) != "replacement-owner" {
		t.Fatalf("replacement ownership changed to %q", got)
	}
}

func TestLockOwnersReceiveUniqueTokens(t *testing.T) {
	dest := filepath.Join(t.TempDir(), "artifact")
	lock := dest + ".lock"

	unlock, err := acquireLock(dest)
	if err != nil {
		t.Fatal(err)
	}
	first, err := os.ReadFile(lock)
	if err != nil {
		t.Fatal(err)
	}
	unlock()

	unlock, err = acquireLock(dest)
	if err != nil {
		t.Fatal(err)
	}
	second, err := os.ReadFile(lock)
	if err != nil {
		t.Fatal(err)
	}
	unlock()

	if string(first) == string(second) {
		t.Fatalf("lock ownership token was reused: %q", first)
	}
}

func TestStaleLockTakeoverIsExclusive(t *testing.T) {
	// Stale window well past the hold time, so a winner's lock is fresh on its
	// own creation stamp and exclusivity does not hinge on heartbeat scheduling.
	shortLockTimings(t, 10*time.Second, 3*time.Second)
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
