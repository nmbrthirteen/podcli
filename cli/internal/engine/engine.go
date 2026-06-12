// Package engine routes podcli subcommands to the Python backend.
package engine

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"

	"podcli/internal/paths"
)

// IsHermeticPython reports whether the resolved interpreter is the provisioned
// one (which lacks openai-whisper, so transcription must default to whisper.cpp).
func IsHermeticPython() bool {
	return strings.HasPrefix(Python(), paths.RuntimeDir())
}

func exists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

func BackendRoot() (string, bool) {
	if b := os.Getenv("PODCLI_BACKEND"); b != "" && exists(filepath.Join(b, "cli.py")) {
		return b, true
	}
	if dir, err := os.Getwd(); err == nil {
		for {
			cand := filepath.Join(dir, "backend")
			if exists(filepath.Join(cand, "cli.py")) {
				return cand, true
			}
			parent := filepath.Dir(dir)
			if parent == dir {
				break
			}
			dir = parent
		}
	}
	cand := filepath.Join(paths.RuntimeDir(), "backend")
	if exists(filepath.Join(cand, "cli.py")) {
		return cand, true
	}
	return "", false
}

func Python() string {
	if p := os.Getenv("PODCLI_PYTHON"); p != "" {
		return p
	}
	hermetic := []string{
		filepath.Join(paths.RuntimeDir(), "python", "bin", "python3"),
		filepath.Join(paths.RuntimeDir(), "python", "python.exe"),
	}
	for _, p := range hermetic {
		if exists(p) {
			return p
		}
	}
	if root, ok := BackendRoot(); ok {
		venv := filepath.Join(filepath.Dir(root), "venv", "bin", "python3")
		if runtime.GOOS == "windows" {
			venv = filepath.Join(filepath.Dir(root), "venv", "Scripts", "python.exe")
		}
		if exists(venv) {
			return venv
		}
	}
	return "python3"
}

func runtimeBin(sub, name string) string {
	for _, p := range []string{
		filepath.Join(paths.RuntimeDir(), sub, name),
		filepath.Join(paths.RuntimeDir(), sub, name+".exe"),
	} {
		if exists(p) {
			return p
		}
	}
	return ""
}

func FFmpeg() string     { return runtimeBin("ffmpeg", "ffmpeg") }
func FFprobe() string    { return runtimeBin("ffmpeg", "ffprobe") }
func WhisperCLI() string { return runtimeBin("whisper", "whisper-cli") }

func Run(args []string) (int, error) {
	root, ok := BackendRoot()
	if !ok {
		return 1, fmt.Errorf("python backend not found — set PODCLI_BACKEND or run inside the repo")
	}
	cli := filepath.Join(root, "cli.py")
	full := append([]string{"-W", "ignore::UserWarning", cli}, args...)

	cmd := exec.Command(Python(), full...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	env := append(os.Environ(),
		"OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES",
		"PYTHONIOENCODING=utf-8",
		"PYTHONUTF8=1",
	)
	if ff := FFmpeg(); ff != "" {
		env = append(env, "PODCLI_FFMPEG="+ff)
	}
	if fp := FFprobe(); fp != "" {
		env = append(env, "PODCLI_FFPROBE="+fp)
	}
	if wc := WhisperCLI(); wc != "" {
		env = append(env, "PODCLI_WHISPER_CLI="+wc)
	}
	cmd.Env = env

	if err := cmd.Run(); err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			return ee.ExitCode(), nil
		}
		return 1, err
	}
	return 0, nil
}
