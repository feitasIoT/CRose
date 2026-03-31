import json

from odoo import models, fields, api, _


class FtsNrNode(models.Model):
    _name = "fts.nr.node"
    _description = "Node-RED Node"

    name = fields.Char(string=_("Name"), required=True)
    nr_id = fields.Char(string="Node ID", required=True)
    node_type = fields.Char(string=_("Type"))
    content = fields.Text(string=_("Content"))

    flow_id = fields.Many2one("fts.nr.flow", string=_("Flow"), required=True, ondelete="cascade")
    instance_id = fields.Many2one(
        "fts.nr.instance",
        string=_("Instance"),
        related="flow_id.instance_id",
        store=True,
        readonly=True,
    )
    config_node_ids = fields.Many2many(
        "fts.nr.node",
        "fts_nr_node_config_rel",
        "node_id",
        "config_node_id",
        string=_("Config Nodes"),
    )
    item_ids = fields.One2many("fts.node.item", "node_id", string=_("Configuration Items"))

    def action_sync_to_knowledge(self):
        """Synchronize selected nodes to the knowledge base and vectorize them."""
        Knowledge = self.env['fts.knowledge']
        created_records = Knowledge.browse()
        for record in self:
            vals = {
                'name': f"Node: {record.name}",
                'description': f"Type: {record.node_type}",
                'json_source': record.content,
            }
            created_records |= Knowledge.create(vals)

        created_records.action_vectorize()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Synchronization Successful'),
                'message': _('Synchronized %(count)s nodes to the knowledge base and completed vectorization.', count=len(self)),
                'sticky': False,
            }
        }

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
