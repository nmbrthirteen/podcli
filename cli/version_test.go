package main

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

// The embedded VERSION is generated from package.json. If someone bumps one without
// running `go generate`, the launcher ships a version that disagrees with the repo,
// which is how a v2.2.1 default survived into the v2.4.0 release.
func TestVersionMatchesPackageJSON(t *testing.T) {
	b, err := os.ReadFile("../package.json")
	if err != nil {
		t.Fatalf("read package.json: %v", err)
	}
	var pkg struct {
		Version string `json:"version"`
	}
	if err := json.Unmarshal(b, &pkg); err != nil {
		t.Fatalf("parse package.json: %v", err)
	}
	if pkg.Version == "" {
		t.Fatal("package.json has no version")
	}
	if Version != pkg.Version {
		t.Fatalf("embedded VERSION = %q, package.json = %q - run `go generate ./...` in cli/", Version, pkg.Version)
	}
}

// The stamp comparison and the update check are string equality, so a stray newline
// from VERSION would make every backend look stale and every release look newer.
func TestVersionIsTrimmed(t *testing.T) {
	if Version == "" {
		t.Fatal("Version is empty")
	}
	if strings.TrimSpace(Version) != Version {
		t.Fatalf("Version %q has surrounding whitespace", Version)
	}
}
