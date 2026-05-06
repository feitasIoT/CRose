package config

import (
	"os"

	"gopkg.in/yaml.v3"
)

type MQTTConfig struct {
	BrokerURL   string `yaml:"broker_url"`
	Username    string `yaml:"username"`
	Password    string `yaml:"password"`
	ClientID    string `yaml:"client_id"`
	TopicCmd    string `yaml:"topic_cmd"`
	TopicStatus string `yaml:"topic_status"`
	TLSInsecure bool   `yaml:"tls_insecure"`
}

type NodeREDConfig struct {
	BaseURL    string `yaml:"base_url"`
	Token      string `yaml:"token"`
	APIVersion string `yaml:"api_version"`
}

type SystemdConfig struct {
	User             string `yaml:"user"`
	Group            string `yaml:"group"`
	NodeRedCmd       string `yaml:"node_red_cmd"`
	InstancesBaseDir string `yaml:"instances_base_dir"`
	EnvDir           string `yaml:"env_dir"`
	ServicePath      string `yaml:"service_path"`
	WorkingDir       string `yaml:"working_dir"`
}

type HTTPConfig struct {
	ListenAddr string `yaml:"listen_addr"`
	Token      string `yaml:"token"`
}

type Config struct {
	DeviceID string        `yaml:"device_id"`
	MQTT     MQTTConfig    `yaml:"mqtt"`
	NodeRED  NodeREDConfig `yaml:"node_red"`
	Systemd  SystemdConfig `yaml:"systemd"`
	HTTP     HTTPConfig    `yaml:"http"`
}

func Load(path string) (*Config, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var c Config
	if err := yaml.Unmarshal(b, &c); err != nil {
		return nil, err
	}
	return &c, nil
}
