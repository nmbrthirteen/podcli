package podstack

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// withHome points os.UserHomeDir() (and HOME/USERPROFILE) at a temp dir for
// the duration of the test, so installCodexSkills never touches the real
// ~/.codex/skills.
func withHome(t *testing.T) string {
	t.Helper()
	home := t.TempDir()
	envVar := "HOME"
	if runtime.GOOS == "windows" {
		envVar = "USERPROFILE"
	}
	old, hadOld := os.LookupEnv(envVar)
	if err := os.Setenv(envVar, home); err != nil {
		t.Fatalf("setenv: %v", err)
	}
	t.Cleanup(func() {
		if hadOld {
			os.Setenv(envVar, old)
		} else {
			os.Unsetenv(envVar)
		}
	})
	return home
}

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

	// The legacy content here is not the stock embedded file, so it must not
	// be adopted as a baseline. Doing so is what let the second run in
	// TestInstallCommandsNeverOverwritesAPreManifestUserEditOnASecondRun
	// clobber a genuine user edit.
	manifest := readManifest(dest)
	if manifest.Files["auto.md"] != "" {
		t.Fatal("a pre-manifest file that doesn't match the stock content must not get a baseline hash recorded")
	}
	found := false
	for _, f := range report.UserModified {
		if f == "auto.md" {
			found = true
		}
	}
	if !found {
		t.Fatalf("expected the non-stock pre-manifest file to be reported as user-modified, got %+v", report)
	}
}

// Regression test for the bug where a pre-manifest, user-edited file was
// adopted with its *current* hash as the baseline on first sight. The next
// run then saw "unmodified relative to baseline" and overwrote it with the
// embedded version, destroying the user's edit instead of leaving it alone.
func TestInstallCommandsNeverOverwritesAPreManifestUserEditOnASecondRun(t *testing.T) {
	project := t.TempDir()
	dest := filepath.Join(project, ".claude", "commands")
	if err := os.MkdirAll(dest, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	legacy := filepath.Join(dest, "auto.md")
	edited := []byte("# a pre-existing, hand-edited auto.md\n")
	if err := os.WriteFile(legacy, edited, 0o644); err != nil {
		t.Fatalf("seed legacy file: %v", err)
	}

	if _, err := installCommands(project); err != nil {
		t.Fatalf("first install: %v", err)
	}
	report, err := installCommands(project)
	if err != nil {
		t.Fatalf("second install: %v", err)
	}
	for _, f := range report.Updated {
		if f == "auto.md" {
			t.Fatal("a pre-manifest user edit must never be adopted as a baseline and then overwritten")
		}
	}

	after, err := os.ReadFile(legacy)
	if err != nil {
		t.Fatalf("read after second install: %v", err)
	}
	if string(after) != string(edited) {
		t.Fatal("pre-manifest user edit was overwritten on the second install run")
	}
}

func TestFrontmatterDescriptionExtractsTheDescriptionField(t *testing.T) {
	raw := "---\ndescription: Full pipeline from transcript to publish-ready package\nallowed-tools: Read\n---\n\n# body\n"
	if got := frontmatterDescription(raw); got != "Full pipeline from transcript to publish-ready package" {
		t.Fatalf("got %q", got)
	}
}

func TestFrontmatterDescriptionIsEmptyWithoutFrontmatter(t *testing.T) {
	if got := frontmatterDescription("# just a body\n"); got != "" {
		t.Fatalf("got %q, want empty", got)
	}
}

func TestCodexSkillContentTranslatesFrontmatterAndKeepsBody(t *testing.T) {
	raw := "---\ndescription: does a thing\nallowed-tools: Read\n---\n\n# /auto\n\nbody text\n"
	got := codexSkillContent("auto", raw)
	if !strings.HasPrefix(got, "---\nname: auto\ndescription: does a thing\n---\n\n") {
		t.Fatalf("unexpected frontmatter translation: %q", got)
	}
	if !strings.Contains(got, "# /auto\n\nbody text\n") {
		t.Fatal("body was dropped or altered")
	}
}

func TestInstallCodexSkillsWritesOneSkillDirPerCommand(t *testing.T) {
	withHome(t)

	if err := installCodexSkills(); err != nil {
		t.Fatalf("installCodexSkills: %v", err)
	}

	skillsDir, err := codexSkillsDir()
	if err != nil {
		t.Fatalf("codexSkillsDir: %v", err)
	}
	for _, name := range Names() {
		skillFile := filepath.Join(skillsDir, name, "SKILL.md")
		data, err := os.ReadFile(skillFile)
		if err != nil {
			t.Fatalf("%s: %v", skillFile, err)
		}
		if !strings.HasPrefix(string(data), "---\nname: "+name+"\n") {
			t.Fatalf("%s: missing expected frontmatter, got: %q", skillFile, string(data)[:min(60, len(data))])
		}
	}
}

func TestInstallCodexSkillsLeavesUserEditsAlone(t *testing.T) {
	withHome(t)
	if err := installCodexSkills(); err != nil {
		t.Fatalf("first install: %v", err)
	}
	skillsDir, err := codexSkillsDir()
	if err != nil {
		t.Fatalf("codexSkillsDir: %v", err)
	}
	target := filepath.Join(skillsDir, "auto", "SKILL.md")
	installed, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("read installed skill: %v", err)
	}
	edited := append(append([]byte{}, installed...), []byte("\n<!-- edited -->\n")...)
	if err := os.WriteFile(target, edited, 0o644); err != nil {
		t.Fatalf("simulate user edit: %v", err)
	}

	if err := installCodexSkills(); err != nil {
		t.Fatalf("second install: %v", err)
	}
	after, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("read after reinstall: %v", err)
	}
	if string(after) != string(edited) {
		t.Fatal("user-edited skill file was overwritten")
	}
}

// Same regression as TestInstallCommandsNeverOverwritesAPreManifestUserEditOnASecondRun,
// for the Codex skills installer: a skill file that predates manifest
// tracking, and doesn't match the stock content, must never be adopted as a
// baseline and then overwritten on a later install.
func TestInstallCodexSkillsNeverOverwritesAPreManifestUserEditOnASecondRun(t *testing.T) {
	withHome(t)
	skillsDir, err := codexSkillsDir()
	if err != nil {
		t.Fatalf("codexSkillsDir: %v", err)
	}
	skillDir := filepath.Join(skillsDir, "auto")
	if err := os.MkdirAll(skillDir, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	target := filepath.Join(skillDir, "SKILL.md")
	edited := []byte("---\nname: auto\ndescription: hand-edited before manifest tracking existed\n---\n\nbody\n")
	if err := os.WriteFile(target, edited, 0o644); err != nil {
		t.Fatalf("seed legacy skill file: %v", err)
	}

	if err := installCodexSkills(); err != nil {
		t.Fatalf("first install: %v", err)
	}
	if err := installCodexSkills(); err != nil {
		t.Fatalf("second install: %v", err)
	}

	after, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("read after second install: %v", err)
	}
	if string(after) != string(edited) {
		t.Fatal("pre-manifest user edit was overwritten on the second install run")
	}
}
