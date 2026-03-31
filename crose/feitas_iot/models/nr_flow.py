import json

from odoo import models, fields, api, _


class FtsNrFlow(models.Model):
    _name = "fts.nr.flow"
    _description = "Node-RED Flow"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string=_("Name"), required=True)
    nr_id = fields.Char(string="Flow ID", required=True)
    type = fields.Char(string=_("Type"))
    is_template = fields.Boolean(_("Is Template"))
    content = fields.Text(_("Content"))

    instance_id = fields.Many2one("fts.nr.instance", string=_("Instance"), ondelete="cascade")
    data_model_id = fields.Many2one('fts.data.model', string="Data Model")

    tag_ids = fields.Many2many("fts.nr.tag", string=_("Tags"))
    param_ids = fields.One2many("fts.nr.flow.param", "flow_id", string=_("Parameters"))
    heat = fields.Integer(_("Heat"))
    description = fields.Html(_("Description"))
    prompt = fields.Text(_("Prompt"))

    def action_sync_to_knowledge(self):
        """Synchronize selected flows to the knowledge base and vectorize them."""
        Knowledge = self.env['fts.knowledge']
        created_records = Knowledge.browse()
        for record in self:
            vals = {
                'name': f"Flow: {record.name}",
                'description': record.description or '',
                'json_source': record.content,
            }
            created_records |= Knowledge.create(vals)

        created_records.action_vectorize()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Synchronization Successful'),
                'message': _('Synchronized %(count)s flows to the knowledge base and completed vectorization.', count=len(self)),
                'sticky': False,
            }
        }

    def action_view_nodes(self):
        """Open flow nodes together with their related config nodes."""
        self.ensure_one()
        action = self.env.ref("feitas_iot.action_fts_nr_node", raise_if_not_found=False)
        if action:
            res = action.read()[0]
            res["display_name"] = _("Nodes")
            res["name"] = _("Nodes")
            Node = self.env["fts.nr.node"]
            nodes = Node.search([("flow_id", "=", self.id)])
            to_process = nodes
            seen_ids = set(nodes.ids)
            config_ids = set()
            while to_process:
                cfgs = to_process.mapped("config_node_ids")
                new_cfgs = cfgs.filtered(lambda r: r.id not in seen_ids)
                if not new_cfgs:
                    break
                new_ids = set(new_cfgs.ids)
                config_ids |= new_ids
                seen_ids |= new_ids
                to_process = new_cfgs

            if config_ids:
                res["domain"] = ["|", ("flow_id", "=", self.id), ("id", "in", sorted(config_ids))]
            else:
                res["domain"] = [("flow_id", "=", self.id)]
            res["context"] = {
                "default_flow_id": self.id,
            }
            return res
        return {}

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
