from odoo import models, fields, _


class InstanceFlowLine(models.Model):
    _name = "instance.flow.line"
    _description = "Instance Flow Line"

    instance_id = fields.Many2one("fts.nr.instance", string=_("Instance"), required=True, ondelete="cascade")
    flow_id = fields.Many2one("fts.nr.flow", string=_("Flow"), required=True, ondelete="restrict")
