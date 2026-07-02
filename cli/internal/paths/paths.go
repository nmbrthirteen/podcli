// Package paths resolves the global managed directory where podcli keeps its
// hermetic runtimes, models, binaries, and config. Per-project working data
// (.podcli/) stays in the user's current directory and is not handled here.
package paths

import (
	"os"
	"path/filepath"
	"runtime"
)

// Home is the global managed dir. Override with PODCLI_HOME (used by tests and
// power users). Layout matches plans/native-cli.md:
//
//	darwin  ~/Library/Application Support/podcli
//	windows %LOCALAPPDATA%\podcli
//	linux   $XDG_DATA_HOME/podcli  (or ~/.local/share/podcli)
func Home() string {
	if h := os.Getenv("PODCLI_HOME"); h != "" {
		return h
	}
	home, err := os.UserHomeDir()
	if err != nil {
		home = "."
	}
	switch runtime.GOOS {
	case "darwin":
		return filepath.Join(home, "Library", "Application Support", "podcli")
	case "windows":
		if d := os.Getenv("LOCALAPPDATA"); d != "" {
			return filepath.Join(d, "podcli")
		}
		return filepath.Join(home, "AppData", "Local", "podcli")
	default:
		if d := os.Getenv("XDG_DATA_HOME"); d != "" {
			return filepath.Join(d, "podcli")
		}
		return filepath.Join(home, ".local", "share", "podcli")
	}
}

func RuntimeDir() string { return filepath.Join(Home(), "runtime") }
func ModelsDir() string  { return filepath.Join(Home(), "models") }
func BinDir() string     { return filepath.Join(Home(), "bin") }
func ConfigPath() string { return filepath.Join(Home(), "config.json") }

func ExeSuffix() string {
	if runtime.GOOS == "windows" {
		return ".exe"
	}
	return ""
}
