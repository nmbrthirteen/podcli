package provision

import (
	"archive/tar"
	"archive/zip"
	"compress/gzip"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"podcli/internal/paths"
)

// nodeVersion is the hermetic Node.js used to serve the studio web UI. Node only
// runs the bundled server (rendering is delegated to the Python backend), so any
// recent LTS works; pin for reproducibility.
const nodeVersion = "20.18.1"

var nodeTriples = map[string]string{
	"darwin/amd64":  "darwin-x64",
	"darwin/arm64":  "darwin-arm64",
	"linux/amd64":   "linux-x64",
	"linux/arm64":   "linux-arm64",
	"windows/amd64": "win-x64",
}

func NodeDir() string { return filepath.Join(paths.RuntimeDir(), "node") }

func NodeBin() string {
	if runtime.GOOS == "windows" {
		return filepath.Join(NodeDir(), "node.exe")
	}
	return filepath.Join(NodeDir(), "bin", "node")
}

func StudioDir() string    { return filepath.Join(paths.RuntimeDir(), "studio") }
func StudioServer() string { return filepath.Join(StudioDir(), "web-server.mjs") }

func EnsureNode() (string, error) {
	bin := NodeBin()
	if have(bin) {
		return bin, nil
	}
	triple, ok := nodeTriples[runtime.GOOS+"/"+runtime.GOARCH]
	if !ok {
		return "", fmt.Errorf("no node build for %s/%s", runtime.GOOS, runtime.GOARCH)
	}
	ext := "tar.gz"
	if runtime.GOOS == "windows" {
		ext = "zip"
	}
	base := fmt.Sprintf("node-v%s-%s", nodeVersion, triple)
	url := fmt.Sprintf("https://nodejs.org/dist/v%s/%s.%s", nodeVersion, base, ext)
	archive := filepath.Join(os.TempDir(), "podcli-"+base+"."+ext)
	if err := fetch(url, archive, "node"); err != nil {
		return "", err
	}
	defer os.Remove(archive)
	if err := os.RemoveAll(NodeDir()); err != nil {
		return "", err
	}
	var err error
	if ext == "zip" {
		err = extractZipStrip1(archive, NodeDir())
	} else {
		err = extractTarGzStrip1(archive, NodeDir())
	}
	if err != nil {
		return "", err
	}
	if runtime.GOOS != "windows" {
		os.Chmod(bin, 0o755)
	}
	if !have(bin) {
		return "", fmt.Errorf("node missing after extraction in %s", NodeDir())
	}
	return bin, nil
}

// EnsureStudio fetches the prebuilt, platform-independent studio bundle (server +
// SPA) from the latest release into StudioDir.
func EnsureStudio() (string, error) {
	server := StudioServer()
	if have(server) {
		return StudioDir(), nil
	}
	url, err := latestReleaseAssetURL("studio-bundle.tar.gz")
	if err != nil {
		return "", err
	}
	archive := filepath.Join(os.TempDir(), "podcli-studio-bundle.tar.gz")
	if err := fetch(url, archive, "studio"); err != nil {
		return "", err
	}
	defer os.Remove(archive)
	if err := os.RemoveAll(StudioDir()); err != nil {
		return "", err
	}
	if err := os.MkdirAll(StudioDir(), 0o755); err != nil {
		return "", err
	}
	if err := extractTarGz(archive, StudioDir()); err != nil {
		return "", err
	}
	if !have(server) {
		return "", fmt.Errorf("studio server missing after extraction in %s", StudioDir())
	}
	return StudioDir(), nil
}

// strip1 drops the leading path component (node tarballs/zips nest everything
// under node-vX-os-arch/).
func strip1(name string) string {
	name = filepath.ToSlash(name)
	if i := strings.IndexByte(name, '/'); i >= 0 {
		return name[i+1:]
	}
	return ""
}

func extractTarGzStrip1(archive, dest string) error {
	f, err := os.Open(archive)
	if err != nil {
		return err
	}
	defer f.Close()
	gz, err := gzip.NewReader(f)
	if err != nil {
		return err
	}
	defer gz.Close()
	tr := tar.NewReader(gz)
	root := filepath.Clean(dest) + string(os.PathSeparator)
	for {
		h, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return err
		}
		rel := strip1(h.Name)
		if rel == "" {
			continue
		}
		target := filepath.Join(dest, rel)
		if !strings.HasPrefix(target, root) {
			return fmt.Errorf("unsafe path in archive: %s", h.Name)
		}
		switch h.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
		case tar.TypeReg:
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			out, err := os.OpenFile(target, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, os.FileMode(h.Mode))
			if err != nil {
				return err
			}
			_, err = io.Copy(out, tr)
			out.Close()
			if err != nil {
				return err
			}
		case tar.TypeSymlink:
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			os.Remove(target)
			os.Symlink(h.Linkname, target)
		}
	}
	return nil
}

func extractZipStrip1(archive, dest string) error {
	zr, err := zip.OpenReader(archive)
	if err != nil {
		return err
	}
	defer zr.Close()
	root := filepath.Clean(dest) + string(os.PathSeparator)
	for _, zf := range zr.File {
		rel := strip1(zf.Name)
		if rel == "" {
			continue
		}
		target := filepath.Join(dest, rel)
		if !strings.HasPrefix(target, root) {
			return fmt.Errorf("unsafe path in archive: %s", zf.Name)
		}
		if zf.FileInfo().IsDir() {
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		rc, err := zf.Open()
		if err != nil {
			return err
		}
		out, err := os.OpenFile(target, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, zf.Mode())
		if err != nil {
			rc.Close()
			return err
		}
		_, err = io.Copy(out, rc)
		out.Close()
		rc.Close()
		if err != nil {
			return err
		}
	}
	return nil
}
