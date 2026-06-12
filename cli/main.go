// podcli — native launcher. Reserved verbs are handled here; everything else
// routes to the Python engine.
package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"podcli/internal/config"
	"podcli/internal/engine"
	"podcli/internal/paths"
	"podcli/internal/provision"
	"podcli/internal/update"
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
		os.Exit(update.Run(Version))
	case "setup":
		os.Exit(setup(args[1:]))
	case "config":
		if len(args) >= 2 && (args[1] == "get" || args[1] == "set") {
			os.Exit(configCmd(args[1:]))
		}
		os.Exit(runEngine(args)) // status/export/import/use → Python
	case "help", "--help", "-h":
		printHelp()
	default:
		os.Exit(runEngine(args))
	}
}

func runEngine(args []string) int {
	update.NotifyIfOutdated(Version)
	if transcribeEngine(args) == "whispercpp" {
		if _, err := provision.EnsureModel("base"); err != nil {
			fmt.Fprintln(os.Stderr, "podcli: provisioning model:", err)
			return 1
		}
		os.Setenv("PODCLI_ENGINE", "whispercpp")
	}
	code, err := engine.Run(args)
	if err != nil {
		fmt.Fprintln(os.Stderr, "podcli:", err)
		return 1
	}
	return code
}

func configCmd(args []string) int {
	switch {
	case args[0] == "get" && len(args) == 2:
		v, err := config.Get(args[1])
		if err != nil {
			fmt.Fprintln(os.Stderr, "podcli:", err)
			return 1
		}
		fmt.Println(v)
	case args[0] == "set" && len(args) == 3:
		if err := config.Set(args[1], args[2]); err != nil {
			fmt.Fprintln(os.Stderr, "podcli:", err)
			return 1
		}
		fmt.Printf("%s = %s\n", args[1], args[2])
	default:
		fmt.Fprintln(os.Stderr, "usage: podcli config get <key> | config set <key> <value>")
		return 2
	}
	return 0
}

// transcribeEngine resolves which engine a process/studio run will use, honoring
// --engine, PODCLI_ENGINE, then defaulting to whisper.cpp on a hermetic Python
// (which has no openai-whisper).
func transcribeEngine(args []string) string {
	if args[0] != "process" && args[0] != "studio" {
		return ""
	}
	sel := strings.ToLower(os.Getenv("PODCLI_ENGINE"))
	for i, a := range args {
		if a == "--engine" && i+1 < len(args) {
			sel = strings.ToLower(args[i+1])
		} else if strings.HasPrefix(a, "--engine=") {
			sel = strings.ToLower(strings.TrimPrefix(a, "--engine="))
		}
	}
	if sel == "" && engine.IsHermeticPython() {
		sel = "whispercpp"
	}
	switch sel {
	case "whispercpp", "whisper-cpp", "whisper.cpp", "cpp":
		return "whispercpp"
	}
	return sel
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
	if root, ok := engine.BackendRoot(); ok {
		reqs := filepath.Join(root, "requirements-runtime.txt")
		if pb, err := provision.EnsurePython(reqs); err != nil {
			fmt.Fprintf(os.Stderr, "  python: skipped (%v) — using dev venv / system python\n", err)
		} else {
			fmt.Printf("  python: %s\n", pb)
		}
	}
	fmt.Println("Done. (whisper.cpp binary provisioning lands once podcli hosts builds)")
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
  doctor               Show resolved paths, interpreter, backend, ffmpeg, models
  version              Print version
  update               Check for and apply a newer release
  setup [--model base] [--vad]
                       Provision runtimes + models into the managed dir
  config set update.auto off    Disable auto-update (also: PODCLI_NO_UPDATE=1)
  config get update.auto

Run a command with --help for its options.
`, Version)
}
