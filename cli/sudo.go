package main

import (
	"fmt"
	"os"
	"strings"

	"podcli/internal/paths"
)

// sudoRefusalMessage returns the text to print, or "" when the invocation is fine.
//
// macOS sudo keeps HOME, so `sudo podcli` resolves the same managed dir as the
// real user and provisions the whole runtime as root. The user is then locked
// out of their own install, and the first symptom is an ensurepip traceback
// several thousand files too late to read as a permissions problem.
//
// Keyed on SUDO_USER rather than euid: a container that legitimately runs as
// root has its own home and must keep working.
func sudoRefusalMessage(sudoUser, home string, rootOwned bool) string {
	if sudoUser == "" {
		return ""
	}
	var b strings.Builder
	b.WriteString("podcli: refusing to run under sudo.\n\n")
	b.WriteString("  Sudo keeps your HOME, so everything installed lands in\n")
	b.WriteString("    " + home + "\n")
	b.WriteString("  owned by root, and you lose access to your own install.\n")
	b.WriteString("  podcli needs no elevated privileges. Run it as " + sudoUser + ".\n")
	if rootOwned {
		b.WriteString("\n  An earlier sudo run already left root-owned files there. Repair with:\n")
		b.WriteString(fmt.Sprintf("    sudo chown -R %s %s\n", sudoUser, shellQuote(home)))
	}
	return b.String()
}

func shellQuote(s string) string {
	if !strings.ContainsAny(s, " \t\n\"'$`\\") {
		return s
	}
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}

// refuseSudo prints and exits when podcli was invoked through sudo.
//
// uninstall is exempt: once a previous sudo run has taken the directory, root
// is the only thing that can remove it, and refusing there would trap the user
// this guard exists for.
func refuseSudo(args []string) {
	if len(args) > 0 && args[0] == "uninstall" {
		return
	}
	sudoUser := os.Getenv("SUDO_USER")
	if sudoUser == "" {
		return
	}
	home := paths.Home()
	msg := sudoRefusalMessage(sudoUser, home, homeHasRootOwnedFiles(home))
	if msg == "" {
		return
	}
	fmt.Fprint(os.Stderr, msg)
	os.Exit(1)
}
