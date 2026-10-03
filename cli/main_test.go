package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestTranscribeModel(t *testing.T) {
	if got := transcribeModel([]string{"process", "episode.mp4"}); got != "base" {
		t.Fatalf("default model = %q, want base", got)
	}
	if got := transcribeModel([]string{"process", "episode.mp4", "--fast"}); got != "tiny" {
		t.Fatalf("fast model with unset language = %q, want tiny (multilingual, no language conditioning)", got)
	}
	if got := transcribeModel([]string{"process", "episode.mp4", "--fast", "--language", "en"}); got != "tiny.en" {
		t.Fatalf("fast english model = %q, want tiny.en", got)
	}
	if got := transcribeModel([]string{"process", "episode.mp4", "--fast", "--language", "ka"}); got != "tiny" {
		t.Fatalf("fast non-english model = %q, want tiny", got)
	}
	if got := transcribeModel([]string{"process", "episode.mp4", "--fast", "--language=ka"}); got != "tiny" {
		t.Fatalf("fast non-english model (= form) = %q, want tiny", got)
	}
}

func TestTranscribeEngineAssemblyAI(t *testing.T) {
	old, ok := os.LookupEnv("PODCLI_ENGINE")
	t.Cleanup(func() {
		if ok {
			os.Setenv("PODCLI_ENGINE", old)
		} else {
			os.Unsetenv("PODCLI_ENGINE")
		}
	})
	os.Unsetenv("PODCLI_ENGINE")
	if got := transcribeEngine([]string{"process", "episode.mp4", "--engine", "assemblyai"}); got != "assemblyai" {
		t.Fatalf("engine = %q, want assemblyai", got)
	}
}

func TestTranscribeEngineOmnilingual(t *testing.T) {
	old, ok := os.LookupEnv("PODCLI_ENGINE")
	t.Cleanup(func() {
		if ok {
			os.Setenv("PODCLI_ENGINE", old)
		} else {
			os.Unsetenv("PODCLI_ENGINE")
		}
	})
	os.Unsetenv("PODCLI_ENGINE")
	if got := transcribeEngine([]string{"process", "episode.mp4", "--engine", "omnilingual"}); got != "omnilingual" {
		t.Fatalf("engine = %q, want omnilingual", got)
	}
}

func TestSetupInterruptedAfterBackendOnly(t *testing.T) {
	home := t.TempDir()
	t.Setenv("PODCLI_HOME", home)
	t.Setenv("PODCLI_BACKEND", "")
	t.Setenv("PODCLI_PYTHON", "")
	chdirTemp(t)
	backendDir := filepath.Join(home, "runtime", "backend")
	if err := os.MkdirAll(backendDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(backendDir, "cli.py"), nil, 0o644); err != nil {
		t.Fatal(err)
	}

	if !setupInterrupted() {
		t.Fatal("a backend with no Python runtime and no completion stamp should count as interrupted")
	}
	if err := os.WriteFile(setupDoneStamp(), []byte("2.8.1\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if setupInterrupted() {
		t.Fatal("a completed setup that skipped Python must not rerun on every command")
	}
}

func TestSetupNotInterruptedWithHermeticPython(t *testing.T) {
	home := t.TempDir()
	t.Setenv("PODCLI_HOME", home)
	t.Setenv("PODCLI_BACKEND", "")
	t.Setenv("PODCLI_PYTHON", "")
	chdirTemp(t)
	for _, f := range []string{filepath.Join("runtime", "backend", "cli.py"), filepath.Join("runtime", "python", "bin", "python3"), filepath.Join("runtime", "python", "python.exe")} {
		p := filepath.Join(home, f)
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, nil, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	if setupInterrupted() {
		t.Fatal("an install from before the stamp existed, with its Python runtime, must not rerun setup")
	}
}

// chdirTemp leaves any repo checkout, whose backend/ would otherwise win
// BackendRoot's working-directory search.
func chdirTemp(t *testing.T) {
	t.Helper()
	prev, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chdir(t.TempDir()); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chdir(prev) })
}
