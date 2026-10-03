package main

import (
	"os"
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
