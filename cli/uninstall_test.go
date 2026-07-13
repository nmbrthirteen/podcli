package main

import (
	"os"
	"path/filepath"
	"testing"

	"podcli/internal/paths"
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

func TestUninstallTargetsKeepUserDataByDefault(t *testing.T) {
	home := filepath.Join(t.TempDir(), "podcli")
	got := uninstallTargets(home, false)
	want := map[string]bool{
		filepath.Join(home, "bin"):     true,
		filepath.Join(home, "runtime"): true,
		filepath.Join(home, "models"):  true,
	}
	if len(got) != len(want) {
		t.Fatalf("uninstall targets = %v, want app dirs only", got)
	}
	for _, p := range got {
		if p == home {
			t.Fatalf("default uninstall must not remove the whole home dir")
		}
		if !want[p] {
			t.Fatalf("unexpected uninstall target %s", p)
		}
	}
}

func TestUninstallTargetsPurgeRemovesHome(t *testing.T) {
	home := filepath.Join(t.TempDir(), "podcli")
	got := uninstallTargets(home, true)
	if len(got) != 1 || got[0] != home {
		t.Fatalf("purge targets = %v, want only %s", got, home)
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

func TestPodcliLinksPreservesSelfOutsideManagedBin(t *testing.T) {
	home := t.TempDir()
	managedHome := filepath.Join(t.TempDir(), "managed")
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("PODCLI_HOME", managedHome)

	linkDir := filepath.Join(home, ".local", "bin")
	if err := os.MkdirAll(linkDir, 0o755); err != nil {
		t.Fatal(err)
	}
	self := filepath.Join(t.TempDir(), "custom", "podcli"+paths.ExeSuffix())
	if err := os.MkdirAll(filepath.Dir(self), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(self, []byte("x"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(self, filepath.Join(linkDir, "podcli"+paths.ExeSuffix())); err != nil {
		t.Skipf("symlink unavailable: %v", err)
	}

	managed := filepath.Join(paths.BinDir(), "podcli"+paths.ExeSuffix())
	if got := podcliLinks(managed, self); len(got) != 0 {
		t.Fatalf("podcliLinks should preserve self outside managed bin: %v", got)
	}
}

func TestRemoveAllExceptKeepsRunningBinaryOnly(t *testing.T) {
	root := t.TempDir()
	keep := filepath.Join(root, "bin", "podcli"+paths.ExeSuffix())
	sibling := filepath.Join(root, "bin", "helper.exe")
	otherDir := filepath.Join(root, "runtime")
	if err := os.MkdirAll(filepath.Dir(keep), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(otherDir, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, p := range []string{keep, sibling, filepath.Join(otherDir, "x")} {
		if err := os.WriteFile(p, []byte("x"), 0o755); err != nil {
			t.Fatal(err)
		}
	}

	if err := removeAllExcept(root, keep); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(keep); err != nil {
		t.Fatalf("kept binary missing: %v", err)
	}
	if _, err := os.Stat(sibling); !os.IsNotExist(err) {
		t.Fatalf("sibling should be removed, got %v", err)
	}
	if _, err := os.Stat(otherDir); !os.IsNotExist(err) {
		t.Fatalf("other dir should be removed, got %v", err)
	}
}
