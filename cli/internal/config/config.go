// Package config persists launcher settings (currently the auto-update
// off-switch) in the managed dir's config.json.
package config

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"

	"podcli/internal/paths"
)

type Config struct {
	Update struct {
		Auto *bool `json:"auto,omitempty"`
	} `json:"update"`
}

func Load() Config {
	var c Config
	if b, err := os.ReadFile(paths.ConfigPath()); err == nil {
		json.Unmarshal(b, &c)
	}
	return c
}

func (c Config) Save() error {
	if err := os.MkdirAll(paths.Home(), 0o755); err != nil {
		return err
	}
	b, _ := json.MarshalIndent(c, "", "  ")
	return os.WriteFile(paths.ConfigPath(), b, 0o644)
}

func truthy(s string) bool {
	switch strings.ToLower(strings.TrimSpace(s)) {
	case "1", "true", "yes", "on":
		return true
	}
	return false
}

// AutoUpdate is true unless disabled via config update.auto or PODCLI_NO_UPDATE.
func AutoUpdate() bool {
	if truthy(os.Getenv("PODCLI_NO_UPDATE")) {
		return false
	}
	if a := Load().Update.Auto; a != nil {
		return *a
	}
	return true
}

func Get(key string) (string, error) {
	switch key {
	case "update.auto":
		if a := Load().Update.Auto; a != nil && !*a {
			return "off", nil
		}
		return "on", nil
	}
	return "", fmt.Errorf("unknown config key %q (known: update.auto)", key)
}

func Set(key, val string) error {
	switch key {
	case "update.auto":
		on := !offValue(val)
		c := Load()
		c.Update.Auto = &on
		return c.Save()
	}
	return fmt.Errorf("unknown config key %q (known: update.auto)", key)
}

func offValue(s string) bool {
	switch strings.ToLower(strings.TrimSpace(s)) {
	case "off", "false", "no", "0", "disable", "disabled":
		return true
	}
	return false
}
