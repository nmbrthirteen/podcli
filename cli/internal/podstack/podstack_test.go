package podstack

import (
	"os"
	"path/filepath"
	"testing"
)

func TestInstallCommandsWritesEveryFileOnFirstRun(t *testing.T) {
	project := t.TempDir()

	report, err := installCommands(project)
	if err != nil {
		t.Fatalf("installCommands: %v", err)
	}
	if len(report.Installed) == 0 {
		t.Fatal("expected files to be reported as installed")
	}
	if len(report.Updated) != 0 || len(report.UserModified) != 0 {
		t.Fatalf("first run should only install, got updated=%v userModified=%v", report.Updated, report.UserModified)
	}

	dest := filepath.Join(project, ".claude", "commands")
	if _, err := os.Stat(filepath.Join(dest, "auto.md")); err != nil {
		t.Fatalf("auto.md not written: %v", err)
	}
	if _, err := os.Stat(filepath.Join(dest, manifestName)); err != nil {
		t.Fatalf("manifest not written: %v", err)
	}
}

func TestInstallCommandsIsIdempotentWhenNothingChanged(t *testing.T) {
	project := t.TempDir()
	if _, err := installCommands(project); err != nil {
		t.Fatalf("first install: %v", err)
	}

	report, err := installCommands(project)
	if err != nil {
		t.Fatalf("second install: %v", err)
	}
	if report.hasUpdates() || len(report.UserModified) != 0 {
		t.Fatalf("expected no-op on second run, got %+v", report)
	}
}

func TestInstallCommandsLeavesUserEditsAloneAndReportsThem(t *testing.T) {
	project := t.TempDir()
	if _, err := installCommands(project); err != nil {
		t.Fatalf("first install: %v", err)
	}
	dest := filepath.Join(project, ".claude", "commands")
	target := filepath.Join(dest, "auto.md")

	installed, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("read installed file: %v", err)
	}
	edited := append(append([]byte{}, installed...), []byte("\n<!-- user edit -->\n")...)
	if err := os.WriteFile(target, edited, 0o644); err != nil {
		t.Fatalf("simulate user edit: %v", err)
	}

	report, err := installCommands(project)
	if err != nil {
		t.Fatalf("second install: %v", err)
	}
	found := false
	for _, f := range report.UserModified {
		if f == "auto.md" {
			found = true
		}
	}
	if !found {
		t.Fatalf("expected auto.md to be reported as user-modified, got %+v", report)
	}

	after, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("read after install: %v", err)
	}
	if string(after) != string(edited) {
		t.Fatal("user-modified file was overwritten, but it should have been left alone")
	}
}

func TestInstallCommandsUpdatesFilesTheUserDidNotTouch(t *testing.T) {
	project := t.TempDir()
	if _, err := installCommands(project); err != nil {
		t.Fatalf("first install: %v", err)
	}
	dest := filepath.Join(project, ".claude", "commands")
	target := filepath.Join(dest, "auto.md")

	// Simulate an older podcli version: rewrite both the file and its
	// manifest entry to some other content, consistently, as if that's what
	// an earlier install wrote. The file is still untouched by the user
	// relative to that record, so the next install should treat it as safe
	// to refresh to the current embedded content.
	stale := []byte("# an older auto.md\n")
	if err := os.WriteFile(target, stale, 0o644); err != nil {
		t.Fatalf("simulate stale install: %v", err)
	}
	manifest := readManifest(dest)
	manifest.Files["auto.md"] = sha256Hex(stale)
	if err := writeManifest(dest, manifest); err != nil {
		t.Fatalf("rewrite manifest: %v", err)
	}

	report, err := installCommands(project)
	if err != nil {
		t.Fatalf("second install: %v", err)
	}
	found := false
	for _, f := range report.Updated {
		if f == "auto.md" {
			found = true
		}
	}
	if !found {
		t.Fatalf("expected auto.md to be reported as updated, got %+v", report)
	}
	if len(report.UserModified) != 0 {
		t.Fatalf("unmodified file should not be reported as user-modified, got %v", report.UserModified)
	}

	after, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("read after install: %v", err)
	}
	if string(after) == string(stale) {
		t.Fatal("stale file was not refreshed to the current embedded content")
	}
}

func TestInstallCommandsAdoptsPreManifestFilesWithoutOverwriting(t *testing.T) {
	project := t.TempDir()
	dest := filepath.Join(project, ".claude", "commands")
	if err := os.MkdirAll(dest, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	// A file that predates manifest tracking: some arbitrary content, no
	// manifest entry for it at all.
	legacy := filepath.Join(dest, "auto.md")
	if err := os.WriteFile(legacy, []byte("# a pre-existing, possibly hand-edited auto.md\n"), 0o644); err != nil {
		t.Fatalf("seed legacy file: %v", err)
	}

	report, err := installCommands(project)
	if err != nil {
		t.Fatalf("installCommands: %v", err)
	}
	for _, f := range report.Installed {
		if f == "auto.md" {
			t.Fatal("pre-manifest file must not be reported as freshly installed")
		}
	}
	for _, f := range report.Updated {
		if f == "auto.md" {
			t.Fatal("pre-manifest file must not be overwritten on first sight")
		}
	}

	content, err := os.ReadFile(legacy)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if string(content) != "# a pre-existing, possibly hand-edited auto.md\n" {
		t.Fatal("pre-manifest file content was changed, but it should have been adopted as-is")
	}

	manifest := readManifest(dest)
	if manifest.Files["auto.md"] == "" {
		t.Fatal("expected a baseline hash to be recorded for the adopted file")
	}
}
