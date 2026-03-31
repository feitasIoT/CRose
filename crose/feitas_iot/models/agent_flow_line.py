from odoo import models, fields, _


class AgentFlowLine(models.Model):
    _name = "agent.flow.line"
    _description = "Agent Flow Line"

    agent_id = fields.Many2one("fts.edge.agent", string="Agent", required=True, ondelete="cascade")
    flow_id = fields.Many2one("fts.nr.flow", string=_("Flow"), required=True, ondelete="restrict")
