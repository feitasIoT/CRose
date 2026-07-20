from odoo import models, fields


class CroseAutomation(models.Model):
    _inherit = "base.automation"
    _description = "Flow Automation Rule"

    flow_id = fields.Many2one(
        "fts.nr.flow",
        string="Flow",
        ondelete="cascade",
        index=True,
    )
