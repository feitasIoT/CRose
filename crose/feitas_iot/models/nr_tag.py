from odoo import models, fields, _

class FtsNrTag(models.Model):
    _name = "fts.nr.tag"
    _description = "Node-RED Tag"

    name = fields.Char(string=_("Tag"), required=True)
    color = fields.Integer(string=_("Color Index"))
