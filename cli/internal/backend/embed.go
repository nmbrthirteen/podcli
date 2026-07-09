// Package backend ships the Python processing backend inside the launcher
// binary so an installed podcli runs without the source repo. The files/ tree is
// synced from the repo backend/ at build time (`go generate ./...` or CI) and is
// gitignored — never edit files/ by hand.
package backend

import (
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"strings"
)

//go:generate sh sync.sh
//go:embed all:files
var files embed.FS

var integrityFiles = []string{
	"cli.py",
	"services/clip_generator.py",
	"services/claude_suggest.py",
	"services/thumbnail_ai.py",
	"main.py",
}

func embeddedDigest(rel string) (string, error) {
	data, err := files.ReadFile(path.Join("files", filepath.ToSlash(rel)))
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:]), nil
}

func fileDigest(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:]), nil
}

// ExtractedMatchesEmbedded reports whether dest holds the same bytes as the
// launcher bundle for a relative backend path.
func ExtractedMatchesEmbedded(dest, rel string) bool {
	want, err := embeddedDigest(rel)
	if err != nil {
		return false
	}
	got, err := fileDigest(filepath.Join(dest, rel))
	if err != nil {
		return false
	}
	return want == got
}

// IntegrityMismatches lists embedded backend files that differ on disk.
func IntegrityMismatches(dest string) []string {
	var out []string
	for _, rel := range integrityFiles {
		if !ExtractedMatchesEmbedded(dest, rel) {
			out = append(out, rel)
		}
	}
	return out
}

// stampName matches the marker provision writes for the studio and Remotion
// bundles, so every version-bound artifact under runtime/ is checked the same way.
const stampName = ".podcli-version"

// Version returns the launcher version that extracted dest, or "" if unstamped.
func Version(dest string) string {
	b, err := os.ReadFile(filepath.Join(dest, stampName))
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(b))
}

// IsCurrent reports whether dest holds this launcher's backend. An unstamped dest
// is stale by definition: it predates stamping, so it carries whatever release
// first provisioned it.
func IsCurrent(dest, version string) bool {
	return Version(dest) == version
}

// Extract replaces dest with the embedded backend tree. dest is removed first so a
// stale dev symlink or an older extracted copy never shadows the shipped one. The
// stamp is written last, so a crash mid-extract leaves dest unstamped and the next
// run re-extracts.
func Extract(dest, version string) error {
	if err := os.RemoveAll(dest); err != nil {
		return err
	}
	err := fs.WalkDir(files, "files", func(p string, d fs.DirEntry, err error) error {
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
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(dest, stampName), []byte(version), 0o644)
}
