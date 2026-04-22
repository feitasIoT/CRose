import json
import re
import uuid

import requests

from odoo import models, fields, _
from odoo.exceptions import UserError, ValidationError


class FtsNrInstance(models.Model):
    _name = "fts.nr.instance"
    _description = "IoT Instance"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="Name", required=True)
    ip_address = fields.Char(string="IP Address", required=True)
    port = fields.Integer(string="Port", required=True, default=1880)
    editor_port = fields.Integer(string="Editor Port")
    instance_type = fields.Selection(
        [
            ("local", "Local Instance"),
            ("remote", "Remote Instance"),
        ],
        string="Instance Type",
        required=True,
        default="local",
    )
    edge_node_id = fields.Many2one(
        "fts.edge.node",
        string="Edge Node",
        ondelete="restrict",
        required=False,
    )
    status = fields.Selection(
        [
            ("online", "Online"),
            ("offline", "Offline"),
            ("error", "Error"),
        ],
        string="Status",
        required=True,
        default="offline",
    )
    flow_ids = fields.One2many("fts.nr.flow", "instance_id", string="Flows")
    flow_line_ids = fields.One2many("instance.flow.line", "instance_id", string="Flow Lines")
    npm_registry_id = fields.Many2one("crose.component", string="NPM Registry", domain=[('component_type', '=', 'npm')])

    def update_status(self):
        for instance in self:
            if not instance.ip_address:
                instance.status = "offline"
                continue

            url = "http://%s:%d" % (instance.ip_address, instance.port)
            try:
                response = requests.get(url, timeout=3)
                if 200 <= response.status_code < 400:
                    instance.status = "online"
                else:
                    instance.status = "error"
            except Exception:
                instance.status = "offline"

    def _sync_nr_nodes_for_flow(
        self,
        flow_record,
        flow_detail,
        *,
        include_nodes=True,
        include_configs=False,
        include_subflows=False,
        global_nodes_by_nr_id=None,
    ):
        self.ensure_one()
        Node = self.env["fts.nr.node"]
        if not flow_record:
            return
        items = []
        if include_nodes:
            items.extend(flow_detail.get("nodes") or [])
        if include_configs:
            items.extend(flow_detail.get("configs") or [])
        if include_subflows:
            items.extend(flow_detail.get("subflows") or [])
        items = [i for i in items if isinstance(i, dict) and i.get("id")]
        nr_ids = [i["id"] for i in items]
        existing = Node.search([("flow_id", "=", flow_record.id)])
        existing_by_nr_id = {rec.nr_id: rec for rec in existing}

        def _collect_strings(value, out):
            if isinstance(value, dict):
                for v in value.values():
                    _collect_strings(v, out)
            elif isinstance(value, list):
                for v in value:
                    _collect_strings(v, out)
            elif isinstance(value, str):
                out.add(value)

        to_create = []
        for node in items:
            nr_id = node["id"]
            node_type = node.get("type")
            name = node.get("name") or node.get("label") or node_type or nr_id
            vals = {
                "name": name,
                "nr_id": nr_id,
                "node_type": node_type,
                "content": json.dumps(node, ensure_ascii=False),
                "flow_id": flow_record.id,
            }
            if global_nodes_by_nr_id is not None:
                strings = set()
                _collect_strings(node, strings)
                config_ids = sorted({global_nodes_by_nr_id[s] for s in strings if s in global_nodes_by_nr_id})
                vals["config_node_ids"] = [(6, 0, config_ids)]
            rec = existing_by_nr_id.get(nr_id)
            if rec:
                rec.write(vals)
            else:
                to_create.append(vals)
        if to_create:
            Node.create(to_create)
        stale = existing.filtered(lambda r: r.nr_id not in set(nr_ids))
        if stale:
            stale.unlink()

    def action_restart(self):
        return True

    def action_create(self):
        """
            Create a remote instance.
        """
        pass

    def action_start(self):
        """
        Start the instance.
        """
        self.ensure_one()
        if not self.edge_node_id:
            raise UserError(_("A remote instance must have an edge agent before it can be started."))

        config = self.env["ir.config_parameter"].sudo()
        publish_url = config.get_param("feitas_iot.gmqtt_publish_url") or ""
        if not publish_url:
            server_ip = config.get_param("feitas_iot.gmqtt_server_ip") or "127.0.0.1"
            server_port = config.get_param("feitas_iot.gmqtt_server_port") or "8083"
            publish_url = f"http://{server_ip}:{server_port}/v1/publish"
        publish_url = str(publish_url)

        body = {
            "topic_name": f"agent/create/{self.edge_node_id.id}",
            "payload": json.dumps(
                {
                    "instance_id": self.id,
                    "instance_name": self.name,
                    "instance_type": self.instance_type,
                    "ip_address": self.ip_address,
                    "port": self.port,
                },
                ensure_ascii=False,
            ),
            "qos": 1,
            "retained": False,
        }

        try:
            response = requests.post(publish_url, json=body, timeout=15)
            response.raise_for_status()
        except Exception as e:
            raise UserError(_("Failed to call the GMQTT publish API: %(error)s", error=str(e)))

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Start Submitted"),
                "message": _("Published to %(topic)s", topic=body['topic_name']),
                "type": "success",
                "sticky": False,
            },
        }

    def action_open_editor(self):
        self.ensure_one()
        action = self.env.ref("feitas_iot.action_node_red_editor_client", raise_if_not_found=False)
        if action:
            res = action.read()[0]
            res["display_name"] = _("Node-RED Editor")
            res["name"] = _("Node-RED Editor")
            editor_port = self.editor_port or self.port
            res['params'] = {
                'instance_id': self.id,
                'node_red_url': f"http://{self.ip_address}:{editor_port}",
            }
            return res
        return {}

    def action_view_flows(self):
        self.ensure_one()
        flows = self.flow_ids
        if not flows:
            raise ValidationError(_("No flows are linked to this instance."))
        if len(flows) == 1:
            return {
                "type": "ir.actions.act_window",
                "name": _("Flow"),
                "res_model": "fts.nr.flow",
                "view_mode": "form",
                "target": "current",
                "res_id": flows.id,
                "context": {},
            }
        return {
            "type": "ir.actions.act_window",
            "name": _("Flows"),
            "res_model": "fts.nr.flow",
            "view_mode": "list,form",
            "target": "current",
            "domain": [("id", "in", flows.ids)],
            "context": {},
        }

    def action_view_logs(self):
        self.ensure_one()
        if not self.edge_node_id:
            raise UserError(_("This instance has no edge agent configured, so runtime logs cannot be read."))
        action = self.env.ref("feitas_iot.action_node_red_logs_client", raise_if_not_found=False)
        if not action:
            raise UserError(_("The log action was not found. Please contact your administrator."))
        res = action.read()[0]
        res["display_name"] = _("Logs")
        res["name"] = _("Logs")
        res["params"] = {
            "instance_id": self.id,
        }
        return res

    def action_test(self):
        self.ensure_one()
        tried = []
        for base_url in self._nr_candidate_base_urls():
            try:
                response = requests.get(base_url, timeout=5)
                if 200 <= response.status_code < 400:
                    self.status = "online"
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': _('Connection Successful'),
                            'message': _('The Node-RED instance is reachable: %(url)s', url=base_url),
                            'type': 'success',
                            'sticky': False,
                        }
                    }
                tried.append(f"{base_url} -> {response.status_code}")
            except Exception as e:
                tried.append(f"{base_url} -> {str(e)}")

        self.status = "offline"
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Connection Error'),
                'message': _('Unable to connect to Node-RED. Tried: %(detail)s', detail=" | ".join(tried[:3])),
                'type': 'danger',
                'sticky': False,
            }
        }

    def action_apply_flows(self):
        """
            Push the selected applications of the instance to Node-RED.
        """
        self.ensure_one()
        if not self.flow_ids:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Application Complete"),
                    "message": _("No applications are configured."),
                    "type": "warning",
                    "sticky": False,
                },
            }

        ok_count = 0
        fail_count = 0
        error_messages = []

        for line in self.flow_ids:
            flow = line.flow_id
            if not flow:
                continue
            try:
                payload = self._nr_build_flow_payload(flow)
                self._nr_post_json("/flow", payload, timeout=30)
                ok_count += 1
            except Exception as e:
                fail_count += 1
                error_messages.append(f"{flow.display_name}: {str(e)}")

        message = _("Success: %(ok)s, Failed: %(fail)s", ok=ok_count, fail=fail_count)
        if error_messages:
            message = f"{message}\n" + "\n".join(error_messages[:5])

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Application Complete"),
                "message": message,
                "type": "success" if fail_count == 0 else "warning",
                "sticky": False,
            },
        }

    def api_sync_flows(self):
        """
            Synchronize all flows of the instance.
        """
        if not self:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Synchronization Complete'),
                    'message': _('No instances were selected.'),
                    'type': 'warning',
                    'sticky': False,
                }
            }

        Flow = self.env['fts.nr.flow']
        Node = self.env["fts.nr.node"]
        ok_count = 0
        fail_count = 0
        error_messages = []

        for instance in self:
            try:
                flow_list = instance._nr_get_json('/flows')
                if isinstance(flow_list, dict):
                    flow_nodes = flow_list.get('flows') or []
                else:
                    flow_nodes = flow_list or []

                tabs = [
                    node for node in flow_nodes
                    if isinstance(node, dict) and node.get('type') == 'tab' and node.get('id')
                ]
                tab_ids = [t['id'] for t in tabs]

                subflows = [
                    node for node in flow_nodes
                    if isinstance(node, dict) and node.get('type') == 'subflow' and node.get('id')
                ]
                subflow_ids = [s['id'] for s in subflows]

                config_nodes = [
                    node for node in flow_nodes
                    if (
                        isinstance(node, dict)
                        and node.get('id')
                        and node.get('type') not in ('tab', 'subflow')
                        and 'wires' not in node
                    )
                ]
                config_by_id = {c['id']: c for c in config_nodes if isinstance(c.get('id'), str)}

                nodes_by_z = {}
                for node in flow_nodes:
                    if not isinstance(node, dict):
                        continue
                    z = node.get('z')
                    if not isinstance(z, str) or not z:
                        continue
                    if not node.get('id'):
                        continue
                    nodes_by_z.setdefault(z, []).append(node)

                def _collect_strings(value, out):
                    if isinstance(value, dict):
                        for v in value.values():
                            _collect_strings(v, out)
                    elif isinstance(value, list):
                        for v in value:
                            _collect_strings(v, out)
                    elif isinstance(value, str):
                        out.add(value)

                def _resolve_configs_for_nodes(nodes):
                    strings = set()
                    for n in nodes:
                        _collect_strings(n, strings)
                    queue = [s for s in strings if s in config_by_id]
                    resolved = []
                    seen = set()
                    while queue:
                        rid = queue.pop(0)
                        if rid in seen:
                            continue
                        cfg = config_by_id.get(rid)
                        if not cfg:
                            continue
                        seen.add(rid)
                        resolved.append(cfg)
                        nested = set()
                        _collect_strings(cfg, nested)
                        for s in nested:
                            if s in config_by_id and s not in seen:
                                queue.append(s)
                    return resolved

                global_detail = instance.api_sync_flow_global()
                global_vals = {
                    'name': 'Global',
                    'nr_id': 'global',
                    'type': 'global',
                    'content': json.dumps(global_detail, ensure_ascii=False),
                    'instance_id': instance.id,
                }
                global_flow = Flow.search([
                    ('instance_id', '=', instance.id),
                    ('type', '=', 'global'),
                    ('nr_id', '=', 'global'),
                ], limit=1)
                if global_flow:
                    global_flow.write(global_vals)
                    global_record = global_flow
                else:
                    global_record = Flow.create(global_vals)
                instance._sync_nr_nodes_for_flow(
                    global_record,
                    global_detail,
                    include_nodes=False,
                    include_configs=True,
                    include_subflows=True,
                    global_nodes_by_nr_id=None,
                )
                global_nodes = Node.search([("flow_id", "=", global_record.id)])
                global_nodes_by_nr_id = {rec.nr_id: rec.id for rec in global_nodes if rec.nr_id}

                existing_flows = Flow.search([
                    ('instance_id', '=', instance.id),
                    ('type', '=', 'tab'),
                    ('nr_id', 'in', tab_ids),
                ])
                existing_by_nr_id = {rec.nr_id: rec for rec in existing_flows}

                for tab in tabs:
                    flow_id = tab['id']
                    label = tab.get('label') or tab.get('name') or flow_id
                    flow_detail = instance.api_sync_flow_by_id(flow_id)
                    vals = {
                        'name': label,
                        'nr_id': flow_id,
                        'type': 'tab',
                        'content': json.dumps(flow_detail, ensure_ascii=False),
                        'instance_id': instance.id,
                    }
                    existing = existing_by_nr_id.get(flow_id)
                    if existing:
                        existing.write(vals)
                        flow_record = existing
                    else:
                        flow_record = Flow.create(vals)
                    instance._sync_nr_nodes_for_flow(
                        flow_record,
                        flow_detail,
                        include_nodes=True,
                        include_configs=True,
                        include_subflows=False,
                        global_nodes_by_nr_id=global_nodes_by_nr_id,
                    )

                existing_subflows = Flow.search([
                    ('instance_id', '=', instance.id),
                    ('type', '=', 'subflow'),
                    ('nr_id', 'in', subflow_ids),
                ])
                existing_subflow_by_nr_id = {rec.nr_id: rec for rec in existing_subflows}

                for subflow in subflows:
                    subflow_id = subflow['id']
                    label = subflow.get('name') or subflow.get('label') or subflow_id
                    subflow_nodes = nodes_by_z.get(subflow_id, [])
                    flow_detail = {
                        'id': subflow_id,
                        'label': label,
                        'nodes': subflow_nodes,
                        'configs': _resolve_configs_for_nodes(subflow_nodes),
                        'subflow': subflow,
                    }
                    vals = {
                        'name': label,
                        'nr_id': subflow_id,
                        'type': 'subflow',
                        'content': json.dumps(flow_detail, ensure_ascii=False),
                        'instance_id': instance.id,
                    }
                    existing = existing_subflow_by_nr_id.get(subflow_id)
                    if existing:
                        existing.write(vals)
                        flow_record = existing
                    else:
                        flow_record = Flow.create(vals)
                    instance._sync_nr_nodes_for_flow(
                        flow_record,
                        flow_detail,
                        include_nodes=True,
                        include_configs=True,
                        include_subflows=False,
                        global_nodes_by_nr_id=global_nodes_by_nr_id,
                    )

                stale_flows = Flow.search([
                    ('instance_id', '=', instance.id),
                    ('type', '=', 'tab'),
                    ('nr_id', 'not in', tab_ids),
                ])
                if stale_flows:
                    stale_flows.unlink()

                stale_subflows = Flow.search([
                    ('instance_id', '=', instance.id),
                    ('type', '=', 'subflow'),
                    ('nr_id', 'not in', subflow_ids),
                ])
                if stale_subflows:
                    stale_subflows.unlink()

                ok_count += 1
            except Exception as e:
                fail_count += 1
                error_messages.append(f'{instance.name}: {str(e)}')

        message = _('Success: %(ok)s, Failed: %(fail)s', ok=ok_count, fail=fail_count)
        if error_messages:
            message = f'{message}\n' + '\n'.join(error_messages[:5])

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Synchronization Complete'),
                'message': message,
                'type': 'success' if fail_count == 0 else 'warning',
                'sticky': False,
            }
        }

    def api_sync_flow_by_id(self, flow_id):
        """
            Get detailed flow content by flow id.
        """
        self.ensure_one()
        if not flow_id:
            return {}
        return self._nr_get_json(f'/flow/{flow_id}')

    def api_sync_flow_global(self):
        """
            Get the global flow configuration.
        """
        self.ensure_one()
        return self._nr_get_json('/flow/global')

    def _nr_candidate_base_urls(self):
        self.ensure_one()
        host = (self.ip_address or "").strip()
        port = int(self.port or 1880)
        if not host:
            return []
        if host.startswith("http://"):
            host = host[7:]
        elif host.startswith("https://"):
            host = host[8:]
        if "/" in host:
            host = host.split("/", 1)[0]
        if ":" in host:
            maybe_host, maybe_port = host.rsplit(":", 1)
            if maybe_port.isdigit():
                host = maybe_host
                port = int(maybe_port)
        return [f"http://{host}:{port}"]

    def _nr_get_json(self, path, timeout=15):
        self.ensure_one()
        headers = {
            'Node-RED-API-Version': 'v2',
        }
        last_error = None
        for base_url in self._nr_candidate_base_urls():
            url = f"{base_url}{path}"
            try:
                response = requests.get(url, headers=headers, timeout=timeout)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                last_error = e
        raise UserError(_("Failed to call Node-RED API: %(error)s", error=str(last_error)))

    def _nr_post_json(self, path, body, timeout=15):
        self.ensure_one()
        headers = {
            "Node-RED-API-Version": "v2",
        }
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
        raise UserError(_("Failed to call Node-RED API: %(error)s", error=str(last_error)))

    def _nr_generate_id(self):
        return f"{uuid.uuid4().hex[:7]}.{uuid.uuid4().hex[:7]}"

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

    def _nr_remap_payload_ids(self, payload):
        self.ensure_one()
        if not isinstance(payload, dict):
            return payload

        payload_id = payload.get("id")
        nodes = payload.get("nodes") or []
        configs = payload.get("configs") or []
        if not isinstance(nodes, list) or not isinstance(configs, list):
            return payload

        def _dedup_by_id(items):
            seen = set()
            result = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("id")
                if not item_id:
                    result.append(item)
                    continue
                if item_id in seen:
                    continue
                seen.add(item_id)
                result.append(item)
            return result

        nodes = _dedup_by_id(nodes)
        configs = _dedup_by_id(configs)

        ids = [
            i.get("id")
            for i in (nodes + configs)
            if isinstance(i, dict) and i.get("id")
        ]
        if not ids:
            payload["nodes"] = nodes
            payload["configs"] = configs
            return payload

        mapping = {}
        used_new = set()
        for old_id in ids:
            if old_id in mapping:
                continue
            new_id = self._nr_generate_id()
            while new_id in used_new or (payload_id and new_id == payload_id):
                new_id = self._nr_generate_id()
            mapping[old_id] = new_id
            used_new.add(new_id)

        payload = dict(payload)
        payload["nodes"] = self._nr_replace_ids(nodes, mapping)
        payload["configs"] = self._nr_replace_ids(configs, mapping)
        if "credentials" in payload:
            payload["credentials"] = self._nr_replace_ids(payload.get("credentials"), mapping)
        return payload

    def _nr_render_item_value(self, value):
        if value is None:
            return value
        if not isinstance(value, str):
            return value

        def _resolve_path(record, path):
            current = record
            for part in path.split("."):
                if not part:
                    return ""
                current = getattr(current, part, None)
                if current is None:
                    return ""
            if isinstance(current, models.BaseModel):
                current.ensure_one()
                return current.id
            return current

        pattern = re.compile(r"\{\{\s*record\.([a-zA-Z_][\w\.]*)\s*\}\}")

        def _replace(match):
            resolved = _resolve_path(self, match.group(1))
            if resolved is None:
                return ""
            return str(resolved)

        return pattern.sub(_replace, value)

    def _nr_set_dict_path(self, target, path, value):
        if not isinstance(target, dict):
            return
        if not path:
            return
        parts = str(path).split(".")
        current = target
        for part in parts[:-1]:
            if not part:
                return
            nxt = current.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                current[part] = nxt
            current = nxt
        last = parts[-1]
        if last:
            current[last] = value

    def _nr_get_dict_path(self, target, path):
        if not isinstance(target, dict):
            return None
        if not path:
            return None
        parts = str(path).split(".")
        current = target
        for part in parts:
            if not part or not isinstance(current, dict) or part not in current:
                return None
            current = current.get(part)
        return current

    def _nr_build_flow_payload(self, flow):
        """
            Build the Node-RED flow payload.
        """
        self.ensure_one()
        raw = flow.content or "{}"
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            parsed = {}

        nodes = []
        configs = []
        if isinstance(parsed, dict):
            if isinstance(parsed.get("nodes"), list):
                nodes = parsed.get("nodes") or []
            if isinstance(parsed.get("configs"), list):
                configs = parsed.get("configs") or []

        nodes = [n for n in nodes if isinstance(n, dict)]
        configs = [c for c in configs if isinstance(c, dict)]
        for c in configs:
            if isinstance(c, dict) and "z" in c:
                c.pop("z", None)

        tab_id = flow.nr_id if (flow.instance_id and flow.instance_id.id == self.id and flow.nr_id) else self._nr_generate_id()
        payload = {
            "id": tab_id,
            "label": flow.name or "",
            "nodes": nodes,
            "configs": configs,
        }

        Node = self.env["fts.nr.node"]
        def _parse_node_content(rec):
            raw_content = rec.content or "{}"
            try:
                val = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
            except Exception:
                return None
            return val if isinstance(val, dict) else None

        base_nr_ids = [
            n.get("id")
            for n in payload["nodes"] + payload["configs"]
            if isinstance(n, dict) and n.get("id")
        ]

        existing_config_nr_ids = {
            c.get("id") for c in (payload.get("configs") or []) if isinstance(c, dict) and c.get("id")
        }

        if base_nr_ids:
            base_records = Node.search([("instance_id", "=", self.id), ("nr_id", "in", base_nr_ids)])
            queue = list(base_records)
            seen_cfg_rec_ids = set()
            while queue:
                rec = queue.pop(0)
                for cfg in rec.config_node_ids:
                    if cfg.id in seen_cfg_rec_ids:
                        continue
                    seen_cfg_rec_ids.add(cfg.id)
                    cfg_dict = _parse_node_content(cfg)
                    if cfg_dict and cfg_dict.get("id") and cfg_dict["id"] not in existing_config_nr_ids:
                        payload["configs"].append(cfg_dict)
                        existing_config_nr_ids.add(cfg_dict["id"])
                    queue.append(cfg)

        all_nr_ids = [
            n.get("id")
            for n in payload["nodes"] + payload["configs"]
            if isinstance(n, dict) and n.get("id")
        ]
        if all_nr_ids:
            node_records = Node.search([("instance_id", "=", self.id), ("nr_id", "in", all_nr_ids)])
            node_by_nr_id = {rec.nr_id: rec for rec in node_records if rec.nr_id}
            credentials_by_nr_id = {}
            for node_dict in payload["nodes"] + payload["configs"]:
                if not isinstance(node_dict, dict):
                    continue
                nr_id = node_dict.get("id")
                rec = node_by_nr_id.get(nr_id)
                if not rec or not rec.item_ids:
                    continue
                if node_dict.get("type") == "mqtt-broker":
                    user_value = None
                    password_value = None
                    for item in rec.item_ids:
                        if item.key in ("user", "credentials.user"):
                            user_value = self._nr_render_item_value(item.value)
                        elif item.key in ("password", "credentials.password"):
                            password_value = self._nr_render_item_value(item.value)
                    if user_value is not None or password_value is not None:
                        node_dict["credentials"] = {
                            "user": user_value or "",
                            "password": password_value or "",
                        }
                        credentials_by_nr_id[nr_id] = {
                            "user": user_value or "",
                            "password": password_value or "",
                        }
                for item in rec.item_ids:
                    if not item.key:
                        continue
                    if node_dict.get("type") == "mqtt-broker" and item.key in (
                        "user",
                        "password",
                        "credentials.user",
                        "credentials.password",
                    ):
                        continue
                    rendered = self._nr_render_item_value(item.value)
                    if item.value_type == "json":
                        try:
                            parsed_json = json.loads(rendered) if isinstance(rendered, str) else rendered
                        except Exception as e:
                            raise UserError(_("The item value is not valid JSON: %(key)s (%(error)s)", key=item.key, error=str(e)))
                        existing_value = self._nr_get_dict_path(node_dict, item.key)
                        if isinstance(existing_value, (dict, list)):
                            rendered = parsed_json
                        else:
                            rendered = json.dumps(parsed_json, ensure_ascii=False)
                    self._nr_set_dict_path(node_dict, item.key, rendered)

        mapping = {}
        used_new = set()
        for item in payload["nodes"] + payload["configs"]:
            old_id = item.get("id")
            if old_id and old_id not in mapping:
                new_id = self._nr_generate_id()
                while new_id in used_new or new_id == payload["id"]:
                    new_id = self._nr_generate_id()
                mapping[old_id] = new_id
                used_new.add(new_id)

        if all_nr_ids:
            credentials = {}
            for old_id, cred in credentials_by_nr_id.items():
                credentials[mapping.get(old_id, old_id)] = cred
            if credentials:
                payload["credentials"] = credentials

        payload = self._nr_replace_ids(payload, mapping)

        remapped_items = (payload.get("nodes") or []) + (payload.get("configs") or [])
        remapped_ids = [i.get("id") for i in remapped_items if isinstance(i, dict) and i.get("id")]
        if len(remapped_ids) != len(set(remapped_ids)) or payload["id"] in set(remapped_ids):
            raise UserError(_("Duplicate node IDs exist in the flow, so it cannot be applied."))

        return payload
