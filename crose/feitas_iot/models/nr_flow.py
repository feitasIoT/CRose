import json
import re
import uuid

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


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
    automation_ids = fields.One2many("base.automation", "flow_id", string="Automation Rules")
    heat = fields.Integer("Heat")
    description = fields.Html("Description")
    prompt = fields.Text("Prompt")
    is_listed = fields.Boolean(string="Listed", default=True)
    is_valid = fields.Boolean(string="Valid", default=True)

    image = fields.Binary("Image", attachment=True)

    node_ids = fields.One2many("fts.nr.node", "flow_id", string="Nodes")
    node_count = fields.Integer(string="Node Count", compute="_compute_node_count")

    state = fields.Selection([
        ("active", "Active"),
        ("disabled", "Disabled")
    ], string="State", default="active")

    @api.depends("node_ids")
    def _compute_node_count(self):
        for record in self:
            record.node_count = len(record.node_ids)

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
        if isinstance(value, str):
            if value in mapping:
                return mapping[value]
            if value.startswith("subflow:"):
                subflow_id = value.split(":", 1)[1]
                if subflow_id in mapping:
                    return f"subflow:{mapping[subflow_id]}"
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

    def _nr_parse_content_dict(self, value):
        if value is None:
            return {}
        try:
            parsed = json.loads(value) if isinstance(value, str) else value
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _nr_collect_publish_ref_ids(self, value, out):
        ignored_keys = {"id", "z", "links", "x", "y", "wires"}
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ignored_keys:
                    continue
                self._nr_collect_publish_ref_ids(item, out)
            return
        if isinstance(value, list):
            for item in value:
                self._nr_collect_publish_ref_ids(item, out)
            return
        if isinstance(value, str):
            text = value.strip()
            if re.fullmatch(r"(?:[0-9a-fA-F]{7}\.[0-9a-fA-F]{7}|[0-9a-fA-F]{16})", text):
                out.append(text)

    def action_publish_app(self):
        """
            1. 拷贝
            2. 遍历nodes，排查node中的id、z、links、x、y、wires，其他属性的值如果类似关联id（例如：24f00ec.1ac0752、785c9c801678e4ea）
            则根据属性值去查询fts.nr.node（匹配nr_id字段，且type不是subflow的），将查询到的节点增加到nodes里。
            查询到的节点不需要递归检查。
            3. 对于subflow，看作是普通的flow进行发布。
        """
        Node = self.env["fts.nr.node"]
        for rec in self:
            app = rec.copy({
                "is_template": True,
                "instance_id": False,
                "nr_id": False,
                "is_listed": True,
                "is_valid": True,
            })
            parsed = self._nr_parse_content_dict(app.content)

            nodes = parsed.get("nodes")
            nodes = [node for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []

            ref_ids = []
            for node in nodes:
                self._nr_collect_publish_ref_ids(node, ref_ids)

            related_nodes = []
            existing_ids = {node.get("id") for node in nodes if isinstance(node.get("id"), str) and node.get("id")}
            ordered_ref_ids = []
            seen_ref_ids = set()
            for ref_id in ref_ids:
                if ref_id in seen_ref_ids or ref_id in existing_ids:
                    continue
                seen_ref_ids.add(ref_id)
                ordered_ref_ids.append(ref_id)

            if ordered_ref_ids:
                domain = [
                    ("nr_id", "in", ordered_ref_ids),
                    ("node_type", "!=", "subflow"),
                ]
                if rec.instance_id:
                    domain.append(("instance_id", "=", rec.instance_id.id))
                related_records = Node.search(domain)
                record_by_nr_id = {}
                for record in related_records:
                    if record.nr_id and record.nr_id not in record_by_nr_id:
                        record_by_nr_id[record.nr_id] = record

                for ref_id in ordered_ref_ids:
                    record = record_by_nr_id.get(ref_id)
                    if not record:
                        continue
                    related_node = self._nr_parse_content_dict(record.content)
                    related_node_id = related_node.get("id")
                    if not related_node_id or related_node_id in existing_ids:
                        continue
                    related_nodes.append(related_node)
                    existing_ids.add(related_node_id)

            parsed["nodes"] = nodes + related_nodes
            parsed.pop("configs", None)
            parsed.pop("subflow_deps", None)
            app.write({"content": parsed})

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
