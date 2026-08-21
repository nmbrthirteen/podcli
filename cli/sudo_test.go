package main

import (
	"strings"
	"testing"
)

func TestNoRefusalWithoutSudo(t *testing.T) {
	if msg := sudoRefusalMessage("", "/home/x/podcli", false); msg != "" {
		t.Fatalf("expected no refusal, got %q", msg)
	}
}

func TestRefusalNamesTheRealUserAndHome(t *testing.T) {
	msg := sudoRefusalMessage("nika", "/Users/nika/Library/Application Support/podcli", false)
	if msg == "" {
		t.Fatal("expected a refusal")
	}
	for _, want := range []string{"refusing to run under sudo", "nika", "/Users/nika/Library/Application Support/podcli"} {
		if !strings.Contains(msg, want) {
			t.Errorf("message missing %q:\n%s", want, msg)
		}
	}
	if strings.Contains(msg, "chown") {
		t.Errorf("clean home should not suggest a repair:\n%s", msg)
	}
}

func TestRefusalSuggestsRepairWhenAlreadyRootOwned(t *testing.T) {
	msg := sudoRefusalMessage("nika", "/Users/nika/Library/Application Support/podcli", true)
	want := `sudo chown -R nika '/Users/nika/Library/Application Support/podcli'`
	if !strings.Contains(msg, want) {
		t.Errorf("expected repair command %q in:\n%s", want, msg)
	}
}

func TestShellQuoteOnlyWhenNeeded(t *testing.T) {
	if got := shellQuote("/home/x/podcli"); got != "/home/x/podcli" {
		t.Errorf("plain path should not be quoted, got %q", got)
	}
	if got := shellQuote("/a b/c"); got != "'/a b/c'" {
		t.Errorf("spaced path: got %q", got)
	}
	if got := shellQuote("/a'b"); got != `'/a'\''b'` {
		t.Errorf("single quote: got %q", got)
	}
}
