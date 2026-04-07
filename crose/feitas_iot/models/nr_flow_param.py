from odoo import models, fields

class FtsNrFlowParam(models.Model):
    _name = "fts.nr.flow.param"
    _description = "Node-RED Flow Parameter"

    name = fields.Char(string="Parameter Name", required=True)
    value = fields.Char(string="Value")
    type = fields.Selection([
        ('str', 'String'),
        ('num', 'Number'),
        ('bool', 'Boolean'),
        ('json', 'JSON'),
        ('env', 'Environment Variable')
    ], string="Type", default='str', required=True)
    description = fields.Char(string="Description")

    flow_id = fields.Many2one("fts.nr.flow", string="Related Flow", ondelete="cascade")
    model_id = fields.Many2one("fts.data.model", string="Related Data Model", ondelete="cascade")
