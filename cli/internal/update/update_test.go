package update

import (
	"os"
	"path/filepath"
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
