// Package engine routes podcli subcommands to the Python backend.
package engine

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"

	"podcli/internal/paths"
)

// stderrFilter drops the macOS/OpenCV/Whisper startup noise the old bash
// launcher stripped with `grep -v`, so native runs aren't louder than before.
// It buffers partial lines and forwards everything else to real stderr.
type stderrFilter struct{ buf []byte }

func isStderrNoise(line string) bool {
	return strings.HasPrefix(line, "objc[") ||
		strings.Contains(line, "FP16 is not supported") ||
		strings.Contains(line, "warnings.warn")
}

func (w *stderrFilter) Write(p []byte) (int, error) {
	w.buf = append(w.buf, p...)
	for {
		i := bytes.IndexByte(w.buf, '\n')
		if i < 0 {
			break
		}
		line := w.buf[:i]
		w.buf = w.buf[i+1:]
		if !isStderrNoise(string(line)) {
			os.Stderr.Write(append(line, '\n'))
		}
	}
	return len(p), nil
}

func (w *stderrFilter) flush() {
	if len(w.buf) > 0 && !isStderrNoise(string(w.buf)) {
		os.Stderr.Write(w.buf)
	}
	w.buf = nil
}

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

func Node() string {
	for _, p := range []string{
		filepath.Join(paths.RuntimeDir(), "node", "bin", "node"),
		filepath.Join(paths.RuntimeDir(), "node", "node.exe"),
	} {
		if exists(p) {
			return p
		}
	}
	return ""
}

func StudioServer() string {
	p := filepath.Join(paths.RuntimeDir(), "studio", "web-server.mjs")
	if exists(p) {
		return p
	}
	return ""
}

// ProjectDir resolves the user's project root: the nearest ancestor of the
// working directory holding a .podcli dir or .podcli-home marker, else the
// working directory itself. This keeps episode data, presets, and .env
// project-local — the behavior of the old in-repo launcher — now that the
// backend lives in the global runtime dir instead of beside the data.
func ProjectDir() string {
	dir, err := os.Getwd()
	if err != nil {
		return ""
	}
	for {
		if exists(filepath.Join(dir, ".podcli")) || exists(filepath.Join(dir, ".podcli-home")) {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	if cwd, err := os.Getwd(); err == nil {
		return cwd
	}
	return ""
}

func Run(args []string) (int, error) {
	root, ok := BackendRoot()
	if !ok {
		return 1, fmt.Errorf("python backend not found — set PODCLI_BACKEND or run inside the repo")
	}
	cli := filepath.Join(root, "cli.py")
	full := append([]string{"-W", "ignore::UserWarning", cli}, args...)

	cmd := exec.Command(Python(), full...)
	sf := &stderrFilter{}
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, sf
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
	if nd := Node(); nd != "" {
		env = append(env, "PODCLI_NODE="+nd)
	}
	if ss := StudioServer(); ss != "" {
		env = append(env, "PODCLI_STUDIO="+filepath.Dir(ss))
	}
	// Pin data + .env to the user's project dir so the global runtime backend
	// doesn't strand project-local episodes/presets. Explicit env wins.
	if proj := ProjectDir(); proj != "" {
		if os.Getenv("PODCLI_HOME") == "" {
			env = append(env, "PODCLI_HOME="+filepath.Join(proj, ".podcli"))
		}
		if os.Getenv("PODCLI_DATA") == "" {
			env = append(env, "PODCLI_DATA="+filepath.Join(proj, "data"))
		}
		if os.Getenv("PODCLI_ENV_FILE") == "" {
			env = append(env, "PODCLI_ENV_FILE="+filepath.Join(proj, ".env"))
		}
	}
	cmd.Env = env

	err := cmd.Run()
	sf.flush()
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			return ee.ExitCode(), nil
		}
		return 1, err
	}
	return 0, nil
}
