package main

import (
	"os"
	"path/filepath"
	"testing"

	"podcli/internal/provision"
)

func TestFirstLine(t *testing.T) {
	if got := firstLine("a\nb\nc"); got != "a" {
		t.Fatalf("got %q", got)
	}
	if got := firstLine("  padded  \n"); got != "padded" {
		t.Fatalf("got %q", got)
	}
	if got := firstLine(""); got != "" {
		t.Fatalf("got %q", got)
	}
}

// go is guaranteed present wherever these tests run (it built the test
// binary), so it stands in for "a real hermetic/PATH tool that works".
func TestRunCheckSucceedsForARealBinary(t *testing.T) {
	c := runCheck("go", "", "go", "version")
	if !c.OK {
		t.Fatalf("expected ok, got %+v", c)
	}
	if c.Detail == "" {
		t.Fatal("expected a detail message")
	}
}

func TestRunCheckFailsWhenBinaryIsMissing(t *testing.T) {
	c := runCheck("nope", "", "podcli-definitely-not-a-real-binary-xyz")
	if c.OK {
		t.Fatalf("expected failure, got %+v", c)
	}
}

func TestRunCheckFailsWhenHermeticPathDoesNotRun(t *testing.T) {
	// A hermetic path that resolves but can't actually run (e.g. corrupted
	// install) must fail the check, not just report "found".
	dir := t.TempDir()
	bogus := filepath.Join(dir, "not-executable")
	if err := os.WriteFile(bogus, []byte("not a real binary"), 0o644); err != nil {
		t.Fatalf("seed bogus binary: %v", err)
	}
	c := runCheck("bogus", bogus, "bogus")
	if c.OK {
		t.Fatalf("expected failure, got %+v", c)
	}
}

func TestModelChecksSkipsUnprovisionedModels(t *testing.T) {
	home := t.TempDir()
	old, hadOld := os.LookupEnv("PODCLI_HOME")
	os.Setenv("PODCLI_HOME", home)
	t.Cleanup(func() {
		if hadOld {
			os.Setenv("PODCLI_HOME", old)
		} else {
			os.Unsetenv("PODCLI_HOME")
		}
	})

	checks := modelChecks()
	if len(checks) != 0 {
		t.Fatalf("expected no checks for an install with no models downloaded, got %+v", checks)
	}
}

func TestModelChecksFlagsAHashMismatch(t *testing.T) {
	home := t.TempDir()
	old, hadOld := os.LookupEnv("PODCLI_HOME")
	os.Setenv("PODCLI_HOME", home)
	t.Cleanup(func() {
		if hadOld {
			os.Setenv("PODCLI_HOME", old)
		} else {
			os.Unsetenv("PODCLI_HOME")
		}
	})

	modelPath := provision.ModelPath("base")
	if err := os.MkdirAll(filepath.Dir(modelPath), 0o755); err != nil {
		t.Fatalf("mkdir models dir: %v", err)
	}
	if err := os.WriteFile(modelPath, []byte("not the real model bytes"), 0o644); err != nil {
		t.Fatalf("seed fake model: %v", err)
	}

	checks := modelChecks()
	found := false
	for _, c := range checks {
		if c.Name == "model base" {
			found = true
			if c.OK {
				t.Fatalf("expected a hash mismatch to fail, got %+v", c)
			}
		}
	}
	if !found {
		t.Fatalf("expected a check for the present-but-wrong base model, got %+v", checks)
	}
}

func TestLevelRegistrationChecksWarnsWhenAtLeastOneAgentIsRegistered(t *testing.T) {
	// Claude registered, Codex not: Codex's unregistered folder is only a
	// warning, since the MCP tools already work through Claude.
	checks := levelRegistrationChecks([]doctorCheck{
		{Name: "mcp registration (Claude)", OK: true},
		{Name: "mcp registration (Codex)", OK: false},
	})
	if checks[0].Level != levelOK {
		t.Fatalf("expected the registered agent to be level ok, got %+v", checks[0])
	}
	if checks[1].Level != levelWarn {
		t.Fatalf("expected the unregistered agent to be a warning, not a failure, got %+v", checks[1])
	}
}

func TestLevelRegistrationChecksFailsWhenNoAgentIsRegisteredAnywhere(t *testing.T) {
	// Both agents detected, neither registered: nothing would actually work,
	// so this escalates to a real failure instead of a quiet warning.
	checks := levelRegistrationChecks([]doctorCheck{
		{Name: "mcp registration (Claude)", OK: false},
		{Name: "mcp registration (Codex)", OK: false},
	})
	for _, c := range checks {
		if c.Level != levelFail {
			t.Fatalf("expected every check to fail when none are registered, got %+v", c)
		}
	}
}

func TestLevelRegistrationChecksIsANoOpOnNoDetectedAgents(t *testing.T) {
	if checks := levelRegistrationChecks(nil); len(checks) != 0 {
		t.Fatalf("expected no checks when no agent CLI was detected, got %+v", checks)
	}
}

func TestChecksAllOKIgnoresWarnLevelChecks(t *testing.T) {
	checks := []doctorCheck{
		{Name: "ffmpeg", OK: true, Level: levelOK},
		{Name: "mcp registration (Codex)", OK: false, Level: levelWarn},
	}
	if !checksAllOK(checks) {
		t.Fatal("a warn-level check must not flip doctor's overall result to failing")
	}
}

func TestChecksAllOKCountsFailLevelChecks(t *testing.T) {
	checks := []doctorCheck{
		{Name: "ffmpeg", OK: false, Level: levelFail},
	}
	if checksAllOK(checks) {
		t.Fatal("a fail-level check must flip doctor's overall result to failing")
	}
}
