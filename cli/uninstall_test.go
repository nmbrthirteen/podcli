package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLinkPointsToAbsoluteAndRelativeSymlinks(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "target")
	if err := os.WriteFile(target, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	absLink := filepath.Join(dir, "abs")
	if err := os.Symlink(target, absLink); err != nil {
		t.Skipf("symlink unavailable: %v", err)
	}
	if !linkPointsTo(absLink, target) {
		t.Fatalf("absolute symlink should point to target")
	}
	relLink := filepath.Join(dir, "rel")
	if err := os.Symlink("target", relLink); err != nil {
		t.Fatal(err)
	}
	if !linkPointsTo(relLink, target) {
		t.Fatalf("relative symlink should point to target")
	}
}

func TestUninstallTargetsPreserveUserDataUnlessPurged(t *testing.T) {
	home := filepath.Join(t.TempDir(), "podcli")
	got := uninstallTargets(home, false)
	for _, p := range got {
		if p == home {
			t.Fatalf("non-purge uninstall should not remove the whole home: %v", got)
		}
	}
	purged := uninstallTargets(home, true)
	if len(purged) != 1 || purged[0] != home {
		t.Fatalf("purge targets = %v, want only %s", purged, home)
	}
}

func TestPathContainsDetectsRunningBinaryUnderTarget(t *testing.T) {
	dir := t.TempDir()
	bin := filepath.Join(dir, "bin")
	self := filepath.Join(bin, "podcli")
	if err := os.MkdirAll(bin, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(self, []byte("x"), 0o755); err != nil {
		t.Fatal(err)
	}
	if !pathContains(bin, self) {
		t.Fatalf("bin target should contain running binary")
	}
	if !pathContains(dir, self) {
		t.Fatalf("home target should contain running binary")
	}
	if pathContains(filepath.Join(dir, "models"), self) {
		t.Fatalf("sibling target should not contain running binary")
	}
}
