package provision

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestParseChecksums(t *testing.T) {
	in := []byte("abc123  podcli-darwin-arm64\n" +
		"DEADBEEF  ./dist/whisper-cli-linux-amd64\n" +
		"\n" +
		"malformed-line\n")
	got := ParseChecksums(in)
	if got["podcli-darwin-arm64"] != "abc123" {
		t.Fatalf("podcli hash = %q", got["podcli-darwin-arm64"])
	}
	if got["whisper-cli-linux-amd64"] != "deadbeef" {
		t.Fatalf("whisper hash = %q (want lowercased, basename-keyed)", got["whisper-cli-linux-amd64"])
	}
	if len(got) != 2 {
		t.Fatalf("expected 2 entries, got %d: %v", len(got), got)
	}
}

func TestSymlinkTargetInside(t *testing.T) {
	root := filepath.Clean("/tmp/dest") + string(os.PathSeparator)
	cases := []struct {
		name     string
		linkPath string
		linkname string
		ok       bool
	}{
		{"relative inside", "/tmp/dest/a/link", "../b", true},
		{"to root", "/tmp/dest/link", ".", true},
		{"escape via dotdot", "/tmp/dest/link", "../../etc/passwd", false},
		{"absolute", "/tmp/dest/link", "/etc/passwd", false},
		{"deep escape", "/tmp/dest/a/b/link", "../../../outside", false},
	}
	for _, c := range cases {
		if got := symlinkTargetInside(c.linkPath, c.linkname, root); got != c.ok {
			t.Errorf("%s: symlinkTargetInside(%q,%q)=%v want %v", c.name, c.linkPath, c.linkname, got, c.ok)
		}
	}
}

// TestExtractTarGzRejectsEscapingSymlink builds an in-memory tarball with a
// symlink that escapes the destination and confirms extraction refuses it.
func TestExtractTarGzRejectsEscapingSymlink(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink semantics differ on Windows")
	}
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	if err := tw.WriteHeader(&tar.Header{Name: "evil", Typeflag: tar.TypeSymlink, Linkname: "../../../../etc", Mode: 0o777}); err != nil {
		t.Fatal(err)
	}
	tw.Close()
	gz.Close()

	dir := t.TempDir()
	archive := filepath.Join(dir, "evil.tar.gz")
	if err := os.WriteFile(archive, buf.Bytes(), 0o644); err != nil {
		t.Fatal(err)
	}
	dest := filepath.Join(dir, "out")
	if err := extractTarGz(archive, dest); err == nil {
		t.Fatal("expected extractTarGz to reject escaping symlink, got nil error")
	}
}

func TestExtractTarGzRejectsEscapingHardlink(t *testing.T) {
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	if err := tw.WriteHeader(&tar.Header{Name: "evil", Typeflag: tar.TypeLink, Linkname: "../../../../etc/passwd", Mode: 0o644}); err != nil {
		t.Fatal(err)
	}
	tw.Close()
	gz.Close()

	dir := t.TempDir()
	archive := filepath.Join(dir, "evil.tar.gz")
	if err := os.WriteFile(archive, buf.Bytes(), 0o644); err != nil {
		t.Fatal(err)
	}
	dest := filepath.Join(dir, "out")
	if err := extractTarGz(archive, dest); err == nil {
		t.Fatal("expected extractTarGz to reject escaping hardlink, got nil error")
	}
}
