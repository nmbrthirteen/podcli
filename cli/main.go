// podcli — native launcher. Reserved verbs are handled here; everything else
// routes to the Python engine.
package main

import (
	"fmt"
	"os"
	"strings"

	"podcli/internal/engine"
	"podcli/internal/paths"
	"podcli/internal/provision"
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
		os.Exit(setup(args[1:]))
	case "help", "--help", "-h":
		printHelp()
	default:
		if usesWhisperCpp(args) {
			if _, err := provision.EnsureModel("base"); err != nil {
				fmt.Fprintln(os.Stderr, "podcli: provisioning model:", err)
				os.Exit(1)
			}
		}
		code, err := engine.Run(args)
		if err != nil {
			fmt.Fprintln(os.Stderr, "podcli:", err)
			os.Exit(1)
		}
		os.Exit(code)
	}
}

func usesWhisperCpp(args []string) bool {
	cmd := args[0]
	if cmd != "process" && cmd != "studio" {
		return false
	}
	engineSel := strings.ToLower(os.Getenv("PODCLI_ENGINE"))
	for i, a := range args {
		if a == "--engine" && i+1 < len(args) {
			engineSel = strings.ToLower(args[i+1])
		} else if strings.HasPrefix(a, "--engine=") {
			engineSel = strings.ToLower(strings.TrimPrefix(a, "--engine="))
		}
	}
	switch engineSel {
	case "whispercpp", "whisper-cpp", "whisper.cpp", "cpp":
		return true
	}
	return false
}

func setup(args []string) int {
	size := "base"
	vad := false
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--model":
			if i+1 < len(args) {
				size = args[i+1]
				i++
			}
		case "--vad":
			vad = true
		}
	}
	fmt.Printf("Provisioning into %s\n", paths.Home())
	p, err := provision.EnsureModel(size)
	if err != nil {
		fmt.Fprintln(os.Stderr, "podcli: setup:", err)
		return 1
	}
	fmt.Printf("  model:  %s\n", p)
	if vad {
		vp, err := provision.EnsureVADModel()
		if err != nil {
			fmt.Fprintln(os.Stderr, "podcli: setup:", err)
			return 1
		}
		fmt.Printf("  vad:    %s\n", vp)
	}
	if fp, err := provision.EnsureFFmpeg(); err != nil {
		fmt.Fprintf(os.Stderr, "  ffmpeg: skipped (%v) — backend will use PATH ffmpeg\n", err)
	} else {
		fmt.Printf("  ffmpeg: %s\n", fp)
	}
	fmt.Println("Done. (hermetic python/whisper.cpp provisioning lands in a later phase)")
	return 0
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
	if fp := engine.FFprobe(); fp != "" {
		fmt.Printf("  ffprobe:  %s (hermetic)\n", fp)
	}
	fmt.Println("\nModels")
	fmt.Printf("  base:     %s\n", presence(provision.ModelPath("base")))
	fmt.Printf("  vad:      %s\n", presence(provision.VADModelPath()))
}

func presence(p string) string {
	if fi, err := os.Stat(p); err == nil && fi.Size() > 0 {
		return fmt.Sprintf("%s (%d MB)", p, fi.Size()>>20)
	}
	return "not provisioned — run `podcli setup`"
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
  setup [--model base] [--vad]
                       Provision models into the managed dir (runtimes: later phase)

Run a command with --help for its options.
`, Version)
}
