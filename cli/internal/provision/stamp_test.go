package provision

import (
	"os"
	"testing"
)

func TestBundleStampGatesRefetch(t *testing.T) {
	dir := t.TempDir()

	if bundleAt(dir, "2.3.5") {
		t.Fatal("unstamped bundle must not be reported as current")
	}

	writeBundleStamp(dir, "2.3.5")
	if !bundleAt(dir, "2.3.5") {
		t.Fatal("bundle stamped at the running version must be current")
	}
	if bundleAt(dir, "2.3.6") {
		t.Fatal("bundle stamped at an older version must be refetched")
	}

	writeBundleStamp(dir, "2.3.6")
	if !bundleAt(dir, "2.3.6") {
		t.Fatal("re-stamp must move the bundle to the new version")
	}

	if _, err := os.Stat(bundleStamp(dir)); err != nil {
		t.Fatalf("stamp file should exist: %v", err)
	}
}
