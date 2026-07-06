package main

import (
	"os"
	"testing"
)

func TestTranscribeModel(t *testing.T) {
	if got := transcribeModel([]string{"process", "episode.mp4"}); got != "base" {
		t.Fatalf("default model = %q, want base", got)
	}
	if got := transcribeModel([]string{"process", "episode.mp4", "--fast"}); got != "tiny.en" {
		t.Fatalf("fast model = %q, want tiny.en", got)
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
