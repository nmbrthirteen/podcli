// Package podstack forwards PodStack workflow commands (auto, generate-titles,
// …) to an AI agent CLI (Claude Code or Codex), porting the old bash launcher.
// The commands are Claude Code slash commands driving the podcli MCP tools, so
// they run inside the agent, not the terminal. The slash-command files are
// embedded and installed into the working project on demand.
package podstack

import (
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"encoding/json"
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

// warnEmptyKnowledge nudges toward `podcli knowledge init` before a workflow
// runs against a blank knowledge base. README.md does not count: the studio
// writes one into the knowledge dir, and it carries no show context.
func warnEmptyKnowledge() {
	kb := filepath.Join(paths.Home(), "knowledge")
	entries, err := os.ReadDir(kb)
	if err == nil {
		for _, e := range entries {
			name := e.Name()
			if strings.HasSuffix(name, ".md") && !strings.EqualFold(name, "README.md") {
				return
			}
		}
	}
	fmt.Fprintf(os.Stderr, "  %sKnowledge base is empty.%s Run %spodcli knowledge init%s first, or %s/bootstrap-knowledge%s in your agent, so the workflow knows your show.\n", colYellow, colReset, colAccent, colReset, colAccent, colReset)
}

// manifestName is the install-tracking file, hidden among the slash commands
// it describes. It is not itself a command (no .md suffix), so Names() and
// IsCommand() never see it.
const manifestName = ".podcli-manifest.json"

// installManifest records the sha256 of the content podcli last wrote for
// each command file, so a later install can tell "podcli wrote this and
// nothing has touched it since" (safe to overwrite with the new version)
// apart from "the user edited this" (leave it alone).
type installManifest struct {
	Files map[string]string `json:"files"`
}

func readManifest(dest string) installManifest {
	m := installManifest{Files: map[string]string{}}
	data, err := os.ReadFile(filepath.Join(dest, manifestName))
	if err != nil {
		return m
	}
	_ = json.Unmarshal(data, &m)
	if m.Files == nil {
		m.Files = map[string]string{}
	}
	return m
}

func writeManifest(dest string, m installManifest) error {
	data, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(dest, manifestName), data, 0o644)
}

func sha256Hex(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

// installReport summarizes what installCommands did, for callers that want
// to tell the user about files it left alone.
type installReport struct {
	Installed    []string // written for the first time
	Updated      []string // podcli-owned, unmodified by the user, refreshed to the new version
	UserModified []string // changed since podcli last wrote them, left alone
}

func (r installReport) hasUpdates() bool {
	return len(r.Installed) > 0 || len(r.Updated) > 0
}

// installCommands writes the embedded slash-command files into
// <project>/.claude/commands so the agent can resolve /<cmd>. A manifest
// tracks the hash of what podcli wrote for each file: on a later run (e.g.
// after `podcli update`), a file whose on-disk hash still matches that
// record gets refreshed to the new embedded version; a file the user edited
// is left untouched and reported instead of silently overwritten. A file
// that predates the manifest (no record at all) is treated the same way:
// left alone. Its current content becomes the new baseline, so podcli
// never clobbers an install from before this tracking existed.
func installCommands(project string) (installReport, error) {
	var report installReport
	dest := filepath.Join(project, ".claude", "commands")
	if err := os.MkdirAll(dest, 0o755); err != nil {
		return report, err
	}
	manifest := readManifest(dest)
	changed := false

	err := fs.WalkDir(commands, "commands", func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		name := filepath.Base(p)
		target := filepath.Join(dest, name)
		embedded, err := commands.ReadFile(p)
		if err != nil {
			return err
		}
		embeddedHash := sha256Hex(embedded)

		current, statErr := os.ReadFile(target)
		if statErr != nil {
			// Never installed here before.
			if err := os.WriteFile(target, embedded, 0o644); err != nil {
				return err
			}
			manifest.Files[name] = embeddedHash
			changed = true
			report.Installed = append(report.Installed, name)
			return nil
		}

		lastInstalledHash, tracked := manifest.Files[name]
		currentHash := sha256Hex(current)

		if !tracked {
			// Predates the manifest. Only adopt it as the baseline if it's
			// actually the stock file (hash matches what's embedded). That's
			// the "installed before tracking existed" case. If it doesn't
			// match, it's a user edit (or something else entirely); leave it
			// untracked rather than recording its current hash as a baseline,
			// which would make the next run believe it's unmodified and
			// overwrite it once the embedded version changes.
			if currentHash == embeddedHash {
				manifest.Files[name] = currentHash
				changed = true
				return nil
			}
			report.UserModified = append(report.UserModified, name)
			return nil
		}

		if currentHash != lastInstalledHash {
			// The user changed it since podcli last wrote it.
			report.UserModified = append(report.UserModified, name)
			return nil
		}

		if currentHash == embeddedHash {
			return nil // already current
		}

		if err := os.WriteFile(target, embedded, 0o644); err != nil {
			return err
		}
		manifest.Files[name] = embeddedHash
		changed = true
		report.Updated = append(report.Updated, name)
		return nil
	})
	if err != nil {
		return report, err
	}
	if changed {
		if err := writeManifest(dest, manifest); err != nil {
			return report, err
		}
	}
	return report, nil
}

// codexSkillsDir is where the installed Codex CLI reads skills from
// (confirmed against a local `codex` install: ~/.codex/skills/<name>/SKILL.md,
// one directory per skill). It is global, not per-project, unlike
// .claude/commands.
func codexSkillsDir() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".codex", "skills"), nil
}

// frontmatterDescription pulls the `description:` field out of a command
// file's YAML frontmatter without a YAML dependency. The format here is a
// fixed, simple `key: value` list.
func frontmatterDescription(raw string) string {
	lines := strings.Split(raw, "\n")
	inFrontmatter := false
	for i, line := range lines {
		if i == 0 && strings.TrimSpace(line) == "---" {
			inFrontmatter = true
			continue
		}
		if !inFrontmatter {
			return ""
		}
		if strings.TrimSpace(line) == "---" {
			return ""
		}
		if rest, ok := strings.CutPrefix(line, "description:"); ok {
			return strings.TrimSpace(rest)
		}
	}
	return ""
}

// codexSkillContent rewraps a PodStack command file as a Codex SKILL.md:
// same body, frontmatter translated from this project's `description:` /
// `argument-hint:` shape to the `name:` / `description:` shape codex reads.
func codexSkillContent(name, raw string) string {
	desc := frontmatterDescription(raw)
	if desc == "" {
		desc = "PodStack command: " + name
	}
	body := raw
	if end := strings.Index(raw, "\n---\n"); strings.HasPrefix(raw, "---\n") && end != -1 {
		body = strings.TrimPrefix(raw[end+len("\n---\n"):], "\n")
	}
	return fmt.Sprintf("---\nname: %s\ndescription: %s\n---\n\n%s", name, desc, body)
}

// installCodexSkills writes every PodStack command as a Codex skill under
// ~/.codex/skills/<name>/SKILL.md. Skills are global (not per-project like
// .claude/commands), so this always targets the user's home directory
// regardless of which project podcli was run from. Like installCommands, a
// manifest tracks what podcli last wrote so an upgrade can refresh
// unmodified skills and leave user edits alone.
func installCodexSkills() error {
	skillsDir, err := codexSkillsDir()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(skillsDir, 0o755); err != nil {
		return err
	}
	manifest := readManifest(skillsDir)
	changed := false

	err = fs.WalkDir(commands, "commands", func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		name := strings.TrimSuffix(filepath.Base(p), ".md")
		raw, err := commands.ReadFile(p)
		if err != nil {
			return err
		}
		content := []byte(codexSkillContent(name, string(raw)))
		contentHash := sha256Hex(content)

		skillDir := filepath.Join(skillsDir, name)
		target := filepath.Join(skillDir, "SKILL.md")
		current, statErr := os.ReadFile(target)
		if statErr != nil {
			if err := os.MkdirAll(skillDir, 0o755); err != nil {
				return err
			}
			if err := os.WriteFile(target, content, 0o644); err != nil {
				return err
			}
			manifest.Files[name] = contentHash
			changed = true
			return nil
		}

		lastInstalledHash, tracked := manifest.Files[name]
		currentHash := sha256Hex(current)
		if !tracked {
			// Same reasoning as installCommands: only adopt an untracked file
			// as the baseline if it's actually the stock skill content.
			if currentHash == contentHash {
				manifest.Files[name] = currentHash
				changed = true
			}
			return nil
		}
		if currentHash != lastInstalledHash || currentHash == contentHash {
			return nil // user-modified, or already current
		}
		if err := os.WriteFile(target, content, 0o644); err != nil {
			return err
		}
		manifest.Files[name] = contentHash
		changed = true
		return nil
	})
	if err != nil {
		return err
	}
	if changed {
		return writeManifest(skillsDir, manifest)
	}
	return nil
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
	report, err := installCommands(project)
	if err != nil {
		fmt.Fprintf(os.Stderr, "  %swarning:%s could not install slash commands: %v\n", colYellow, colReset, err)
	}
	if len(report.UserModified) > 0 {
		fmt.Fprintf(os.Stderr, "  %sKept your edits%s to: %s\n", colDim, colReset, strings.Join(report.UserModified, ", "))
	}
	warnEmptyKnowledge()

	prompt := "/" + cmd
	if len(promptArgs) > 0 {
		prompt += " " + strings.Join(promptArgs, " ")
	}
	codexPrompt := fmt.Sprintf("Run the PodStack workflow from .claude/commands/%s.md with these arguments, then follow that workflow exactly: %s", cmd, prompt)

	claudeBin, _ := exec.LookPath("claude")
	codexBin, _ := exec.LookPath("codex")

	if codexBin != "" {
		if err := installCodexSkills(); err != nil {
			fmt.Fprintf(os.Stderr, "  %swarning:%s could not install Codex skills: %v\n", colYellow, colReset, err)
		}
	}

	if engine == "codex" && codexBin == "" {
		fmt.Fprintf(os.Stderr, "\n  %sCodex not found in PATH.%s\n  Install it, then run:\n    %scodex --cd %q %q%s\n\n", colBold, colReset, colAccent, project, codexPrompt, colReset)
		return 1
	}
	if engine == "claude" && claudeBin == "" {
		fmt.Fprintf(os.Stderr, "\n  %sClaude Code not found in PATH.%s\n  Install it, then run:\n    %sclaude %q%s\n\n", colBold, colReset, colAccent, prompt, colReset)
		return 1
	}

	// Fall back to Codex only when Claude isn't installed at all. Claude
	// exiting nonzero (the user cancelled, a tool failed mid-run, etc.) is
	// not a reason to silently relaunch the whole workflow under a
	// different agent. It previously was, which could run the same
	// destructive command twice under two different engines.
	if engine != "codex" && claudeBin != "" {
		fmt.Fprintf(os.Stderr, "\n  %s▶%s Launching Claude Code with: %s%s%s\n  %scwd: %s%s\n\n", colGreen, colReset, colAccent, prompt, colReset, colDim, project, colReset)
		return runIn(project, claudeBin, prompt)
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
