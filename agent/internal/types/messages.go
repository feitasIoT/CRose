package types

import "encoding/json"

type Command struct {
	Action     string          `json:"action"`
	APIVersion string          `json:"apiVersion"`
	DeployType string          `json:"deployType"`
	Body       json.RawMessage `json:"body"`
	Module     string          `json:"module"`
	Version    string          `json:"version"`
}

type Response struct {
	Action string `json:"action"`
	Status string `json:"status"`
	Rev    string `json:"rev,omitempty"`
	Error  string `json:"error,omitempty"`
}
