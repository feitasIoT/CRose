import json

from odoo import models, fields, api, _


class FtsNrNode(models.Model):
    _name = "fts.nr.node"
    _description = "Node-RED Node"

    name = fields.Char(string="Name", required=True)
    nr_id = fields.Char(string="Node ID", required=True)
    node_type = fields.Char(string="Type")
    content = fields.Text(string="Content")

    flow_id = fields.Many2one("fts.nr.flow", string="Flow", required=True, ondelete="cascade")
    instance_id = fields.Many2one(
        "fts.nr.instance",
        string="Instance",
        related="flow_id.instance_id",
        store=True,
        readonly=True,
    )
    config_node_ids = fields.Many2many(
        "fts.nr.node",
        "fts_nr_node_config_rel",
        "node_id",
        "config_node_id",
        string="Config Nodes",
    )
    item_ids = fields.One2many("fts.node.item", "node_id", string="Configuration Items")

    def _format_json_text(self, value):
        if value is None:
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, indent=2)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped or stripped[0] not in ("{", "["):
                return value
            try:
                parsed = json.loads(value)
            except Exception:
                return value
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        return value

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if "content" in vals:
                vals["content"] = self._format_json_text(vals.get("content"))
        return super().create(vals_list)

    def write(self, vals):
        if "content" in vals:
            vals["content"] = self._format_json_text(vals.get("content"))
        return super().write(vals)
