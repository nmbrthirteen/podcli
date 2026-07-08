package backend

import (
	"os"
	"path/filepath"
	"testing"
)

func TestExtractStampsVersion(t *testing.T) {
	dest := filepath.Join(t.TempDir(), "backend")
	if err := Extract(dest, "2.4.1"); err != nil {
		t.Fatalf("Extract: %v", err)
	}
	if _, err := os.Stat(filepath.Join(dest, "cli.py")); err != nil {
		t.Fatalf("cli.py missing after extract: %v", err)
	}
	if got := Version(dest); got != "2.4.1" {
		t.Fatalf("Version = %q, want 2.4.1", got)
	}
	if !IsCurrent(dest, "2.4.1") {
		t.Fatal("IsCurrent false right after extracting that version")
	}
}

// A backend provisioned before stamping existed carries no marker. Treating it as
// current is the bug that let a v2.2.1 backend run under a v2.4.0 launcher.
func TestUnstampedBackendIsStale(t *testing.T) {
	dest := t.TempDir()
	if err := os.WriteFile(filepath.Join(dest, "cli.py"), []byte("old"), 0o644); err != nil {
		t.Fatal(err)
	}
	if Version(dest) != "" {
		t.Fatal("unstamped dir reported a version")
	}
	if IsCurrent(dest, "2.4.1") {
		t.Fatal("unstamped backend reported current")
	}
}

func TestStaleStampIsNotCurrent(t *testing.T) {
	dest := filepath.Join(t.TempDir(), "backend")
	if err := Extract(dest, "2.2.1"); err != nil {
		t.Fatalf("Extract: %v", err)
	}
	if IsCurrent(dest, "2.4.1") {
		t.Fatal("2.2.1 backend reported current under a 2.4.1 launcher")
	}
}

func TestExtractReplacesStaleTree(t *testing.T) {
	dest := filepath.Join(t.TempDir(), "backend")
	if err := Extract(dest, "2.2.1"); err != nil {
		t.Fatalf("Extract: %v", err)
	}
	stray := filepath.Join(dest, "stale_module.py")
	if err := os.WriteFile(stray, []byte("removed"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := Extract(dest, "2.4.1"); err != nil {
		t.Fatalf("re-extract: %v", err)
	}
	if _, err := os.Stat(stray); !os.IsNotExist(err) {
		t.Fatal("stale file survived re-extract")
	}
	if !IsCurrent(dest, "2.4.1") {
		t.Fatal("re-extract did not restamp")
	}
}
