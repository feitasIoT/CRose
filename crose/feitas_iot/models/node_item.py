from odoo import models, fields, _


class FtsNodeItem(models.Model):
    _name = "fts.node.item"
    _description = "Node Item"

    name = fields.Char(string=_("Name"))
    key = fields.Char(string="Key")
    value_type = fields.Selection(
        [
            ("text", "Text"),
            ("json", "JSON"),
        ],
        string=_("Type"),
        required=True,
        default="text",
    )
    value = fields.Text(string="Value")
    note = fields.Text(string=_("Note"))
    node_id = fields.Many2one("fts.nr.node", string=_("Node"), required=True, ondelete="cascade")

