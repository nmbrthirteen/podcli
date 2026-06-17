package provision

import (
	"archive/tar"
	"compress/gzip"
	"os"
	"path/filepath"
	"testing"
)

// writeTarGz builds a .tar.gz from (name, content) entries; empty content means a dir.
func writeTarGz(t *testing.T, path string, entries [][2]string) {
	t.Helper()
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	gz := gzip.NewWriter(f)
	tw := tar.NewWriter(gz)
	for _, e := range entries {
		name, body := e[0], e[1]
		if body == "" {
			tw.WriteHeader(&tar.Header{Name: name, Typeflag: tar.TypeDir, Mode: 0o755})
			continue
		}
		tw.WriteHeader(&tar.Header{Name: name, Typeflag: tar.TypeReg, Mode: 0o644, Size: int64(len(body))})
		tw.Write([]byte(body))
	}
	tw.Close()
	gz.Close()
}

// `tar -C dir .` emits a "./" root entry; extractTarGz must accept it, not reject
// it as an escaping path (the bug that broke Remotion bundle extraction).
func TestExtractTarGzAcceptsRootEntry(t *testing.T) {
	tmp := t.TempDir()
	archive := filepath.Join(tmp, "bundle.tar.gz")
	writeTarGz(t, archive, [][2]string{
		{"./", ""},
		{"./render.mjs", "export default 1\n"},
		{"./node_modules/x/index.js", "x\n"},
	})
	dest := filepath.Join(tmp, "out")
	if err := extractTarGz(archive, dest); err != nil {
		t.Fatalf("extractTarGz rejected a ./-rooted archive: %v", err)
	}
	if _, err := os.Stat(filepath.Join(dest, "render.mjs")); err != nil {
		t.Errorf("render.mjs not extracted: %v", err)
	}
	if _, err := os.Stat(filepath.Join(dest, "node_modules", "x", "index.js")); err != nil {
		t.Errorf("nested file not extracted: %v", err)
	}
}

func TestExtractTarGzRejectsEscapingPath(t *testing.T) {
	tmp := t.TempDir()
	archive := filepath.Join(tmp, "evil.tar.gz")
	writeTarGz(t, archive, [][2]string{{"../escape.txt", "bad\n"}})
	if err := extractTarGz(archive, filepath.Join(tmp, "out")); err == nil {
		t.Fatal("expected extractTarGz to reject a ../ escaping entry")
	}
}
