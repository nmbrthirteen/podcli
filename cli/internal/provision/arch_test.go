package provision

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
)

// Cross-compiling a trivial program gives the test real Mach-O, ELF and PE
// headers to read, rather than hand-rolled fixtures that could agree with a
// broken parser.
func buildFor(t *testing.T, goos, goarch string) string {
	t.Helper()
	dir := t.TempDir()
	write := func(name, body string) {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write("go.mod", "module archprobe\n\ngo 1.21\n")
	write("main.go", "package main\n\nfunc main() {}\n")

	out := filepath.Join(dir, "probe")
	cmd := exec.Command("go", "build", "-o", out, ".")
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "GOOS="+goos, "GOARCH="+goarch, "CGO_ENABLED=0")
	if b, err := cmd.CombinedOutput(); err != nil {
		t.Skipf("cannot cross-build %s/%s here: %v: %s", goos, goarch, err, b)
	}
	return out
}

func TestNativeArchIdentifiesEveryReleaseTarget(t *testing.T) {
	for _, tc := range []struct{ goos, goarch string }{
		{"darwin", "arm64"},
		{"darwin", "amd64"},
		{"linux", "amd64"},
		{"linux", "arm64"},
		{"windows", "amd64"},
		{"windows", "arm64"},
	} {
		t.Run(tc.goos+"_"+tc.goarch, func(t *testing.T) {
			got, ok := nativeArch(buildFor(t, tc.goos, tc.goarch))
			if !ok || got != tc.goarch {
				t.Fatalf("nativeArch = (%q, %v), want (%q, true)", got, ok, tc.goarch)
			}
		})
	}
}

// The x86_64 Python that reached Apple Silicon users survived every setup and
// update because provisioning only checked that the file existed.
func TestNativeBinRejectsForeignArch(t *testing.T) {
	foreign := "amd64"
	if runtime.GOARCH == "amd64" {
		foreign = "arm64"
	}
	if nativeBin(buildFor(t, runtime.GOOS, foreign)) {
		t.Fatalf("nativeBin accepted a %s binary on %s", foreign, runtime.GOARCH)
	}
	if !nativeBin(buildFor(t, runtime.GOOS, runtime.GOARCH)) {
		t.Fatal("nativeBin rejected a native binary")
	}
}

// Scripts and universal binaries have no single arch to compare, and treating
// "cannot tell" as a mismatch would wipe and re-download a working runtime.
func TestNativeBinAllowsUnidentifiableFiles(t *testing.T) {
	p := filepath.Join(t.TempDir(), "script")
	if err := os.WriteFile(p, []byte("#!/bin/sh\necho hi\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	if !nativeBin(p) {
		t.Fatal("nativeBin should accept a format it cannot identify")
	}
}

func TestNativeBinRejectsMissingFile(t *testing.T) {
	if nativeBin(filepath.Join(t.TempDir(), "absent")) {
		t.Fatal("nativeBin accepted a file that does not exist")
	}
}
