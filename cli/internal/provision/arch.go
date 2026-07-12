package provision

import (
	"debug/elf"
	"debug/macho"
	"debug/pe"
	"runtime"
)

// nativeArch reports the GOARCH an executable was built for. ok is false when
// the format is unrecognised (scripts, universal binaries), so callers can tell
// "wrong arch" apart from "cannot tell".
func nativeArch(path string) (arch string, ok bool) {
	if f, err := macho.Open(path); err == nil {
		defer f.Close()
		switch f.Cpu {
		case macho.CpuAmd64:
			return "amd64", true
		case macho.CpuArm64:
			return "arm64", true
		}
		return "", false
	}
	if f, err := elf.Open(path); err == nil {
		defer f.Close()
		switch f.Machine {
		case elf.EM_X86_64:
			return "amd64", true
		case elf.EM_AARCH64:
			return "arm64", true
		}
		return "", false
	}
	if f, err := pe.Open(path); err == nil {
		defer f.Close()
		switch f.Machine {
		case pe.IMAGE_FILE_MACHINE_AMD64:
			return "amd64", true
		case pe.IMAGE_FILE_MACHINE_ARM64:
			return "arm64", true
		}
		return "", false
	}
	return "", false
}

// nativeBin reports whether path exists and runs natively here. Existence alone
// is not enough: a runtime provisioned by an earlier install can hold binaries
// for the wrong arch, which run under emulation at best, and on macOS pin pip to
// x86_64 wheels that upstream no longer publishes. Callers re-provision instead.
func nativeBin(path string) bool {
	if !have(path) {
		return false
	}
	arch, ok := nativeArch(path)
	return !ok || arch == runtime.GOARCH
}
