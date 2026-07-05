// podcli — native launcher. Reserved verbs are handled here; everything else
// routes to the Python engine.
package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"

	"podcli/internal/backend"
	"podcli/internal/config"
	"podcli/internal/engine"
	"podcli/internal/paths"
	"podcli/internal/podstack"
	"podcli/internal/provision"
	"podcli/internal/update"
)

// Version is set at build time via -ldflags "-X main.Version=...".
var Version = "2.0.0-dev"

func main() {
	args := os.Args[1:]
	if len(args) == 0 {
		os.Exit(runEngine(args)) // backend's branded interactive menu
	}

	switch args[0] {
	case "version", "--version", "-v":
		fmt.Printf("podcli %s\n", Version)
	case "doctor":
		doctor()
	case "update":
		os.Exit(update.Run(Version))
	case "uninstall":
		os.Exit(uninstall(args[1:]))
	case "setup":
		os.Exit(setup(args[1:]))
	case "mcp":
		if len(args) >= 2 && args[1] == "install" {
			os.Exit(mcpInstall())
		}
		code, err := engine.RunMCP()
		if err != nil {
			fmt.Fprintln(os.Stderr, "podcli:", err)
		}
		os.Exit(code)
	case "config":
		if len(args) >= 2 && (args[1] == "get" || args[1] == "set") {
			os.Exit(configCmd(args[1:]))
		}
		os.Exit(runEngine(args)) // status/export/import/use → Python
	case "help", "--help", "-h":
		printHelp()
	default:
		if podstack.IsCommand(args[0]) {
			os.Exit(podstack.Run(args[0], args[1:]))
		}
		os.Exit(runEngine(args))
	}
}

// wantsRuntime gates first-run auto-provisioning: only commands that need the
// backend trigger the download, not lightweight ones like config.
func wantsRuntime(args []string) bool {
	if len(args) == 0 {
		return true
	}
	switch args[0] {
	case "process", "transcribe", "studio", "auto", "ui", "webui":
		return true
	}
	return false
}

// ensureRuntime self-provisions on first run so `podcli` works without a separate
// `podcli setup`. Not called on the mcp path, whose stdout is the JSON-RPC channel.
func ensureRuntime() {
	if _, ok := engine.BackendRoot(); ok {
		return
	}
	fmt.Fprintln(os.Stderr, "First run — setting up podcli (one-time download)…")
	setup(nil)
}

func runEngine(args []string) int {
	update.NotifyIfOutdated(Version)
	if wantsRuntime(args) {
		ensureRuntime()
	}
	if transcribeEngine(args) == "whispercpp" {
		model, err := provision.EnsureModel(transcribeModel(args))
		if err != nil {
			fmt.Fprintln(os.Stderr, "podcli: provisioning model:", err)
			return 1
		}
		os.Setenv("PODCLI_ENGINE", "whispercpp")
		os.Setenv("PODCLI_WHISPERCPP_MODEL", model)
	}
	code, err := engine.Run(args)
	if err != nil {
		fmt.Fprintln(os.Stderr, "podcli:", err)
		return 1
	}
	return code
}

func transcribeModel(args []string) string {
	for _, arg := range args {
		if arg == "--fast" {
			return "tiny.en"
		}
	}
	return "base"
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

// transcribeEngine resolves which engine a run will use, honoring --engine,
// PODCLI_ENGINE, then defaulting to whisper.cpp on a hermetic Python (which has
// no openai-whisper). Covers every entry point that can transcribe: the no-arg
// interactive menu, process, studio, and transcribe.
func transcribeEngine(args []string) string {
	cmd := ""
	if len(args) > 0 {
		cmd = args[0]
	}
	switch cmd {
	case "", "process", "studio", "transcribe":
	default:
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
	speakers := false
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--model":
			if i+1 < len(args) {
				size = args[i+1]
				i++
			}
		case "--vad":
			vad = true
		case "--speakers":
			speakers = true
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
	backendDir := filepath.Join(paths.RuntimeDir(), "backend")
	if err := backend.Extract(backendDir); err != nil {
		fmt.Fprintf(os.Stderr, "  backend: skipped (%v) — falling back to repo/PODCLI_BACKEND\n", err)
		backendDir, _ = engine.BackendRoot()
	} else {
		fmt.Printf("  backend: %s\n", backendDir)
	}
	if backendDir != "" {
		reqs := filepath.Join(backendDir, "requirements-runtime.txt")
		if pb, err := provision.EnsurePython(reqs); err != nil {
			fmt.Fprintf(os.Stderr, "  python: skipped (%v) — using dev venv / system python\n", err)
		} else {
			fmt.Printf("  python: %s\n", pb)
		}
	}
	if speakers {
		if err := provision.EnsureSpeakerDeps(); err != nil {
			fmt.Fprintf(os.Stderr, "  speakers: failed (%v)\n", err)
			return 1
		}
		fmt.Printf("  speakers: pyannote.audio installed (set HF_TOKEN to use)\n")
	}
	if wc, err := provision.EnsureWhisperCpp(); err != nil {
		fmt.Fprintf(os.Stderr, "  whisper: skipped (%v) — backend will use PATH whisper-cli\n", err)
	} else {
		fmt.Printf("  whisper: %s\n", wc)
	}
	if nb, err := provision.EnsureNode(); err != nil {
		fmt.Fprintf(os.Stderr, "  node:    skipped (%v) — Web UI will use system Node if present\n", err)
	} else {
		fmt.Printf("  node:    %s\n", nb)
	}
	if sd, err := provision.EnsureStudio(); err != nil {
		fmt.Fprintf(os.Stderr, "  studio:  skipped (%v) — Web UI needs a published release\n", err)
	} else {
		fmt.Printf("  studio:  %s\n", sd)
	}
	if rd, err := provision.EnsureRemotion(); err != nil {
		fmt.Fprintf(os.Stderr, "  remotion: skipped (%v) — captions/thumbnails need a published release\n", err)
	} else {
		fmt.Printf("  remotion: %s\n", rd)
		if err := provision.PrewarmRemotion(); err != nil {
			fmt.Fprintf(os.Stderr, "  bundle:   deferred to first render (%v)\n", err)
		} else {
			fmt.Printf("  bundle:   prebuilt\n")
		}
		if err := provision.EnsureRemotionBrowser(); err != nil {
			fmt.Fprintf(os.Stderr, "  browser:  deferred to first render (%v)\n", err)
		} else {
			fmt.Printf("  browser:  ready\n")
		}
	}
	if engine.MCPServer() != "" {
		if mcpRegisteredToSelf() {
			fmt.Printf("  mcp:     already registered\n")
		} else if err := registerMCPServer(); err != nil {
			fmt.Fprintf(os.Stderr, "  mcp:     not registered (%v) — run `podcli mcp install`\n", err)
		} else {
			fmt.Printf("  mcp:     registered with Claude Code\n")
		}
	}
	fmt.Println("Done.")
	return 0
}

// registerMCPServer points Claude Code at this binary's `mcp` command. Remove
// first so re-runs refresh a stale path and stay idempotent.
func registerMCPServer() error {
	claude, err := exec.LookPath("claude")
	if err != nil {
		return fmt.Errorf("Claude Code CLI not found on PATH")
	}
	self, err := os.Executable()
	if err != nil {
		return err
	}
	exec.Command(claude, "mcp", "remove", "podcli").Run()
	if out, err := exec.Command(claude, "mcp", "add", "podcli", "--", self, "mcp").CombinedOutput(); err != nil {
		return fmt.Errorf("%v: %s", err, strings.TrimSpace(string(out)))
	}
	return nil
}

func mcpRegisteredToSelf() bool {
	claude, err := exec.LookPath("claude")
	if err != nil {
		return false
	}
	self, err := os.Executable()
	if err != nil {
		return false
	}
	out, err := exec.Command(claude, "mcp", "get", "podcli").CombinedOutput()
	return err == nil && strings.Contains(string(out), self)
}

func mcpInstall() int {
	if err := registerMCPServer(); err != nil {
		self, _ := os.Executable()
		fmt.Fprintf(os.Stderr, "podcli: %v\n", err)
		fmt.Fprintf(os.Stderr, "Register manually:  claude mcp add podcli -- %s mcp\n", self)
		return 1
	}
	fmt.Println("Registered podcli MCP server with Claude Code.")
	return 0
}

func uninstall(args []string) int {
	yes, dryRun := false, false
	for _, a := range args {
		switch a {
		case "--yes", "-y":
			yes = true
		case "--dry-run":
			dryRun = true
		case "--purge":
			// Kept for compatibility; uninstall already removes everything.
		case "--help", "-h":
			printUninstallHelp()
			return 0
		default:
			fmt.Fprintf(os.Stderr, "podcli: unknown uninstall option %q\n", a)
			printUninstallHelp()
			return 2
		}
	}

	home := paths.Home()
	self, _ := os.Executable()
	managed := filepath.Join(paths.BinDir(), "podcli"+paths.ExeSuffix())
	if !pathContains(paths.BinDir(), self) {
		self = ""
	}
	targets := uninstallTargets(home)
	links := podcliLinks(managed, self)

	fmt.Println("podcli uninstall")
	fmt.Printf("  This will remove podcli and all managed data under: %s\n", home)
	for _, p := range targets {
		fmt.Printf("  remove: %s\n", p)
	}
	for _, p := range links {
		fmt.Printf("  unlink: %s\n", p)
	}
	if runtime.GOOS == "windows" {
		fmt.Printf("  remove from user PATH: %s\n", paths.BinDir())
	}
	if dryRun {
		fmt.Println("Dry run only - nothing removed.")
		return 0
	}
	if !yes && !confirm("Continue? [y/N] ") {
		fmt.Println("Cancelled.")
		return 0
	}

	for _, p := range links {
		if err := os.Remove(p); err != nil && !os.IsNotExist(err) {
			fmt.Fprintf(os.Stderr, "  warning: could not remove %s: %v\n", p, err)
		}
	}
	if runtime.GOOS == "windows" {
		if removed, err := removeFromWindowsUserPath(paths.BinDir()); err != nil {
			fmt.Fprintf(os.Stderr, "  warning: could not remove %s from user PATH: %v\n", paths.BinDir(), err)
		} else if removed {
			fmt.Println("  removed from user PATH (restart your terminal)")
		}
	}
	runningInUse := false
	for _, p := range targets {
		if runtime.GOOS == "windows" && pathContains(p, self) {
			runningInUse = true
			if err := removeAllExcept(p, self); err != nil {
				fmt.Fprintf(os.Stderr, "  warning: could not remove %s: %v\n", p, err)
			}
			continue
		}
		if err := os.RemoveAll(p); err != nil {
			fmt.Fprintf(os.Stderr, "  warning: could not remove %s: %v\n", p, err)
		}
	}
	if runningInUse {
		fmt.Fprintf(os.Stderr, "  note: the running binary is still in use and was left in place: %s\n", self)
		fmt.Fprintln(os.Stderr, "        Delete it after this command exits, or run the installer script with --uninstall.")
	}
	fmt.Println("Done - podcli app files were removed.")
	return 0
}

func uninstallTargets(home string) []string {
	return []string{home}
}

func podcliLinks(managed, self string) []string {
	// A binary running from outside the managed bin dir is user-installed;
	// links pointing at it must survive the uninstall.
	if !pathContains(paths.BinDir(), self) {
		self = ""
	}
	var out []string
	for _, d := range []string{"/usr/local/bin", filepath.Join(os.Getenv("HOME"), ".local", "bin")} {
		if d == "/usr/local/bin" && paths.ExeSuffix() == ".exe" {
			continue
		}
		p := filepath.Join(d, "podcli"+paths.ExeSuffix())
		if linkPointsTo(p, managed) || (self != "" && linkPointsTo(p, self)) {
			out = append(out, p)
		}
	}
	return out
}

func removeFromWindowsUserPath(remove string) (bool, error) {
	ps := `$remove = [IO.Path]::GetFullPath($env:PODCLI_REMOVE_PATH).TrimEnd('\')
$path = [Environment]::GetEnvironmentVariable('Path', 'User')
$parts = @($path -split ';' | Where-Object { $_ })
$kept = @($parts | Where-Object {
  try { [IO.Path]::GetFullPath($_).TrimEnd('\') -ine $remove } catch { $_.TrimEnd('\') -ine $env:PODCLI_REMOVE_PATH.TrimEnd('\') }
})
if ($kept.Count -eq $parts.Count) { exit 2 }
[Environment]::SetEnvironmentVariable('Path', ($kept -join ';'), 'User')`
	cmd := exec.Command("powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps)
	cmd.Env = append(os.Environ(), "PODCLI_REMOVE_PATH="+remove)
	if err := cmd.Run(); err != nil {
		if exit, ok := err.(*exec.ExitError); ok && exit.ExitCode() == 2 {
			return false, nil
		}
		return false, err
	}
	return true, nil
}

func pathContains(dir, file string) bool {
	if file == "" {
		return false
	}
	rel, err := filepath.Rel(filepath.Clean(dir), filepath.Clean(file))
	return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))
}

func removeAllExcept(root, keep string) error {
	var paths []string
	if err := filepath.WalkDir(root, func(p string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		paths = append(paths, p)
		return nil
	}); err != nil {
		return err
	}
	for i := len(paths) - 1; i >= 0; i-- {
		p := paths[i]
		if pathContains(p, keep) {
			continue
		}
		if err := os.RemoveAll(p); err != nil && !os.IsNotExist(err) {
			return err
		}
	}
	return nil
}

func linkPointsTo(link, target string) bool {
	dest, err := os.Readlink(link)
	if err != nil {
		return false
	}
	if !filepath.IsAbs(dest) {
		dest = filepath.Join(filepath.Dir(link), dest)
	}
	return filepath.Clean(dest) == filepath.Clean(target)
}

func confirm(prompt string) bool {
	fmt.Print(prompt)
	var s string
	if _, err := fmt.Scanln(&s); err != nil {
		return false
	}
	s = strings.ToLower(strings.TrimSpace(s))
	return s == "y" || s == "yes"
}

func printUninstallHelp() {
	fmt.Println(`Usage: podcli uninstall [--yes] [--dry-run] [--purge]

Removes podcli's managed folder, user data, and installer-created links.

Options:
  -y, --yes     Do not prompt for confirmation
  --dry-run     Show what would be removed without deleting anything
  --purge       Kept for compatibility; uninstall already removes everything`)
}

func doctor() {
	fmt.Printf("podcli %s\n\n", Version)
	fmt.Println("Paths")
	fmt.Printf("  home:     %s\n", paths.Home())
	fmt.Printf("  runtime:  %s\n", paths.RuntimeDir())
	fmt.Printf("  models:   %s\n", paths.ModelsDir())
	fmt.Printf("  presets/knowledge/assets/history/cache: %s  (global — follow you everywhere)\n", paths.Home())
	if cwd, err := os.Getwd(); err == nil {
		fmt.Printf("  clips:    %s  (rendered into your working directory)\n", filepath.Join(cwd, "podcli-clips"))
	}
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
	if wc := engine.WhisperCLI(); wc != "" {
		fmt.Printf("  whisper:  %s (hermetic)\n", wc)
	} else {
		fmt.Printf("  whisper:  PATH fallback (install whisper-cli, or provisioned once hosted)\n")
	}
	if nd := engine.Node(); nd != "" {
		fmt.Printf("  node:     %s (hermetic)\n", nd)
	} else {
		fmt.Printf("  node:     PATH fallback (Web UI uses system Node, or run `podcli setup`)\n")
	}
	if ss := engine.StudioServer(); ss != "" {
		fmt.Printf("  studio:   %s\n", ss)
	} else {
		fmt.Printf("  studio:   not provisioned (Web UI needs a published release)\n")
	}
	if ms := engine.MCPServer(); ms != "" {
		fmt.Printf("  mcp:      %s\n", ms)
	} else {
		fmt.Printf("  mcp:      not provisioned (needs a published release)\n")
	}
	if rs := provision.RemotionScript(); fileExists(rs) {
		fmt.Printf("  remotion: %s\n", rs)
	} else {
		fmt.Printf("  remotion: not provisioned (captions/thumbnails need a published release)\n")
	}
	fmt.Println("\nModels")
	fmt.Printf("  base:     %s\n", presence(provision.ModelPath("base")))
	fmt.Printf("  vad:      %s\n", presence(provision.VADModelPath()))
}

func presence(p string) string {
	if fi, err := os.Stat(p); err == nil && fi.Size() > 0 {
		return fmt.Sprintf("%s (%s)", p, humanBytes(fi.Size()))
	}
	return "not provisioned — run `podcli setup`"
}

func fileExists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

func humanBytes(n int64) string {
	switch {
	case n >= 1<<20:
		return fmt.Sprintf("%d MB", n>>20)
	case n >= 1<<10:
		return fmt.Sprintf("%d KB", n>>10)
	default:
		return fmt.Sprintf("%d B", n)
	}
}

func printHelp() {
	fmt.Printf(`podcli %s — AI podcast clip generator

Usage:
  podcli <command> [args]

Engine commands (routed to the processing backend):
  process <video>      Transcribe a video and export short-form clips
  ui                   Open the Studio web dashboard (http://localhost:3847)
  studio <video>       Cut a fragment + intro/outro bookends
  clips                Browse and edit saved clips
  thumbnails           Generate thumbnails
  knowledge | presets | assets | youtube | config | cache | info

PodStack commands (run inside Claude Code / Codex):
  auto <video>         One-verb pipeline: drop footage, get rendered clips
  generate-titles | generate-descriptions | plan-thumbnails | plan-episode
  process-transcript | produce-shorts | review-content | publish-checklist
  retro-episode        Add --codex / --claude to pick the agent

Launcher commands:
  doctor               Show resolved paths, interpreter, backend, ffmpeg, models
  version              Print version
  update               Check for and apply a newer release
  uninstall            Remove podcli app files (keeps user data unless --purge)
  setup [--model base] [--vad] [--speakers]
                       Provision runtimes + models (--speakers adds pyannote+torch, ~2GB)
  mcp                  Run the MCP server (stdio) for Claude/Codex
  mcp install          Register the MCP server with Claude Code
  config set update.auto off    Disable auto-update (also: PODCLI_NO_UPDATE=1)
  config get update.auto

Run a command with --help for its options.
`, Version)
}
