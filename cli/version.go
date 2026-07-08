package main

import (
	_ "embed"
	"strings"
)

//go:generate sh gen-version.sh
//go:embed VERSION
var versionFile string

// Version is generated from package.json by gen-version.sh, so a release bumps one
// file. It is deliberately not wired to -ldflags "-X main.Version": the linker
// applies -X only to a constant-initialized string, and silently ignores it here.
var Version = strings.TrimSpace(versionFile)
