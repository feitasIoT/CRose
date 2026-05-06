package handler

import (
	"context"
	"encoding/json"

	"mygoagent/internal/nodered"
	"mygoagent/internal/orchestrator"
	"mygoagent/internal/types"
)

type Processor struct {
	nr   *nodered.Client
	orch *orchestrator.SystemdOrchestrator
}

func NewProcessor(nr *nodered.Client, orch *orchestrator.SystemdOrchestrator) *Processor {
	return &Processor{nr: nr, orch: orch}
}

func (p *Processor) Handle(payload []byte) (string, error) {
	var cmd types.Command
	if err := json.Unmarshal(payload, &cmd); err != nil {
		return `{"status":"error","error":"invalid_json"}`, nil
	}
	switch cmd.Action {
	case "deploy_flows":
		rev, err := p.nr.DeployFlows(cmd.Body, cmd.DeployType, cmd.APIVersion)
		if err != nil {
			b, _ := json.Marshal(types.Response{Action: cmd.Action, Status: "error", Error: err.Error()})
			return string(b), nil
		}
		b, _ := json.Marshal(types.Response{Action: cmd.Action, Status: "ok", Rev: rev})
		return string(b), nil
	case "update_node":
		err := p.nr.InstallNode(cmd.Module, cmd.Version)
		if err != nil {
			b, _ := json.Marshal(types.Response{Action: cmd.Action, Status: "error", Error: err.Error()})
			return string(b), nil
		}
		b, _ := json.Marshal(types.Response{Action: cmd.Action, Status: "ok"})
		return string(b), nil
	case "create_instance": // 创建实例
		if p.orch == nil {
			b, _ := json.Marshal(types.Response{Action: cmd.Action, Status: "error", Error: "orchestrator_not_configured"})
			return string(b), nil
		}
		var req orchestrator.CreateInstanceRequest
		if err := json.Unmarshal(cmd.Body, &req); err != nil {
			b, _ := json.Marshal(types.Response{Action: cmd.Action, Status: "error", Error: "invalid_body"})
			return string(b), nil
		}
		if err := p.orch.CreateInstance(context.Background(), req); err != nil {
			b, _ := json.Marshal(types.Response{Action: cmd.Action, Status: "error", Error: err.Error()})
			return string(b), nil
		}
		b, _ := json.Marshal(types.Response{Action: cmd.Action, Status: "ok"})
		return string(b), nil
	default:
		return `{"status":"error","error":"unknown_action"}`, nil
	}
}
