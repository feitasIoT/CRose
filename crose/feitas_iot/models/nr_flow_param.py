from odoo import models, fields, _

class FtsNrFlowParam(models.Model):
    _name = "fts.nr.flow.param"
    _description = "Node-RED Flow Parameter"

    name = fields.Char(string=_("Parameter Name"), required=True)
    value = fields.Char(string=_("Value"))
    type = fields.Selection([
        ('str', 'String'),
        ('num', 'Number'),
        ('bool', 'Boolean'),
        ('json', 'JSON'),
        ('env', 'Environment Variable')
    ], string=_("Type"), default='str', required=True)
    description = fields.Char(string=_("Description"))
    
    flow_id = fields.Many2one("fts.nr.flow", string=_("Related Flow"), ondelete="cascade")
    model_id = fields.Many2one("fts.data.model", string=_("Related Data Model"), ondelete="cascade")
