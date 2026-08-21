//go:build !windows

package main

import (
	"os"
	"path/filepath"
	"syscall"
)

// homeHasRootOwnedFiles reports whether a previous sudo run already took the
// directory. Checks only the entries a run creates first, so the refusal is not
// delayed by walking a multi-gigabyte install.
func homeHasRootOwnedFiles(home string) bool {
	for _, p := range []string{home, filepath.Join(home, "runtime"), filepath.Join(home, "models")} {
		info, err := os.Stat(p)
		if err != nil {
			continue
		}
		if st, ok := info.Sys().(*syscall.Stat_t); ok && st.Uid == 0 {
			return true
		}
	}
	return false
}
