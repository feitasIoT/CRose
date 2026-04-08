import json

from odoo import models, fields, api
from odoo.exceptions import UserError


class FtsNrInstanceWizard(models.TransientModel):
    _name = "fts.nr.instance.wizard"
    _description = "Manage Instance Flows"

    operation = fields.Selection(
        [
            ("add", "Add Flows from Template"),
            ("disable", "Disable Flows"),
            ("delete", "Delete Flows"),
        ],
        string="Operation",
        required=True,
        default="add",
    )
    instance_id = fields.Many2one(
        "fts.nr.instance",
        string="Instance",
        required=True,
        ondelete="cascade",
        default=lambda self: self.env.context.get("active_id"),
    )
    template_flow_ids = fields.Many2many(
        "fts.nr.flow",
        "wizard_instance_template_rel",
        "wizard_id",
        "flow_id",
        string="Template Flows",
        domain=[("is_template", "=", True)],
    )
    target_flow_ids = fields.Many2many(
        "fts.nr.flow",
        "wizard_instance_target_rel",
        "wizard_id",
        "flow_id",
        string="Target Flows",
        domain="[('instance_id', '=', instance_id), ('is_template', '=', False)]",
    )

    def _nr_candidate_base_urls(self):
        self.ensure_one()
        inst = self.instance_id
        host = (inst.ip_address or "").strip()
        port = int(inst.port or 1880)
        if not host:
            return []
        if host.startswith("http://"):
            host = host[7:]
        elif host.startswith("https://"):
            host = host[8:]
        if "/" in host:
            host = host.split("/", 1)[0]
        if ":" in host:
            parts = host.rsplit(":", 1)
            if parts[-1].isdigit():
                host = parts[0]
                port = int(parts[-1])
        return [f"http://{host}:{port}"]

    def _nr_post_json(self, path, body, timeout=15):
        import requests

        headers = {"Node-RED-API-Version": "v2"}
        last_error = None
        for base_url in self._nr_candidate_base_urls():
            url = f"{base_url}{path}"
            try:
                response = requests.post(url, headers=headers, json=body, timeout=timeout)
                response.raise_for_status()
                try:
                    return response.json()
                except Exception:
                    return {}
            except Exception as e:
                last_error = e
        raise UserError(f"Node-RED request failed: {last_error}")

    def _nr_delete_json(self, path, timeout=15):
        import requests

        headers = {"Node-RED-API-Version": "v2"}
        last_error = None
        for base_url in self._nr_candidate_base_urls():
            url = f"{base_url}{path}"
            try:
                response = requests.delete(url, headers=headers, timeout=timeout)
                response.raise_for_status()
                return True
            except Exception as e:
                last_error = e
        raise UserError(f"Node-RED delete failed: {last_error}")

    def _nr_disable_flow(self, flow_nr_id):
        payload = {"id": flow_nr_id, "disabled": True}
        self._nr_post_json("/flow", payload)

    def _nr_enable_flow(self, flow_nr_id):
        payload = {"id": flow_nr_id, "disabled": False}
        self._nr_post_json("/flow", payload)

    def _nr_delete_flow(self, flow_nr_id):
        self._nr_delete_json(f"/flow/{flow_nr_id}")

    def _collect_strings(self, value, out):
        if isinstance(value, dict):
            for v in value.values():
                self._collect_strings(v, out)
        elif isinstance(value, list):
            for v in value:
                self._collect_strings(v, out)
        elif isinstance(value, str):
            out.add(value)

    def _resolve_global_configs(self, instance, refs):
        if not instance or not refs:
            return []
        global_flow = self.env["fts.nr.flow"].search(
            [("instance_id", "=", instance.id), ("nr_id", "=", "global")],
            limit=1,
        )
        if not global_flow or not global_flow.content:
            return []
        try:
            parsed = json.loads(global_flow.content)
        except Exception:
            return []
        if not isinstance(parsed, dict):
            return []
        candidates = []
        for key in ("configs", "subflows", "nodes"):
            part = parsed.get(key)
            if isinstance(part, list):
                candidates.extend([i for i in part if isinstance(i, dict) and i.get("id")])
        by_id = {i["id"]: i for i in candidates}
        queue = [rid for rid in refs if rid in by_id]
        selected = {}
        while queue:
            rid = queue.pop(0)
            if rid in selected:
                continue
            node = by_id.get(rid)
            if not node:
                continue
            selected[rid] = node
            nested_refs = set()
            self._collect_strings(node, nested_refs)
            for nested in nested_refs:
                if nested in by_id and nested not in selected:
                    queue.append(nested)
        return list(selected.values())

    def _build_flow_payload(self, flow):
        # 优先复用源实例已有的完整构建逻辑
        source_instance = flow.instance_id
        if source_instance:
            return source_instance._nr_build_flow_payload(flow)

        raw = flow.content or "{}"
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            parsed = {}

        nodes = parsed.get("nodes", []) if isinstance(parsed, dict) else []
        configs = parsed.get("configs", []) if isinstance(parsed, dict) else []
        nodes = [n for n in nodes if isinstance(n, dict)]
        configs = [c for c in configs if isinstance(c, dict) and c.get("id")]

        if not configs:
            refs = set()
            self._collect_strings(nodes, refs)
            global_configs = self._resolve_global_configs(self.instance_id, refs)
            cfg_ids = {c.get("id") for c in configs if c.get("id")}
            for cfg in global_configs:
                cfg_id = cfg.get("id")
                if cfg_id and cfg_id not in cfg_ids:
                    configs.append(cfg)
                    cfg_ids.add(cfg_id)

        return {
            "id": self.instance_id._nr_generate_id(),
            "label": flow.name or "",
            "nodes": nodes,
            "configs": configs,
        }

    def action_confirm(self):
        self.ensure_one()
        if self.operation == "add":
            if not self.template_flow_ids:
                raise UserError("Please select at least one template flow to add.")
            created = []
            for tmpl in self.template_flow_ids:
                payload = self._build_flow_payload(tmpl)
                result = self._nr_post_json("/flow", payload)
                new_nr_id = result.get("id") if isinstance(result, dict) else None
                if not new_nr_id:
                    new_nr_id = payload["id"]
                new_flow = self.env["fts.nr.flow"].create(
                    {
                        "name": f"{tmpl.name} - {self.instance_id.name}",
                        "nr_id": new_nr_id,
                        "type": tmpl.type,
                        "is_template": False,
                        "instance_id": self.instance_id.id,
                        "content": tmpl.content,
                    }
                )

                created.append(new_flow.display_name)

            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Add Complete",
                    "message": f"Created {len(created)} flow(s): {', '.join(created)}",
                    "type": "success",
                    "sticky": False,
                },
            }

        elif self.operation == "disable":
            if not self.target_flow_ids:
                raise UserError("Please select at least one flow to disable.")
            for flow in self.target_flow_ids:
                if flow.nr_id:
                    try:
                        self._nr_disable_flow(flow.nr_id)
                    except Exception:
                        pass
                flow.write({"state": "disabled"})
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Disable Complete",
                    "message": f"{len(self.target_flow_ids)} flow(s) disabled.",
                    "type": "success",
                    "sticky": False,
                },
            }

        elif self.operation == "delete":
            if not self.target_flow_ids:
                raise UserError("Please select at least one flow to delete.")
            deleted = []
            for flow in self.target_flow_ids:
                if flow.nr_id:
                    try:
                        self._nr_delete_flow(flow.nr_id)
                    except Exception:
                        pass
                deleted.append(flow.display_name)
                flow.unlink()
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Delete Complete",
                    "message": f"Deleted {len(deleted)} flow(s).",
                    "type": "success",
                    "sticky": False,
                },
            }
