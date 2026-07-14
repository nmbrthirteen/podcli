package provision

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"sync/atomic"
	"testing"
)

func writeHealthyPython(t *testing.T) string {
	t.Helper()
	bin := PythonBin()
	if err := os.MkdirAll(filepath.Dir(bin), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(bin, []byte("python"), 0o755); err != nil {
		t.Fatal(err)
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
	return bin
}

// An installed runtime must not depend on the network. Resolving the upstream release
// on every run made podcli unusable offline, spent a rate-limited GitHub API call per
// invocation, and re-provisioned whenever upstream published a new build.
func TestEnsurePythonSkipsNetworkForInstalledRuntime(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()
	t.Cleanup(func(orig string) func() {
		return func() { pythonReleasesAPI = orig }
	}(pythonReleasesAPI))
	pythonReleasesAPI = srv.URL

	t.Setenv("PODCLI_HOME", t.TempDir())
	bin := writeHealthyPython(t)

	// A runtime installed before artifact state existed is adopted, not re-downloaded.
	if _, err := EnsurePython(""); err != nil {
		t.Fatalf("installed runtime should not need the network: %v", err)
	}
	if got := hits.Load(); got != 0 {
		t.Fatalf("release API called %d times for an installed runtime, want 0", got)
	}

	// And again, now that state is recorded.
	if _, err := EnsurePython(""); err != nil {
		t.Fatalf("second run should not need the network: %v", err)
	}
	if got := hits.Load(); got != 0 {
		t.Fatalf("release API called %d times on re-run, want 0", got)
	}

	// A swapped binary is still rejected: the local check verifies content, it does not
	// merely note the file exists.
	if err := os.WriteFile(bin, []byte("tampered"), 0o755); err != nil {
		t.Fatal(err)
	}
	if _, err := EnsurePython(""); err == nil {
		t.Fatal("tampered python runtime was trusted")
	}
	if hits.Load() == 0 {
		t.Fatal("tampered python runtime did not trigger re-provisioning")
	}
}

// `podcli setup` is the one path that may re-provision, so it is the one path that
// still asks upstream what the current build is.
func TestSetupStillVerifiesAgainstUpstream(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()
	t.Cleanup(func(orig string) func() {
		return func() { pythonReleasesAPI = orig }
	}(pythonReleasesAPI))
	pythonReleasesAPI = srv.URL

	t.Setenv("PODCLI_HOME", t.TempDir())
	writeHealthyPython(t)
	if _, err := EnsurePython(""); err != nil {
		t.Fatal(err)
	}
	if hits.Load() != 0 {
		t.Fatal("baseline run should not have hit the network")
	}

	VerifyRemote = true
	t.Cleanup(func() { VerifyRemote = false })
	if _, err := EnsurePython(""); err == nil {
		t.Fatal("setup should surface the upstream lookup failure")
	}
	if hits.Load() == 0 {
		t.Fatal("setup did not re-verify against upstream")
	}
}
