package provision

import (
	"archive/tar"
	"archive/zip"
	"compress/gzip"
	"fmt"
	"io"
	"os"
	"os/exec"
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

func bundleStamp(dir string) string { return filepath.Join(dir, ".podcli-version") }

func bundleAt(dir, version string) bool {
	b, err := os.ReadFile(bundleStamp(dir))
	return err == nil && strings.TrimSpace(string(b)) == version
}

func writeBundleStamp(dir, version string) {
	os.WriteFile(bundleStamp(dir), []byte(version), 0o644)
}

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
	archive, err := downloadPath("podcli-" + base + "." + ext)
	if err != nil {
		return "", err
	}
	if err := fetch(url, archive, "node"); err != nil {
		return "", err
	}
	defer os.Remove(archive)
	if err := verifyDownload(archive, fmt.Sprintf("https://nodejs.org/dist/v%s/SHASUMS256.txt", nodeVersion), base+"."+ext); err != nil {
		return "", err
	}
	if err := os.RemoveAll(NodeDir()); err != nil {
		return "", err
	}
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

func RemotionDir() string    { return filepath.Join(paths.RuntimeDir(), "remotion") }
func RemotionScript() string { return filepath.Join(RemotionDir(), "render.mjs") }

// EnsureRemotion fetches the per-platform Remotion render bundle (remotion/ +
// a production node_modules with native bindings) and extracts it into the
// runtime dir so remotion/ and node_modules/ sit beside backend/. Per-platform
// because @rspack and the Remotion compositor ship native binaries.
func EnsureRemotion(version string) (string, error) {
	if have(RemotionScript()) && bundleAt(RemotionDir(), version) {
		return RemotionDir(), nil
	}
	name := fmt.Sprintf("remotion-bundle-%s-%s.tar.gz", runtime.GOOS, runtime.GOARCH)
	assets, err := latestReleaseAssets()
	if err != nil {
		return "", err
	}
	url, ok := assets[name]
	if !ok {
		return "", fmt.Errorf("asset %s not in latest release", name)
	}
	archive, err := downloadPath("podcli-" + name)
	if err != nil {
		return "", err
	}
	if err := fetch(url, archive, "remotion"); err != nil {
		return "", err
	}
	defer os.Remove(archive)
	if err := verifyReleaseAsset(assets, name, archive); err != nil {
		return "", err
	}
	if err := os.RemoveAll(RemotionDir()); err != nil {
		return "", err
	}
	if err := os.RemoveAll(filepath.Join(paths.RuntimeDir(), "node_modules")); err != nil {
		return "", err
	}
	if err := extractTarGz(archive, paths.RuntimeDir()); err != nil {
		return "", err
	}
	if !have(RemotionScript()) {
		return "", fmt.Errorf("remotion render.mjs missing after extraction")
	}
	writeBundleStamp(RemotionDir(), version)
	return RemotionDir(), nil
}

// PrewarmRemotion compiles the project-independent composition bundle once into
// the managed dir so the first caption render skips the ~20s bundling step.
func PrewarmRemotion() error {
	node := NodeBin()
	if !have(node) || !have(RemotionScript()) {
		return fmt.Errorf("node/remotion not provisioned")
	}
	cmd := exec.Command(node, RemotionScript(), "--prebundle")
	cmd.Dir = RemotionDir()
	cmd.Env = append(os.Environ(), "PODCLI_CACHE_DIR="+filepath.Join(RemotionDir(), ".bundle-cache"))
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

// EnsureRemotionBrowser pre-downloads the Chrome Headless Shell the Remotion
// renderer needs, so the first caption render works offline. Best-effort: if it
// fails, the renderer downloads the browser on first use.
func EnsureRemotionBrowser() error {
	node := NodeBin()
	if !have(node) || !have(RemotionScript()) {
		return fmt.Errorf("node/remotion not provisioned")
	}
	cmd := exec.Command(node, "-e",
		"import('@remotion/renderer').then(r=>r.ensureBrowser()).then(()=>process.exit(0)).catch(e=>{console.error(String(e));process.exit(1)})")
	cmd.Dir = RemotionDir()
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

// EnsureStudio fetches the prebuilt, platform-independent studio bundle (server +
// SPA) from the latest release into StudioDir.
func EnsureStudio(version string) (string, error) {
	server := StudioServer()
	if have(server) && bundleAt(StudioDir(), version) {
		return StudioDir(), nil
	}
	assets, err := latestReleaseAssets()
	if err != nil {
		return "", err
	}
	url, ok := assets["studio-bundle.tar.gz"]
	if !ok {
		return "", fmt.Errorf("asset studio-bundle.tar.gz not in latest release")
	}
	archive, err := downloadPath("podcli-studio-bundle.tar.gz")
	if err != nil {
		return "", err
	}
	if err := fetch(url, archive, "studio"); err != nil {
		return "", err
	}
	defer os.Remove(archive)
	if err := verifyReleaseAsset(assets, "studio-bundle.tar.gz", archive); err != nil {
		return "", err
	}
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
	writeBundleStamp(StudioDir(), version)
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
			if !symlinkTargetInside(target, h.Linkname, root) {
				return fmt.Errorf("unsafe symlink %s -> %s in archive", h.Name, h.Linkname)
			}
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			os.Remove(target)
			if err := os.Symlink(h.Linkname, target); err != nil {
				return err
			}
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
