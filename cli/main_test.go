package main

import "testing"

func TestTranscribeModel(t *testing.T) {
	if got := transcribeModel([]string{"process", "episode.mp4"}); got != "base" {
		t.Fatalf("default model = %q, want base", got)
	}
	if got := transcribeModel([]string{"process", "episode.mp4", "--fast"}); got != "tiny.en" {
		t.Fatalf("fast model = %q, want tiny.en", got)
	}
}
