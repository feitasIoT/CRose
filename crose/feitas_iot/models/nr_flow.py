import json
import uuid

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from odoo.tools import html2plaintext


class FtsNrFlow(models.Model):
    _name = "fts.nr.flow"
    _description = "Node-RED Flow"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="Name", required=True)
    nr_id = fields.Char(string="Flow ID", required=False)
    type = fields.Char(string="Type")
    is_template = fields.Boolean("Is Template")
    content = fields.Text("Content")
    app_store_id = fields.Many2one("fts.nr.flow", string="App")

    instance_id = fields.Many2one("fts.nr.instance", string="Instance", ondelete="cascade")
    data_model_id = fields.Many2one('fts.data.model', string="Data Model")

    tag_ids = fields.Many2many("fts.nr.tag", string="Tags")
    param_ids = fields.One2many("fts.nr.flow.param", "flow_id", string="Parameters")
    heat = fields.Integer("Heat")
    description = fields.Html("Description")
    prompt = fields.Text("Prompt")

    image = fields.Binary("Image", attachment=True)

    node_ids = fields.One2many("fts.nr.node", "flow_id", string="Nodes")

    state = fields.Selection([
        ("active", "Active"),
        ("disabled", "Disabled")
    ], string="State", default="active")

    def _nr_generate_id(self):
        return f"{uuid.uuid4().hex[:7]}.{uuid.uuid4().hex[:7]}"

    def _nr_collect_ids(self, value, out):
        if isinstance(value, dict):
            node_id = value.get("id")
            if isinstance(node_id, str) and node_id:
                out.add(node_id)
            for v in value.values():
                self._nr_collect_ids(v, out)
            return
        if isinstance(value, list):
            for v in value:
                self._nr_collect_ids(v, out)

    def _nr_replace_ids(self, value, mapping):
        if isinstance(value, dict):
            replaced = {}
            for k, v in value.items():
                new_k = mapping.get(k, k) if isinstance(k, str) else k
                replaced[new_k] = self._nr_replace_ids(v, mapping)
            return replaced
        if isinstance(value, list):
            return [self._nr_replace_ids(v, mapping) for v in value]
        if isinstance(value, str) and value in mapping:
            return mapping[value]
        return value

    def _nr_regenerate_content_ids(self):
        self.ensure_one()
        raw = self.content or ""
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return raw

        if not isinstance(parsed, (dict, list)):
            return raw

        old_ids = set()
        self._nr_collect_ids(parsed, old_ids)
        if not old_ids:
            return parsed

        mapping = {}
        used_new = set()
        for old in old_ids:
            new = self._nr_generate_id()
            while new in used_new:
                new = self._nr_generate_id()
            mapping[old] = new
            used_new.add(new)

        return self._nr_replace_ids(parsed, mapping)

    def action_sync_to_knowledge(self):
        Document = self.env["fts.ai.knowledge.document"]
        vals_list = []
        for record in self:
            description = html2plaintext(record.description or "").strip()
            lines = [
                f"Flow Name: {record.name or ''}",
                f"Flow ID: {record.nr_id or ''}",
                f"Flow Type: {record.type or ''}",
                "",
                "Flow Prompt:",
                (record.prompt or "").strip(),
                "",
                "Flow Description:",
                description,
                "",
                "Flow JSON:",
                (record.content or "").strip(),
            ]
            raw_text = "\n".join([l for l in lines if l is not None]).strip()
            if not raw_text:
                continue
            vals_list.append(
                {
                    "name": f"Flow: {record.name}",
                    "source_type": "text",
                    "raw_text": raw_text,
                }
            )
        created_records = Document.create(vals_list) if vals_list else Document.browse()
        if created_records:
            created_records.action_split_and_vectorize()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Synchronization Successful'),
                'message': _('Synchronized %(count)s flows to knowledge documents and completed vectorization.', count=len(created_records)),
                'sticky': False,
            }
        }

    def action_view_nodes(self):
        """Open flow nodes together with their related config nodes."""
        self.ensure_one()

        return {
            "type": "ir.actions.act_window",
            "name": _("Flow Nodes"),
            "res_model": "fts.nr.node",
            "view_mode": "list,form",
            "target": "current",
            "domain": [('flow_id', '=', self.id)],
            "context": {},
        }

    def _build_atomic_user_text(self):
        self.ensure_one()
        lines = [
            f"Flow Name: {self.name or ''}",
            f"Flow ID: {self.nr_id or ''}",
            f"Flow Type: {self.type or ''}",
            "",
            "Flow Prompt:",
            self.prompt or "",
            "",
            "Flow Description:",
            self.description or "",
        ]
        return "\n".join(lines).strip()

    def _build_atomic_assistant_text(self):
        self.ensure_one()
        return self.content or ""

    def action_convert_to_atomic_messages(self):
        Prompt = self.env["fts.ai.prompt"]
        Message = self.env["fts.ai.dataset.message"]
        templates = Prompt.search([("is_template", "=", True)])
        if not templates:
            raise ValidationError(_("No template prompts were found. Please create at least one prompt with Template Prompt enabled."))
        created = Message.browse()
        for flow in self:
            user_text = flow._build_atomic_user_text()
            assistant_text = flow._build_atomic_assistant_text()
            for prompt in templates:
                values = {
                    "format": "ChatML",
                    "system": prompt.content or "",
                    "user": user_text,
                    "assistant": assistant_text,
                    "category_ids": [(6, 0, prompt.category_ids.ids)],
                }
                created |= Message.create(values)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Atomic Messages Created"),
                "message": _("%(count)s atomic messages were created from %(flow_count)s flows.", count=len(created), flow_count=len(self)),
                "type": "success",
                "sticky": False,
            },
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

    def action_publish_app(self):
        """
            发布到app store。 复制->修改
        """
        for rec in self:
            app = rec.copy({
                "is_template": True,
                "instance_id": False,
                "nr_id": False
            })
            regenerated = app._nr_regenerate_content_ids()
            if regenerated != (app.content or ""):
                app.write({"content": regenerated})
            rec.write({
                "app_store_id": app.id
            })

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
