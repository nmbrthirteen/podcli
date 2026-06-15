// Package backend ships the Python processing backend inside the launcher
// binary so an installed podcli runs without the source repo. The files/ tree is
// synced from the repo backend/ at build time (`go generate ./...` or CI) and is
// gitignored — never edit files/ by hand.
package backend

import (
	"embed"
	"io/fs"
	"os"
	"path/filepath"
)

//go:generate sh sync.sh
//go:embed all:files
var files embed.FS

// Extract replaces dest with the embedded backend tree. dest is removed first so
// a stale dev symlink or an older extracted copy never shadows the shipped one.
func Extract(dest string) error {
	if err := os.RemoveAll(dest); err != nil {
		return err
	}
	return fs.WalkDir(files, "files", func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel("files", p)
		if err != nil {
			return err
		}
		if rel == "." {
			return nil
		}
		target := filepath.Join(dest, rel)
		if d.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		data, err := files.ReadFile(p)
		if err != nil {
			return err
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		return os.WriteFile(target, data, 0o644)
	})
}
