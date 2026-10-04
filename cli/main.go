// podcli - native launcher. Reserved verbs are handled here; everything else
// routes to the Python engine.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"

	"podcli/internal/backend"
	"podcli/internal/config"
	"podcli/internal/engine"
	"podcli/internal/paths"
	"podcli/internal/podstack"
	"podcli/internal/provision"
	"podcli/internal/update"
)

func main() {
	os.Setenv("PODCLI_VERSION", Version)
	args := os.Args[1:]
	refuseSudo(args)
	update.CleanupOldBinary()
	if len(args) == 0 {
		os.Exit(runEngine(args)) // backend's branded interactive menu
	}

	switch args[0] {
	case "version", "--version", "-v":
		fmt.Printf("podcli %s\n", Version)
	case "doctor":
		os.Exit(doctor(args[1:]))
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
		// Safe on this path despite stdout being the JSON-RPC channel: it only writes
		// to stderr. Skipping it would strand MCP-only clients on a stale backend.
		if err := refreshBackend(); err != nil {
			fmt.Fprintln(os.Stderr, "podcli:", err)
			os.Exit(1)
		}
		code, err := engine.RunMCP()
		if err != nil {
			fmt.Fprintln(os.Stderr, "podcli:", err)
		}
		os.Exit(code)
	case "sync":
		code, err := engine.RunSync()
		if err != nil {
			fmt.Fprintln(os.Stderr, "podcli:", err)
		}
		os.Exit(code)
	case "config":
		if len(args) >= 2 && (args[1] == "get" || args[1] == "set") {
			os.Exit(configCmd(args[1:]))
		}
		os.Exit(runEngine(args)) // status/export/import/use to Python
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
	case "process", "transcribe", "studio", "multicam", "auto", "ui", "webui":
		return true
	}
	return false
}

func wantsStudio(args []string) bool {
	if len(args) == 0 {
		return true
	}
	switch args[0] {
	case "studio", "ui", "webui":
		return true
	}
	return false
}

func refreshStudioBundles() {
	if _, err := provision.EnsureStudio(Version); err != nil {
		fmt.Fprintf(os.Stderr, "  studio: not refreshed (%v)\n", err)
	}
	if _, err := provision.EnsureRemotion(Version); err != nil {
		fmt.Fprintf(os.Stderr, "  remotion: not refreshed (%v)\n", err)
	}
}

// ensureRuntime self-provisions on first run so `podcli` works without a separate
// `podcli setup`. Not called on the mcp path, whose stdout is the JSON-RPC channel;
// that path calls refreshBackend directly, which only writes to stderr.
// setupDoneStamp marks a setup that ran to the end, whatever it had to skip.
// Setup extracts the backend before any download, so a backend alone does not
// prove the runtime arrived.
func setupDoneStamp() string { return filepath.Join(paths.RuntimeDir(), ".setup-complete") }

// setupInterrupted reports a managed install whose first setup stopped after
// the backend landed but before the Python runtime did, e.g. a Ctrl-C during
// the model download. Without this check every later run trusted the backend
// and ran it on whatever python3 was on PATH.
func setupInterrupted() bool {
	root, ok := engine.BackendRoot()
	if !ok || root != filepath.Join(paths.RuntimeDir(), "backend") || os.Getenv("PODCLI_PYTHON") != "" {
		return false
	}
	return !fileExists(setupDoneStamp()) && engine.Python() == "python3"
}

func ensureRuntime() error {
	_, ok := engine.BackendRoot()
	if interrupted := ok && setupInterrupted(); !ok || interrupted {
		if interrupted {
			fmt.Fprintln(os.Stderr, "Finishing an interrupted podcli setup...")
		} else {
			fmt.Fprintln(os.Stderr, "First run - setting up podcli (one-time download)...")
		}
		// setup reports on stdout, which belongs to the command being run here:
		// `--json` callers parse it as exactly one JSON object.
		stdout := os.Stdout
		os.Stdout = os.Stderr
		ok := setup(nil) == 0
		os.Stdout = stdout
		if !ok {
			return fmt.Errorf("first-run setup failed (see errors above) - run `podcli setup` to retry")
		}
	} else if err := refreshBackend(); err != nil {
		return err
	}
	return ensureBackendDeps()
}

// refreshBackend re-extracts the embedded backend after a launcher upgrade, since
// `podcli update` replaces only the binary. Must run before ensureBackendDeps: the
// dep stamp hashes the backend's own requirements-runtime.txt, so a stale tree
// pins a stale dep set.
func refreshBackend() error {
	managed := filepath.Join(paths.RuntimeDir(), "backend")
	root, _ := engine.BackendRoot()
	if root != managed {
		return nil // repo checkout or PODCLI_BACKEND override: the user's tree, not ours
	}
	if backend.IsCurrent(managed, Version) {
		return nil
	}
	fmt.Fprintf(os.Stderr, "Updating backend to podcli %s...\n", Version)
	if err := backend.Extract(managed, Version); err != nil {
		return fmt.Errorf("could not update the Python backend: %w\n  run `podcli setup --refresh` to retry", err)
	}
	return nil
}

func ensureBackendDeps() error {
	if !engine.IsHermeticPython() {
		return nil
	}
	root, ok := engine.BackendRoot()
	if !ok {
		return nil
	}
	reqs := filepath.Join(root, "requirements-runtime.txt")
	if _, err := os.Stat(reqs); err != nil {
		return nil
	}
	if _, err := provision.EnsurePython(reqs); err != nil {
		return fmt.Errorf("could not install Python dependencies: %w\n  run `podcli setup` to retry", err)
	}
	return nil
}

func runEngine(args []string) int {
	update.NotifyIfOutdated(Version)
	if wantsRuntime(args) {
		if err := ensureRuntime(); err != nil {
			fmt.Fprintln(os.Stderr, "podcli:", err)
			return 1
		}
	} else if err := refreshBackend(); err != nil {
		fmt.Fprintln(os.Stderr, "podcli:", err)
		return 1
	}
	if wantsStudio(args) {
		refreshStudioBundles()
	}
	switch transcribeEngine(args) {
	case "whispercpp":
		model, err := provision.EnsureModel(transcribeModel(args))
		if err != nil {
			fmt.Fprintln(os.Stderr, "podcli: provisioning model:", err)
			return 1
		}
		os.Setenv("PODCLI_ENGINE", "whispercpp")
		os.Setenv("PODCLI_WHISPERCPP_MODEL", model)
	case "omnilingual":
		model, tokens, err := provision.EnsureOmnilingualModel()
		if err != nil {
			fmt.Fprintln(os.Stderr, "podcli: provisioning omnilingual model:", err)
			return 1
		}
		os.Setenv("PODCLI_ENGINE", "omnilingual")
		os.Setenv("PODCLI_OMNILINGUAL_MODEL", model)
		os.Setenv("PODCLI_OMNILINGUAL_TOKENS", tokens)
	}
	code, err := engine.Run(args)
	if err != nil {
		fmt.Fprintln(os.Stderr, "podcli:", err)
		return 1
	}
	return code
}

func transcribeModel(args []string) string {
	fast := false
	for _, arg := range args {
		if arg == "--fast" {
			fast = true
			break
		}
	}
	if !fast {
		return "base"
	}
	// tiny.en is English-only; unset language runs auto-detection, which
	// needs the multilingual tiny model same as any non-English request.
	lang := strings.ToLower(transcribeLanguage(args))
	if lang == "en" || lang == "english" {
		return "tiny.en"
	}
	return "tiny"
}

// transcribeLanguage extracts --language/--language=<value> the same way
// transcribeEngine extracts --engine.
func transcribeLanguage(args []string) string {
	lang := ""
	for i, a := range args {
		if a == "--language" && i+1 < len(args) {
			lang = args[i+1]
		} else if strings.HasPrefix(a, "--language=") {
			lang = strings.TrimPrefix(a, "--language=")
		}
	}
	return lang
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
	refresh := false
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
		case "--refresh":
			refresh = true
		}
	}
	// setup is the one path that may re-provision, so it is the one path allowed to ask
	// upstream what the current build is. Every other command trusts the local artifact
	// state and stays offline-capable.
	provision.VerifyRemote = true
	fmt.Printf("Provisioning into %s\n", paths.Home())
	// Extracted from the binary, so it costs nothing and can't fail on a bad network.
	// First, so an interrupted or offline setup still leaves a backend matching this
	// launcher rather than the one a previous release installed.
	backendDir := filepath.Join(paths.RuntimeDir(), "backend")
	if err := backend.Extract(backendDir, Version); err != nil {
		fmt.Fprintf(os.Stderr, "  backend: FAILED (%v)\n", err)
		_ = os.RemoveAll(backendDir)
		fallback, ok := engine.BackendRoot()
		if !ok {
			fmt.Fprintln(os.Stderr, "podcli: setup: no backend available after extract failure")
			return 1
		}
		backendDir = fallback
		fmt.Fprintf(os.Stderr, "  backend: using fallback %s (may be stale — run `podcli doctor`)\n", backendDir)
	} else {
		fmt.Printf("  backend: %s\n", backendDir)
	}
	// --refresh re-provisions what a launcher upgrade invalidates. It must not pull a
	// model: the user's chosen size isn't known here, and defaulting to base would
	// download one they never asked for.
	if !refresh {
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
	}
	if fp, err := provision.EnsureFFmpeg(); err != nil {
		fmt.Fprintf(os.Stderr, "  ffmpeg: skipped (%v) - backend will use PATH ffmpeg\n", err)
	} else {
		fmt.Printf("  ffmpeg: %s\n", fp)
	}
	if backendDir != "" {
		reqs := filepath.Join(backendDir, "requirements-runtime.txt")
		if pb, err := provision.EnsurePython(reqs); err != nil {
			fmt.Fprintf(os.Stderr, "  python: skipped (%v) - using dev venv / system python\n", err)
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
		fmt.Fprintf(os.Stderr, "  whisper: skipped (%v) - backend will use PATH whisper-cli\n", err)
	} else {
		fmt.Printf("  whisper: %s\n", wc)
	}
	if nb, err := provision.EnsureNode(); err != nil {
		fmt.Fprintf(os.Stderr, "  node:    skipped (%v) - Web UI will use system Node if present\n", err)
	} else {
		fmt.Printf("  node:    %s\n", nb)
	}
	if sd, err := provision.EnsureStudio(Version); err != nil {
		fmt.Fprintf(os.Stderr, "  studio:  skipped (%v) - Web UI needs a published release\n", err)
	} else {
		fmt.Printf("  studio:  %s\n", sd)
	}
	if rd, err := provision.EnsureRemotion(Version); err != nil {
		fmt.Fprintf(os.Stderr, "  remotion: skipped (%v) - captions/thumbnails need a published release\n", err)
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
	if err := os.WriteFile(setupDoneStamp(), []byte(Version+"\n"), 0o644); err != nil {
		fmt.Fprintf(os.Stderr, "  setup:   could not record completion (%v)\n", err)
	}
	if engine.MCPServer() != "" {
		if mcpRegisteredToSelf() {
			fmt.Printf("  mcp:     already registered with Claude Code\n")
		} else if _, err := exec.LookPath("claude"); err != nil {
			// Claude MCP registration is optional; Codex users do not need this.
		} else if err := registerMCPServer(); err != nil {
			fmt.Fprintf(os.Stderr, "  mcp:     not registered with Claude Code (%v) - run `podcli mcp install`\n", err)
		} else {
			fmt.Printf("  mcp:     registered with Claude Code\n")
		}

		if codexMCPRegisteredToSelf() {
			fmt.Printf("  mcp:     already registered with Codex\n")
		} else if _, err := exec.LookPath("codex"); err != nil {
			// Codex MCP registration is optional; Claude users do not need this.
		} else if err := registerCodexMCPServer(); err != nil {
			fmt.Fprintf(os.Stderr, "  mcp:     not registered with Codex (%v)\n", err)
		} else {
			fmt.Printf("  mcp:     registered with Codex\n")
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

// registerCodexMCPServer points Codex at this binary's `mcp` command. Unlike
// Claude's `mcp add`, `codex mcp add` overwrites an existing entry by name,
// so no remove-first step is needed to stay idempotent.
func registerCodexMCPServer() error {
	codex, err := exec.LookPath("codex")
	if err != nil {
		return fmt.Errorf("Codex CLI not found on PATH")
	}
	self, err := os.Executable()
	if err != nil {
		return err
	}
	if out, err := exec.Command(codex, "mcp", "add", "podcli", "--", self, "mcp").CombinedOutput(); err != nil {
		return fmt.Errorf("%v: %s", err, strings.TrimSpace(string(out)))
	}
	return nil
}

func codexMCPRegisteredToSelf() bool {
	codex, err := exec.LookPath("codex")
	if err != nil {
		return false
	}
	self, err := os.Executable()
	if err != nil {
		return false
	}
	out, err := exec.Command(codex, "mcp", "get", "podcli").CombinedOutput()
	return err == nil && strings.Contains(string(out), self)
}

func mcpInstall() int {
	self, _ := os.Executable()
	_, claudeErr := exec.LookPath("claude")
	_, codexErr := exec.LookPath("codex")
	registeredAny := false
	failedAny := false

	if claudeErr == nil {
		if err := registerMCPServer(); err != nil {
			fmt.Fprintf(os.Stderr, "podcli: %v\n", err)
			fmt.Fprintf(os.Stderr, "Register manually:  claude mcp add podcli -- %s mcp\n", self)
			failedAny = true
		} else {
			fmt.Println("Registered podcli MCP server with Claude Code.")
			registeredAny = true
		}
	}
	if codexErr == nil {
		if err := registerCodexMCPServer(); err != nil {
			fmt.Fprintf(os.Stderr, "podcli: %v\n", err)
			fmt.Fprintf(os.Stderr, "Register manually:  codex mcp add podcli -- %s mcp\n", self)
			failedAny = true
		} else {
			fmt.Println("Registered podcli MCP server with Codex.")
			registeredAny = true
		}
	}

	if !registeredAny && !failedAny {
		fmt.Fprintln(os.Stderr, "podcli: neither Claude Code nor Codex CLI found on PATH")
		return 1
	}
	if failedAny {
		return 1
	}
	return 0
}

func uninstall(args []string) int {
	yes, dryRun, purge := false, false, false
	for _, a := range args {
		switch a {
		case "--yes", "-y":
			yes = true
		case "--dry-run":
			dryRun = true
		case "--purge":
			purge = true
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
	targets := uninstallTargets(home, purge)
	links := podcliLinks(managed, self)

	fmt.Println("podcli uninstall")
	if purge {
		fmt.Printf("  This will remove podcli and all managed data under: %s\n", home)
	} else {
		fmt.Printf("  This will remove podcli app files under: %s\n", home)
		fmt.Println("  User data (config, knowledge, presets, assets, history, cache) is kept - pass --purge to remove it too.")
	}
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
	if purge {
		fmt.Println("Done - podcli and all managed data were removed.")
	} else {
		fmt.Println("Done - podcli app files were removed (user data kept).")
	}
	return 0
}

// uninstallTargets lists what uninstall removes. By default only the app dirs
// go; user data (config.json, knowledge, presets, assets, history, cache) sits
// directly under home and survives unless --purge removes the whole dir.
func uninstallTargets(home string, purge bool) []string {
	if purge {
		return []string{home}
	}
	return []string{
		filepath.Join(home, "bin"),
		filepath.Join(home, "runtime"),
		filepath.Join(home, "models"),
	}
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
$key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
if (-not $key) { exit 2 }
try { $kind = $key.GetValueKind('Path') } catch { $kind = [Microsoft.Win32.RegistryValueKind]::ExpandString }
$path = [string]$key.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
$parts = @($path -split ';' | Where-Object { $_ })
$kept = @($parts | Where-Object {
  try { [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($_)).TrimEnd('\') -ine $remove } catch { $_.TrimEnd('\') -ine $env:PODCLI_REMOVE_PATH.TrimEnd('\') }
})
if ($kept.Count -eq $parts.Count) { exit 2 }
$key.SetValue('Path', ($kept -join ';'), $kind)`
	cmd := exec.Command("powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps)
	cmd.Env = append(os.Environ(), "PODCLI_REMOVE_PATH="+remove)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		if exit, ok := err.(*exec.ExitError); ok && exit.ExitCode() == 2 {
			return false, nil
		}
		if detail := strings.TrimSpace(stderr.String()); detail != "" {
			return false, fmt.Errorf("%w: %s", err, detail)
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

Removes podcli's app files (bin, runtime, models) and installer-created links.
User data (config, knowledge, presets, assets, history, cache) is kept.

Options:
  -y, --yes     Do not prompt for confirmation
  --dry-run     Show what would be removed without deleting anything
  --purge       Also remove user data (deletes the entire podcli folder)`)
}

// backendStamp annotates an unmanaged or out-of-date backend, the drift the
// launcher version alone can't show: it exports PODCLI_VERSION into the backend,
// so a stale backend reports the launcher's version as its own.
func backendStamp(root string) string {
	if root != filepath.Join(paths.RuntimeDir(), "backend") {
		return "  (unmanaged - repo or PODCLI_BACKEND)"
	}
	stamp := backend.Version(root)
	switch stamp {
	case Version:
		if mismatches := backend.IntegrityMismatches(root); len(mismatches) > 0 {
			return fmt.Sprintf(
				"  (STALE: stamp %s matches launcher but files differ [%s] - run `podcli setup --refresh`)",
				stamp,
				strings.Join(mismatches, ", "),
			)
		}
		return ""
	case "":
		if mismatches := backend.IntegrityMismatches(root); len(mismatches) > 0 {
			return fmt.Sprintf(
				"  (STALE: unstamped, files differ from launcher [%s] - run `podcli setup --refresh`)",
				strings.Join(mismatches, ", "),
			)
		}
		return "  (STALE: unstamped - run `podcli setup --refresh`)"
	default:
		return fmt.Sprintf("  (STALE: stamped %s, launcher %s - run `podcli setup --refresh`)", stamp, Version)
	}
}

// Check severity. "warn" surfaces a problem without counting toward
// doctor's exit code. Used for MCP registration, which is a per-folder
// (Claude) or optional (Codex-only users) setting, not evidence anything is
// broken.
const (
	levelOK   = "ok"
	levelWarn = "warn"
	levelFail = "fail"
)

// doctorCheck is one pass/fail probe doctor actually runs, as opposed to the
// path/engine-resolution report above it, which only states what was found.
type doctorCheck struct {
	Name   string `json:"name"`
	OK     bool   `json:"ok"`
	Level  string `json:"level"` // "ok", "warn", or "fail" - only "fail" counts toward doctor's exit code
	Detail string `json:"detail"`
}

type doctorReport struct {
	Version string            `json:"version"`
	Paths   map[string]string `json:"paths"`
	Checks  []doctorCheck     `json:"checks"`
	OK      bool              `json:"ok"`
}

func firstLine(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.IndexByte(s, '\n'); i != -1 {
		s = s[:i]
	}
	return s
}

// runCheck resolves a hermetic binary (falling back to PATH), then actually
// runs it. A binary that resolves but fails to run (missing shared lib, bad
// install) is exactly the failure mode `podcli doctor` otherwise can't see.
func runCheck(name, hermetic, pathFallback string, args ...string) doctorCheck {
	bin := hermetic
	source := "hermetic"
	if bin == "" {
		if p, err := exec.LookPath(pathFallback); err == nil {
			bin = p
			source = "PATH"
		}
	}
	if bin == "" {
		return doctorCheck{Name: name, OK: false, Level: levelFail, Detail: "not found (hermetic or PATH)"}
	}
	out, err := exec.Command(bin, args...).CombinedOutput()
	if err != nil {
		return doctorCheck{Name: name, OK: false, Level: levelFail, Detail: fmt.Sprintf("%s (%s): %v: %s", bin, source, err, firstLine(string(out)))}
	}
	return doctorCheck{Name: name, OK: true, Level: levelOK, Detail: fmt.Sprintf("%s (%s): %s", bin, source, firstLine(string(out)))}
}

func pythonBackendCheck() doctorCheck {
	root, ok := engine.BackendRoot()
	if !ok {
		return doctorCheck{Name: "python backend", OK: false, Level: levelFail, Detail: "backend not found (set PODCLI_BACKEND or run inside the repo)"}
	}
	// A representative sample, not every module: enough to catch "the
	// interpreter can't even import the backend's own services" without
	// reimplementing the whole import graph here.
	modules := []string{"services.ai_cli", "services.multicam", "services.caption_renderer", "services.transcription"}
	script := fmt.Sprintf("import sys; sys.path.insert(0, %q); import %s", root, strings.Join(modules, ", "))
	out, err := exec.Command(engine.Python(), "-c", script).CombinedOutput()
	if err != nil {
		return doctorCheck{Name: "python backend", OK: false, Level: levelFail, Detail: fmt.Sprintf("%s: %v: %s", engine.Python(), err, firstLine(string(out)))}
	}
	return doctorCheck{Name: "python backend", OK: true, Level: levelOK, Detail: fmt.Sprintf("%s imports %s", engine.Python(), strings.Join(modules, ", "))}
}

// modelHashCheck reports ok=false when the file is absent: an unprovisioned
// model is a valid state, not a failure.
func modelHashCheck(name, p, want string) (doctorCheck, bool) {
	if !fileExists(p) {
		return doctorCheck{}, false
	}
	got, err := provision.Sha256File(p)
	if err != nil {
		return doctorCheck{Name: name, OK: false, Level: levelFail, Detail: fmt.Sprintf("could not hash %s: %v", p, err)}, true
	}
	if got != want {
		return doctorCheck{Name: name, OK: false, Level: levelFail, Detail: fmt.Sprintf("%s hash mismatch: got %s, want %s", p, got, want)}, true
	}
	return doctorCheck{Name: name, OK: true, Level: levelOK, Detail: p}, true
}

func modelChecks() []doctorCheck {
	var checks []doctorCheck
	sizes := provision.KnownModelSizes()
	sort.Strings(sizes)
	for _, size := range sizes {
		want, _ := provision.ModelSHA256(size)
		if c, ok := modelHashCheck("model "+size, provision.ModelPath(size), want); ok {
			checks = append(checks, c)
		}
	}
	if c, ok := modelHashCheck("model vad", provision.VADModelPath(), provision.VADModelSHA256()); ok {
		checks = append(checks, c)
	}
	files := provision.OmnilingualFileHashes()
	names := make([]string, 0, len(files))
	for name := range files {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		p := filepath.Join(provision.OmnilingualDir(), name)
		if c, ok := modelHashCheck("model omnilingual "+name, p, files[name]); ok {
			checks = append(checks, c)
		}
	}
	return checks
}

// mcpRegistrationChecks reports registration status for whichever agent
// CLIs are on PATH. Registration is per-folder for Claude and optional for
// Codex-only users, so an unregistered folder is normal, not broken: these
// checks are warnings and don't count toward doctor's exit code, unless
// none of the detected agents are registered anywhere this can see, in
// which case nothing would actually work and that's a real failure.
func mcpRegistrationChecks() []doctorCheck {
	var checks []doctorCheck
	if _, err := exec.LookPath("claude"); err == nil {
		ok := mcpRegisteredToSelf()
		checks = append(checks, doctorCheck{
			Name:   "mcp registration (Claude)",
			OK:     ok,
			Detail: pick(ok, "registered", "not registered for this folder - run `podcli mcp install` here"),
		})
	}
	if _, err := exec.LookPath("codex"); err == nil {
		ok := codexMCPRegisteredToSelf()
		checks = append(checks, doctorCheck{
			Name:   "mcp registration (Codex)",
			OK:     ok,
			Detail: pick(ok, "registered", "not registered - run `podcli mcp install`"),
		})
	}
	return levelRegistrationChecks(checks)
}

func pick(cond bool, ifTrue, ifFalse string) string {
	if cond {
		return ifTrue
	}
	return ifFalse
}

// levelRegistrationChecks turns the OK/not-OK result of each detected
// agent's registration check into a severity: registered is "ok"; an
// unregistered agent is just "warn" as long as at least one detected agent
// is registered somewhere, since the MCP tools work through that one. If
// none of the detected agents are registered anywhere, nothing would
// actually work, which escalates every one of them to "fail".
func levelRegistrationChecks(checks []doctorCheck) []doctorCheck {
	anyRegistered := false
	for _, c := range checks {
		if c.OK {
			anyRegistered = true
			break
		}
	}
	for i := range checks {
		switch {
		case checks[i].OK:
			checks[i].Level = levelOK
		case anyRegistered:
			checks[i].Level = levelWarn
		default:
			checks[i].Level = levelFail
		}
	}
	return checks
}

func runDoctorChecks() []doctorCheck {
	var checks []doctorCheck
	checks = append(checks, runCheck("ffmpeg", engine.FFmpeg(), "ffmpeg", "-version"))
	checks = append(checks, runCheck("ffprobe", engine.FFprobe(), "ffprobe", "-version"))
	checks = append(checks, runCheck("whisper-cli", engine.WhisperCLI(), "whisper-cli", "--help"))
	checks = append(checks, runCheck("node", engine.Node(), "node", "--version"))
	checks = append(checks, pythonBackendCheck())
	checks = append(checks, modelChecks()...)
	checks = append(checks, mcpRegistrationChecks()...)
	return checks
}

// checksAllOK is doctor's exit-code rule: only a "fail" level counts.
// "warn" (currently just MCP registration) surfaces in the output without
// turning an otherwise-healthy install into a reported failure.
func checksAllOK(checks []doctorCheck) bool {
	for _, c := range checks {
		if c.Level == levelFail {
			return false
		}
	}
	return true
}

func doctor(args []string) int {
	asJSON := false
	for _, a := range args {
		if a == "--json" {
			asJSON = true
		}
	}

	pathInfo := map[string]string{
		"home":    paths.Home(),
		"runtime": paths.RuntimeDir(),
		"models":  paths.ModelsDir(),
	}
	if out := os.Getenv("PODCLI_OUTPUT"); out != "" {
		pathInfo["clips"] = out
	} else if cwd, err := os.Getwd(); err == nil {
		pathInfo["clips"] = filepath.Join(cwd, "podcli-clips")
	}

	checks := runDoctorChecks()
	allOK := checksAllOK(checks)

	if asJSON {
		report := doctorReport{Version: Version, Paths: pathInfo, Checks: checks, OK: allOK}
		data, err := json.MarshalIndent(report, "", "  ")
		if err != nil {
			fmt.Fprintln(os.Stderr, "podcli: doctor:", err)
			return 1
		}
		fmt.Println(string(data))
		if !allOK {
			return 1
		}
		return 0
	}

	fmt.Printf("podcli %s\n\n", Version)
	fmt.Println("Paths")
	fmt.Printf("  home:     %s\n", pathInfo["home"])
	fmt.Printf("  runtime:  %s\n", pathInfo["runtime"])
	fmt.Printf("  models:   %s\n", pathInfo["models"])
	fmt.Printf("  presets/knowledge/assets/history/cache: %s  (global - follow you everywhere)\n", paths.Home())
	if clips, ok := pathInfo["clips"]; ok {
		fmt.Printf("  clips:    %s\n", clips)
	}
	fmt.Println("\nEngine resolution")
	if root, ok := engine.BackendRoot(); ok {
		stamp := backend.Version(root)
		fmt.Printf("  backend:  %s%s\n", root, backendStamp(root))
		if stamp != "" && stamp != Version {
			fmt.Printf("  backend stamp: %s (launcher %s)\n", stamp, Version)
		}
		if root == filepath.Join(paths.RuntimeDir(), "backend") {
			fmt.Printf("  note:     Python may report launcher version via PODCLI_VERSION; trust stamp + file hashes above\n")
		}
	} else {
		fmt.Printf("  backend:  NOT FOUND (set PODCLI_BACKEND or run inside the repo)\n")
	}
	fmt.Printf("  python:   %s\n", engine.Python())
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

	fmt.Println("\nChecks")
	for _, c := range checks {
		mark := "OK  "
		switch c.Level {
		case levelWarn:
			mark = "WARN"
		case levelFail:
			mark = "FAIL"
		}
		fmt.Printf("  [%s] %-28s %s\n", mark, c.Name, c.Detail)
	}

	if !allOK {
		fmt.Println("\npodcli doctor found problems above.")
		return 1
	}
	fmt.Println("\nAll checks passed.")
	return 0
}

func fileExists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

func printHelp() {
	fmt.Printf(`podcli %s - AI podcast clip generator

Usage:
  podcli <command> [args]

Engine commands (routed to the processing backend):
  process <video>      Transcribe a video and export short-form clips
  ui                   Open the Studio web dashboard (http://localhost:3847)
  studio <video>       Cut a fragment + intro/outro bookends
  multicam <folder>    Sync every camera and mic, auto-cut to the speaker, render the episode
  clips                Browse and edit saved clips
  thumbnails           Generate thumbnails
  knowledge | presets | assets | youtube | config | cache | info

PodStack commands (run inside Claude Code / Codex):
  auto <video>         One-verb pipeline: drop footage, get rendered clips
  generate-titles | generate-descriptions | plan-thumbnails | plan-episode
  process-transcript | produce-shorts | review-content | publish-checklist
  retro-episode        Add --codex / --claude to pick the agent

Launcher commands:
  login | logout | whoami
                       podcli Pro account on this machine
  sync                 Reconcile clips, assets, and knowledge with your workspace
  doctor               Show resolved paths, interpreter, backend, ffmpeg, models
  version              Print version
  update               Check for and apply a newer release
  uninstall            Remove podcli app files (keeps user data unless --purge)
  setup [--model base] [--vad] [--speakers]
                       Provision runtimes + models (--speakers adds pyannote+torch, ~2GB)
  setup --refresh      Re-provision runtimes for this launcher version, skipping models
  mcp                  Run the MCP server (stdio) for Claude/Codex
  mcp install          Register the MCP server with Claude Code
  config set update.auto off    Disable auto-update (also: PODCLI_NO_UPDATE=1)
  config get update.auto

Run a command with --help for its options.
`, Version)
}
