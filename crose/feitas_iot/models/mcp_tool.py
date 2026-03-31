import json
import logging
import requests

from odoo import models, api, fields

_logger = logging.getLogger(__name__)


class McpToolRegistry(models.AbstractModel):
    _name = 'fts.mcp.tool.registry'
    _description = 'MCP Tool Registry'

    @api.model
    def list_tools(self):
        """Return the available tools in MCP format."""
        return [
            {
                "name": "list_agents",
                "description": "List all IoT Edge Agents and their status",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                }
            },
            {
                "name": "get_agent_logs",
                "description": "Get logs for a specific agent",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent_name": {"type": "string", "description": "Name of the agent"},
                        "lines": {"type": "integer", "description": "Number of log lines to retrieve (default 50)"}
                    },
                    "required": ["agent_name"]
                }
            },
            {
                "name": "restart_agent",
                "description": "Restart a specific agent",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent_name": {"type": "string", "description": "Name of the agent"}
                    },
                    "required": ["agent_name"]
                }
            }
        ]

    @api.model
    def execute_tool(self, name, arguments):
        """Execute a tool by name"""
        method_name = f'_tool_{name}'
        if hasattr(self, method_name):
            return getattr(self, method_name)(**arguments)
        raise ValueError(f"Tool {name} not found")

    def _tool_list_agents(self):
        """Return the list of all IoT Edge Agents."""
        agents = self.env['fts.edge.agent'].search([])
        result = []
        for agent in agents:
            result.append({
                "name": agent.name,
                "status": agent.status,
                "ip": agent.ip_address,
                "version": agent.version
            })
        return json.dumps(result, ensure_ascii=False)

    def _tool_get_agent_logs(self, agent_name, lines=50):
        agent = self.env['fts.edge.agent'].search([('name', '=', agent_name)], limit=1)
        if not agent:
            return f"Error: Agent '{agent_name}' not found."
        
        # Reuse logic from download.py or similar
        host = (agent.ip_address or "").strip()
        port = int(agent.agent_port or 18080)
        
        if not host:
            return "Error: Agent IP not configured."
            
        url = f"http://{host}:{port}/v1/nodered/logs"
        params = {
            "identifier": "agent",
            "limit": int(lines),
        }
        token = self.env["ir.config_parameter"].sudo().get_param("feitas_iot.agent_http_token") or ""
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
            
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=5)
            if resp.status_code != 200:
                return f"Error fetching logs: {resp.status_code} - {resp.text}"
            
            data = resp.json()
            log_lines = data.get("lines", [])
            return "\n".join(log_lines)
        except Exception as e:
            return f"Exception fetching logs: {str(e)}"

    def _tool_restart_agent(self, agent_name):
        # Placeholder for restart logic
        agent = self.env['fts.edge.agent'].search([('name', '=', agent_name)], limit=1)
        if not agent:
            return f"Error: Agent '{agent_name}' not found."
        return f"Simulated restart command sent to {agent_name}."
