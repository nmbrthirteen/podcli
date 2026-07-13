// Package podstack forwards PodStack workflow commands (auto, generate-titles,
// …) to an AI agent CLI (Claude Code or Codex), porting the old bash launcher.
// The commands are Claude Code slash commands driving the podcli MCP tools, so
// they run inside the agent, not the terminal. The slash-command files are
// embedded and installed into the working project on demand.
package podstack

import (
	"embed"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"syscall"

	"podcli/internal/paths"
)

//go:generate sh sync.sh
//go:embed all:commands
var commands embed.FS

const (
	colAccent = "\033[38;2;212;135;74m"
	colGreen  = "\033[38;2;74;222;128m"
	colYellow = "\033[38;2;250;204;21m"
	colBold   = "\033[1m"
	colDim    = "\033[2m"
	colReset  = "\033[0m"
)

// Names lists the supported PodStack commands, derived from the embedded files.
func Names() []string {
	entries, err := commands.ReadDir("commands")
	if err != nil {
		return nil
	}
	var names []string
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".md") {
			names = append(names, strings.TrimSuffix(e.Name(), ".md"))
		}
	}
	sort.Strings(names)
	return names
}

func IsCommand(name string) bool {
	name = strings.TrimPrefix(strings.TrimPrefix(name, "--"), "/")
	for _, n := range Names() {
		if n == name {
			return true
		}
	}
	return false
}

// installCommands writes the embedded slash-command files into
// <project>/.claude/commands so the agent can resolve /<cmd>. Existing files are
// left untouched, so a user's local edits win.
// warnEmptyKnowledge nudges toward `podcli knowledge init` before a workflow
// runs against a blank knowledge base. Best-effort: PODCLI_HOME and the
// platform default cover installed binaries; source checkouts resolve home in
// the Python layer and are skipped rather than guessed.
func warnEmptyKnowledge() {
	kb := filepath.Join(paths.Home(), "knowledge")
	entries, err := os.ReadDir(kb)
	if err == nil {
		for _, e := range entries {
			if strings.HasSuffix(e.Name(), ".md") {
				return
			}
		}
	}
	fmt.Fprintf(os.Stderr, "  %sKnowledge base is empty.%s Run %spodcli knowledge init%s first, or %s/bootstrap-knowledge%s in your agent, so the workflow knows your show.\n", colYellow, colReset, colAccent, colReset, colAccent, colReset)
}

func installCommands(project string) error {
	dest := filepath.Join(project, ".claude", "commands")
	if err := os.MkdirAll(dest, 0o755); err != nil {
		return err
	}
	return fs.WalkDir(commands, "commands", func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		target := filepath.Join(dest, filepath.Base(p))
		if _, err := os.Stat(target); err == nil {
			return nil
		}
		data, err := commands.ReadFile(p)
		if err != nil {
			return err
		}
		return os.WriteFile(target, data, 0o644)
	})
}

// Run launches the agent for cmd with the remaining args as the slash-command
// arguments. Engine selection: --claude / --codex / --ai <engine>, else
// PODCLI_AI, else auto (Claude preferred, Codex fallback).
func Run(cmd string, args []string) int {
	cmd = strings.TrimPrefix(strings.TrimPrefix(cmd, "--"), "/")
	engine := os.Getenv("PODCLI_AI")
	if engine == "" {
		engine = "auto"
	}
	var promptArgs []string
	for i := 0; i < len(args); i++ {
		switch a := args[i]; {
		case a == "--codex":
			engine = "codex"
		case a == "--claude":
			engine = "claude"
		case a == "--ai":
			if i+1 < len(args) {
				engine = args[i+1]
				i++
			}
		case strings.HasPrefix(a, "--ai="):
			engine = strings.TrimPrefix(a, "--ai=")
		default:
			promptArgs = append(promptArgs, a)
		}
	}
	switch engine {
	case "auto", "claude", "codex":
	default:
		fmt.Fprintf(os.Stderr, "  %sInvalid AI engine:%s %s — use --claude, --codex, or --ai auto\n", colBold, colReset, engine)
		return 1
	}

	project, err := os.Getwd()
	if err != nil {
		project = "."
	}
	if err := installCommands(project); err != nil {
		fmt.Fprintf(os.Stderr, "  %swarning:%s could not install slash commands: %v\n", colYellow, colReset, err)
	}
	warnEmptyKnowledge()

	prompt := "/" + cmd
	if len(promptArgs) > 0 {
		prompt += " " + strings.Join(promptArgs, " ")
	}
	codexPrompt := fmt.Sprintf("Run the PodStack workflow from .claude/commands/%s.md with these arguments, then follow that workflow exactly: %s", cmd, prompt)

	claudeBin, _ := exec.LookPath("claude")
	codexBin, _ := exec.LookPath("codex")

	if engine == "codex" && codexBin == "" {
		fmt.Fprintf(os.Stderr, "\n  %sCodex not found in PATH.%s\n  Install it, then run:\n    %scodex --cd %q %q%s\n\n", colBold, colReset, colAccent, project, codexPrompt, colReset)
		return 1
	}
	if engine == "claude" && claudeBin == "" {
		fmt.Fprintf(os.Stderr, "\n  %sClaude Code not found in PATH.%s\n  Install it, then run:\n    %sclaude %q%s\n\n", colBold, colReset, colAccent, prompt, colReset)
		return 1
	}

	if engine != "codex" && claudeBin != "" {
		fmt.Fprintf(os.Stderr, "\n  %s▶%s Launching Claude Code with: %s%s%s\n  %scwd: %s%s\n\n", colGreen, colReset, colAccent, prompt, colReset, colDim, project, colReset)
		if code := runIn(project, claudeBin, prompt); code == 0 || engine == "claude" || codexBin == "" {
			return code
		}
		fmt.Fprintf(os.Stderr, "\n  %s⚠%s Claude exited nonzero; trying Codex...\n", colYellow, colReset)
	}

	if codexBin == "" {
		fmt.Fprintf(os.Stderr, "\n  %sNo AI agent CLI found in PATH.%s\n  Install Claude Code or Codex, then run one of:\n    %sclaude %q%s\n    %scodex --cd %q %q%s\n\n", colBold, colReset, colAccent, prompt, colReset, colAccent, project, codexPrompt, colReset)
		return 1
	}
	fmt.Fprintf(os.Stderr, "\n  %s▶%s Launching Codex with: %s%s%s\n  %scwd: %s%s\n\n", colGreen, colReset, colAccent, prompt, colReset, colDim, project, colReset)
	return runIn(project, codexBin, "--cd", project, codexPrompt)
}

func runIn(dir, bin string, args ...string) int {
	cmd := exec.Command(bin, args...)
	cmd.Dir = dir
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	if err := cmd.Run(); err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			if ws, ok := ee.Sys().(syscall.WaitStatus); ok {
				return ws.ExitStatus()
			}
			return 1
		}
		return 1
	}
	return 0
}
