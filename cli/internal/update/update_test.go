package update

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSwapReplacesBinary(t *testing.T) {
	dir := t.TempDir()
	dest := filepath.Join(dir, "podcli")
	if err := os.WriteFile(dest, []byte("OLD"), 0o755); err != nil {
		t.Fatal(err)
	}
	staged := dest + ".new"
	if err := os.WriteFile(staged, []byte("NEW"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := swap(staged, dest); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(dest)
	if string(b) != "NEW" {
		t.Fatalf("swap left %q, want NEW", b)
	}
}

func TestNewer(t *testing.T) {
	cases := []struct {
		remote, current string
		want            bool
	}{
		{"2.0.1", "2.0.0", true},
		{"2.0.0", "2.0.0", false},
		{"1.9.9", "2.0.0", false},
		{"2.1.0", "2.0.9", true},
		{"v2.0.1", "2.0.0", true},
		{"3.0.0", "2.9.9", true},
	}
	for _, c := range cases {
		if got := newer(c.remote, c.current); got != c.want {
			t.Errorf("newer(%q, %q) = %v, want %v", c.remote, c.current, got, c.want)
		}
	}
}

func TestSelfUpdateFailureOmitsPackageManagerFallback(t *testing.T) {
	var out bytes.Buffer
	printSelfUpdateFailure(&out, errors.New("boom"))

	got := out.String()
	for _, unwanted := range []string{"npm", "bun", "package manager"} {
		if strings.Contains(got, unwanted) {
			t.Fatalf("failure output contains %q:\n%s", unwanted, got)
		}
	}
	if !strings.Contains(got, "Your installed podcli was left unchanged.") {
		t.Fatalf("failure output should say the install is unchanged:\n%s", got)
	}
}

func TestDownloadFailureSuggestsRetry(t *testing.T) {
	var out bytes.Buffer
	printSelfUpdateFailure(&out, &phaseError{phase: phaseDownload, err: errors.New("network down")})

	got := out.String()
	if !strings.Contains(got, "Download failed.") {
		t.Fatalf("download failure output should identify the download failure:\n%s", got)
	}
	if strings.Contains(got, "latest release binary manually") {
		t.Fatalf("download failure output should not suggest manual install first:\n%s", got)
	}
}
