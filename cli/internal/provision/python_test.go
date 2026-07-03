package provision

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestPythonHealthyRequiresStdlibEncodings(t *testing.T) {
	home := t.TempDir()
	t.Setenv("PODCLI_HOME", home)
	bin := PythonBin()
	if err := os.MkdirAll(filepath.Dir(bin), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(bin, []byte("x"), 0o755); err != nil {
		t.Fatal(err)
	}
	if pythonHealthy(bin) {
		t.Fatal("python without encodings should be unhealthy")
	}

	encodings := filepath.Join(pythonRoot(bin), "lib", "python3.12", "encodings", "__init__.py")
	if runtime.GOOS == "windows" {
		encodings = filepath.Join(pythonRoot(bin), "Lib", "encodings", "__init__.py")
	}
	if err := os.MkdirAll(filepath.Dir(encodings), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(encodings, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if !pythonHealthy(bin) {
		t.Fatal("python with encodings should be healthy")
	}
}
