//go:build windows

package main

// Windows has no sudo, and SUDO_USER is never set by its shells.
func homeHasRootOwnedFiles(string) bool { return false }
