// podcli — native launcher.
//
// Phase 0: resolves the Python backend + interpreter and routes subcommands to
// it (replacing the bash `podcli` and install.cmd). Reserved launcher verbs
// (version, doctor, update, setup) are handled here; everything else is passed
// through to the engine. update/setup are stubs until Phases 0+/2.
package main

import (
	"fmt"
	"os"

	"podcli/internal/engine"
	"podcli/internal/paths"
)

// Version is set at build time via -ldflags "-X main.Version=...".
var Version = "2.0.0-dev"

func main() {
	args := os.Args[1:]
	if len(args) == 0 {
		printHelp()
		return
	}

	switch args[0] {
	case "version", "--version", "-v":
		fmt.Printf("podcli %s\n", Version)
	case "doctor":
		doctor()
	case "update":
		fmt.Println("self-update: not yet implemented (Phase 2 — GitHub Releases + atomic swap)")
	case "setup":
		fmt.Println("hermetic provisioning: not yet implemented (Phase 0+ — fetch pinned python/ffmpeg/whisper.cpp)")
	case "help", "--help", "-h":
		printHelp()
	default:
		code, err := engine.Run(args)
		if err != nil {
			fmt.Fprintln(os.Stderr, "podcli:", err)
			os.Exit(1)
		}
		os.Exit(code)
	}
}

func doctor() {
	fmt.Printf("podcli %s\n\n", Version)
	fmt.Println("Paths")
	fmt.Printf("  home:     %s\n", paths.Home())
	fmt.Printf("  runtime:  %s\n", paths.RuntimeDir())
	fmt.Printf("  models:   %s\n", paths.ModelsDir())
	fmt.Println("\nEngine resolution")
	if root, ok := engine.BackendRoot(); ok {
		fmt.Printf("  backend:  %s\n", root)
	} else {
		fmt.Printf("  backend:  NOT FOUND (set PODCLI_BACKEND or run inside the repo)\n")
	}
	fmt.Printf("  python:   %s\n", engine.Python())
	if ff := engine.FFmpeg(); ff != "" {
		fmt.Printf("  ffmpeg:   %s (hermetic)\n", ff)
	} else {
		fmt.Printf("  ffmpeg:   PATH fallback (not yet hermetic)\n")
	}
}

func printHelp() {
	fmt.Printf(`podcli %s — AI podcast clip generator

Usage:
  podcli <command> [args]

Engine commands (routed to the processing backend):
  process <video>      Transcribe a video and export short-form clips
  studio <video>       Cut a fragment + intro/outro bookends
  clips                Browse and edit saved clips
  thumbnails           Generate thumbnails
  knowledge | presets | assets | youtube | config | cache | info

Launcher commands:
  doctor               Show resolved paths, interpreter, backend, ffmpeg
  version              Print version
  update               Self-update (coming in Phase 2)
  setup                Provision hermetic runtimes (coming soon)

Run a command with --help for its options.
`, Version)
}
