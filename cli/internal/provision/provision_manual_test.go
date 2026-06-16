package provision

import (
	"os"
	"os/exec"
	"strings"
	"testing"

	"podcli/internal/paths"
)

// Guarded network integration check (set PODCLI_MANUAL=1). Validates GitHub-API
// asset resolution, tar.gz extraction (incl. symlinks), and that the provisioned
// interpreter runs. PODCLI_PY_ARCHIVE=<file> extracts a local tarball instead of
// downloading; PODCLI_REQS also tests pip install.
func TestEnsurePythonManual(t *testing.T) {
	if os.Getenv("PODCLI_MANUAL") == "" {
		t.Skip("set PODCLI_MANUAL=1 to run")
	}
	if arc := os.Getenv("PODCLI_PY_ARCHIVE"); arc != "" {
		if err := os.MkdirAll(paths.RuntimeDir(), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := extractTarGz(arc, paths.RuntimeDir()); err != nil {
			t.Fatal(err)
		}
		assertRuns(t, PythonBin())
		return
	}
	bin, err := EnsurePython(os.Getenv("PODCLI_REQS"))
	if err != nil {
		t.Fatal(err)
	}
	assertRuns(t, bin)
}

func assertRuns(t *testing.T, bin string) {
	out, err := exec.Command(bin, "--version").CombinedOutput()
	if err != nil {
		t.Fatal(err)
	}
	v := strings.TrimSpace(string(out))
	t.Logf("python %s -> %s", bin, v)
	if !strings.HasPrefix(v, "Python 3.") {
		t.Fatalf("unexpected version: %q", v)
	}
}
