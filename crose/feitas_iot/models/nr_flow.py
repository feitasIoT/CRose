import json
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

    def action_publish_app(self):
        """
            发布到app store。 复制->修改
        """
        for rec in self:
            app = rec.copy({
                "is_template": True,
                "instance_id": False,
                "nr_id": False,
                "is_listed": True,
                "is_valid": True,
            })
            raw = app.content or "{}"
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                parsed = {}
            if not isinstance(parsed, dict):
                parsed = {}

            nodes = parsed.get("nodes")
            nodes = nodes if isinstance(nodes, list) else []
            subflow_ids = set()
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                node_type = node.get("type")
                if isinstance(node_type, str) and node_type.startswith("subflow:"):
                    sid = node_type.split(":", 1)[1]
                    if sid:
                        subflow_ids.add(sid)

            if subflow_ids and rec.instance_id:
                Flow = self.env["fts.nr.flow"]
                deps = []
                existing_deps = parsed.get("subflow_deps")
                if isinstance(existing_deps, list):
                    deps.extend([d for d in existing_deps if isinstance(d, dict)])

                existing_ids = set()
                for dep in deps:
                    subflow_def = dep.get("subflow") if isinstance(dep, dict) else None
                    if isinstance(subflow_def, dict) and isinstance(subflow_def.get("id"), str):
                        existing_ids.add(subflow_def.get("id"))

                for sid in sorted(subflow_ids):
                    if sid in existing_ids:
                        continue
                    subflow_flow = Flow.search(
                        [
                            ("instance_id", "=", rec.instance_id.id),
                            ("type", "=", "subflow"),
                            ("nr_id", "=", sid),
                        ],
                        limit=1,
                    )
                    if not subflow_flow or not subflow_flow.content:
                        continue
                    try:
                        dep_parsed = json.loads(subflow_flow.content)
                    except Exception:
                        dep_parsed = None
                    if isinstance(dep_parsed, dict):
                        deps.append(dep_parsed)

                if deps:
                    global_flow = Flow.search(
                        [
                            ("instance_id", "=", rec.instance_id.id),
                            ("type", "=", "global"),
                            ("nr_id", "=", "global"),
                        ],
                        limit=1,
                    )
                    global_by_id = {}
                    if global_flow and global_flow.content:
                        try:
                            global_parsed = json.loads(global_flow.content)
                        except Exception:
                            global_parsed = None
                        if isinstance(global_parsed, dict):
                            candidates = []
                            for key in ("configs", "nodes", "subflows"):
                                part = global_parsed.get(key)
                                if isinstance(part, list):
                                    candidates.extend([i for i in part if isinstance(i, dict) and i.get("id")])
                            global_by_id = {i["id"]: i for i in candidates if isinstance(i.get("id"), str)}

                    def _collect_strings(value, out):
                        if isinstance(value, dict):
                            for v in value.values():
                                _collect_strings(v, out)
                        elif isinstance(value, list):
                            for v in value:
                                _collect_strings(v, out)
                        elif isinstance(value, str):
                            out.add(value)

                    def _is_config_node(item):
                        return (
                            isinstance(item, dict)
                            and item.get("id")
                            and item.get("type") not in ("tab", "subflow")
                            and "wires" not in item
                        )

                    if global_by_id:
                        def _resolve_configs_from_refs(refs, existing_ids):
                            queue = [rid for rid in refs if rid in global_by_id]
                            seen = set()
                            resolved = []
                            while queue:
                                rid = queue.pop(0)
                                if rid in seen or rid in existing_ids:
                                    continue
                                item = global_by_id.get(rid)
                                if not item:
                                    continue
                                seen.add(rid)
                                if _is_config_node(item):
                                    resolved.append(item)
                                    existing_ids.add(rid)
                                    nested = set()
                                    _collect_strings(item, nested)
                                    for nid in nested:
                                        if nid in global_by_id and nid not in seen and nid not in existing_ids:
                                            queue.append(nid)
                            return resolved

                        for dep in deps:
                            configs = dep.get("configs")
                            if not isinstance(configs, list):
                                configs = []
                            configs = [c for c in configs if isinstance(c, dict) and c.get("id")]
                            config_ids = {c.get("id") for c in configs if isinstance(c.get("id"), str)}

                            refs = set()
                            _collect_strings(dep, refs)
                            configs.extend(_resolve_configs_from_refs(refs, config_ids))

                            dep["configs"] = configs

                        main_configs = parsed.get("configs")
                        if not isinstance(main_configs, list):
                            main_configs = []
                        main_configs = [c for c in main_configs if isinstance(c, dict) and c.get("id")]
                        main_config_ids = {c.get("id") for c in main_configs if isinstance(c.get("id"), str)}

                        refs = set()
                        _collect_strings(parsed.get("nodes") or [], refs)
                        main_configs.extend(_resolve_configs_from_refs(refs, main_config_ids))
                        parsed["configs"] = main_configs

                    parsed["subflow_deps"] = deps
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
