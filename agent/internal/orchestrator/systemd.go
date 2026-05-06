package orchestrator

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"

	"mygoagent/internal/config"
)

type CreateInstanceRequest struct {
	Name    string `json:"name"`
	Port    int    `json:"port"`
	UserDir string `json:"userDir"`
}

type SystemdOrchestrator struct {
	cfg config.SystemdConfig
}

func NewSystemdOrchestrator(cfg config.SystemdConfig) *SystemdOrchestrator {
	return &SystemdOrchestrator{cfg: cfg}
}

// CreateInstance 创建Node-RED实例
func (o *SystemdOrchestrator) CreateInstance(ctx context.Context, req CreateInstanceRequest) error {
	if runtime.GOOS != "linux" {
		return fmt.Errorf("unsupported_os: %s", runtime.GOOS)
	}
	if err := validateInstanceName(req.Name); err != nil {
		return err
	}
	if err := validatePort(req.Port); err != nil {
		return err
	}
	if o.cfg.User == "" {
		return errors.New("systemd.user_required")
	}
	if o.cfg.Group == "" {
		return errors.New("systemd.group_required")
	}
	if o.cfg.NodeRedCmd == "" {
		return errors.New("systemd.node_red_cmd_required")
	}
	if o.cfg.InstancesBaseDir == "" {
		return errors.New("systemd.instances_base_dir_required")
	}
	if o.cfg.EnvDir == "" {
		return errors.New("systemd.env_dir_required")
	}
	if o.cfg.ServicePath == "" {
		return errors.New("systemd.service_path_required")
	}
	if o.cfg.WorkingDir == "" {
		return errors.New("systemd.working_dir_required")
	}

	userDir := req.UserDir
	if userDir == "" {
		userDir = filepath.Join(o.cfg.InstancesBaseDir, req.Name)
	}
	userDir = filepath.Clean(userDir)
	base := filepath.Clean(o.cfg.InstancesBaseDir)
	if !isWithinDir(base, userDir) {
		return errors.New("userDir_outside_instances_base_dir")
	}

	if err := os.MkdirAll(userDir, 0o755); err != nil {
		return err
	}
	if err := run(ctx, "chown", "-R", o.cfg.User+":"+o.cfg.Group, userDir); err != nil {
		return err
	}

	if err := os.MkdirAll(o.cfg.EnvDir, 0o755); err != nil {
		return err
	}
	envPath := filepath.Join(o.cfg.EnvDir, req.Name+".conf")
	envContent := buildEnvFile(userDir, req.Port)
	if err := os.WriteFile(envPath, []byte(envContent), 0o644); err != nil {
		return err
	}

	if _, err := os.Stat(o.cfg.ServicePath); err != nil {
		if !errors.Is(err, os.ErrNotExist) {
			return err
		}
		serviceContent := buildServiceTemplate(o.cfg)
		if err := os.WriteFile(o.cfg.ServicePath, []byte(serviceContent), 0o644); err != nil {
			return err
		}
	}

	if err := run(ctx, "systemctl", "daemon-reload"); err != nil {
		return err
	}
	unit := "nodered@" + req.Name + ".service"
	if err := run(ctx, "systemctl", "enable", "--now", unit); err != nil {
		return err
	}
	return nil
}

func validateInstanceName(name string) error {
	if name == "" {
		return errors.New("instance_name_required")
	}
	if len(name) > 32 {
		return errors.New("instance_name_too_long")
	}
	ok := regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_-]*$`).MatchString(name)
	if !ok {
		return errors.New("instance_name_invalid")
	}
	return nil
}

func validatePort(port int) error {
	if port < 1 || port > 65535 {
		return errors.New("port_invalid")
	}
	if port < 1024 {
		return errors.New("port_privileged")
	}
	return nil
}

func isWithinDir(baseDir, target string) bool {
	base := filepath.Clean(baseDir)
	t := filepath.Clean(target)
	if base == t {
		return true
	}
	rel, err := filepath.Rel(base, t)
	if err != nil {
		return false
	}
	rel = filepath.ToSlash(rel)
	return rel != ".." && !strings.HasPrefix(rel, "../")
}

func buildEnvFile(userDir string, port int) string {
	return "USER_DIR=" + escapeEnvValue(userDir) + "\nPORT=" + strconv.Itoa(port) + "\n"
}

func escapeEnvValue(v string) string {
	if strings.ContainsAny(v, " \t\"'\\") {
		return `"` + strings.ReplaceAll(v, `"`, `\"`) + `"`
	}
	return v
}

func buildServiceTemplate(cfg config.SystemdConfig) string {
	return strings.Join([]string{
		"[Unit]",
		"Description=Node-RED instance %i",
		"After=network.target",
		"",
		"[Service]",
		"Type=simple",
		"User=" + cfg.User,
		"Group=" + cfg.Group,
		"EnvironmentFile=" + filepath.ToSlash(filepath.Clean(cfg.EnvDir)) + "/%i.conf",
		"WorkingDirectory=" + cfg.WorkingDir,
		"ExecStart=/usr/bin/env " + cfg.NodeRedCmd + " --userDir=${USER_DIR} --port=${PORT}",
		"Restart=on-failure",
		"RestartSec=5",
		"KillSignal=SIGINT",
		"SyslogIdentifier=nodered-%i",
		"",
		"[Install]",
		"WantedBy=multi-user.target",
		"",
	}, "\n")
}

func run(ctx context.Context, name string, args ...string) error {
	cmd := exec.CommandContext(ctx, name, args...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s: %w: %s", name, err, strings.TrimSpace(string(out)))
	}
	return nil
}
