import json
import re
import requests
from urllib.parse import quote

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
        host = host.strip().lower()
        if not host:
            return []

        use_edge_proxy = bool(inst and inst.instance_type == "remote" and inst.edge_node_id and inst.edge_node_id.use_frp)
        if use_edge_proxy:
            config = self.env["ir.config_parameter"].sudo()
            proxy_base = (config.get_param("feitas_iot.nodered_proxy_base_url") or "http://nginx").strip().rstrip("/")
            encoded_host = quote(host, safe="")
            return [f"{proxy_base}/edge-proxy/{encoded_host}"]

        return [f"http://{host}:{port}"]

    def _nr_post_json(self, path, body, timeout=15):

        inst = self.instance_id
        last_error = None
        for base_url in self._nr_candidate_base_urls():
            url = f"{base_url}{path}"
            try:
                headers = inst._nr_headers_for(base_url) if inst else {"Node-RED-API-Version": "v2"}
                response = requests.post(url, headers=headers, json=body, timeout=timeout)
                response.raise_for_status()
                try:
                    return response.json()
                except Exception:
                    return {}
            except Exception as e:
                last_error = e
        raise UserError(f"Node-RED request failed: {last_error}")

    def _nr_get_json(self, path, timeout=15):
        inst = self.instance_id
        last_error = None
        for base_url in self._nr_candidate_base_urls():
            url = f"{base_url}{path}"
            try:
                headers = inst._nr_headers_for(base_url) if inst else {"Node-RED-API-Version": "v2"}
                response = requests.get(url, headers=headers, timeout=timeout)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                last_error = e
        raise UserError(f"Node-RED request failed: {last_error}")

    def _nr_delete_json(self, path, timeout=15):

        inst = self.instance_id
        last_error = None
        for base_url in self._nr_candidate_base_urls():
            url = f"{base_url}{path}"
            try:
                headers = inst._nr_headers_for(base_url) if inst else {"Node-RED-API-Version": "v2"}
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
        raw = flow.content or "{}"
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            parsed = {}

        nodes = parsed.get("nodes", []) if isinstance(parsed, dict) else []
        configs = parsed.get("configs", []) if isinstance(parsed, dict) else []
        nodes = [n for n in nodes if isinstance(n, dict)]
        configs = [c for c in configs if isinstance(c, dict) and c.get("id")]
        if configs:
            refs = set()
            self._collect_strings(nodes, refs)
            by_id = {c.get("id"): c for c in configs if isinstance(c.get("id"), str)}
            queue = [rid for rid in refs if rid in by_id]
            resolved = []
            seen = set()
            while queue:
                rid = queue.pop(0)
                if rid in seen:
                    continue
                cfg = by_id.get(rid)
                if not cfg:
                    continue
                seen.add(rid)
                resolved.append(cfg)
                nested = set()
                self._collect_strings(cfg, nested)
                for nid in nested:
                    if nid in by_id and nid not in seen:
                        queue.append(nid)
            configs = resolved
        for c in configs:
            if isinstance(c, dict) and "z" in c:
                c.pop("z", None)

        if not configs:
            refs = set()
            self._collect_strings(nodes, refs)
            global_configs = self._resolve_global_configs(self.instance_id, refs)
            cfg_ids = {c.get("id") for c in configs if c.get("id")}
            for cfg in global_configs:
                cfg_id = cfg.get("id")
                if cfg_id and cfg_id not in cfg_ids:
                    cfg_copy = dict(cfg)
                    cfg_copy.pop("z", None)
                    configs.append(cfg_copy)
                    cfg_ids.add(cfg_id)

        payload = {
            "id": self.instance_id._nr_generate_id(),
            "label": flow.name or "",
            "nodes": nodes,
            "configs": configs,
        }
        tab_id = payload.get("id")
        if tab_id:
            for node in payload.get("nodes") or []:
                if isinstance(node, dict) and isinstance(node.get("z"), str):
                    node["z"] = tab_id
        return payload

    def _get_component_account_credentials(self, component_type, username):
        component = self.env["crose.component"].search(
            [("component_type", "=", component_type), ("status", "=", "online")],
            limit=1,
        )
        if not component:
            component = self.env["crose.component"].search([("component_type", "=", component_type)], limit=1)
        if not component:
            raise UserError(f"Component '{component_type}' not found.")
        account = component.account_ids.filtered(lambda x: (x.username or "").strip() == username)[:1]
        if not account:
            raise UserError(f"Account '{username}' not found on component '{component.name}'.")
        password = account._get_plain_password()
        if not password:
            raise UserError(f"Account '{username}' has no decryptable password.")
        return account.username, password

    def _inject_iotdb_mqtt_broker_credentials(self, payload):
        if not isinstance(payload, dict):
            return payload
        username, password = self._get_component_account_credentials("iotdb", "mqtt_client")
        credentials_map = payload.get("credentials")
        if not isinstance(credentials_map, dict):
            credentials_map = {}
        for section in ("configs", "nodes"):
            for item in payload.get(section) or []:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "mqtt-broker":
                    continue
                if str(item.get("name") or "").strip().lower() != "iotdb":
                    continue
                item_credentials = item.get("credentials") if isinstance(item.get("credentials"), dict) else {}
                item_credentials["user"] = username
                item_credentials["password"] = password
                item["credentials"] = item_credentials
                node_id = item.get("id")
                if node_id:
                    credentials_map[node_id] = {
                        "user": username,
                        "password": password,
                    }
        if credentials_map:
            payload["credentials"] = credentials_map
        return payload

    def _collect_ids(self, value, out):
        if isinstance(value, dict):
            node_id = value.get("id")
            if isinstance(node_id, str) and node_id:
                out.add(node_id)
            for v in value.values():
                self._collect_ids(v, out)
            return
        if isinstance(value, list):
            for v in value:
                self._collect_ids(v, out)

    def _resolve_record_path(self, record, path):
        current = record
        for part in str(path).split("."):
            if not part:
                return ""
            if isinstance(current, models.BaseModel):
                if not current:
                    return ""
                current = current[part] if part in current._fields else None
            elif isinstance(current, dict):
                current = current.get(part)
            else:
                current = getattr(current, part, None) if hasattr(current, part) else None
            if current is None:
                return ""
        if isinstance(current, models.BaseModel):
            if not current:
                return ""
            if len(current) > 1:
                return ", ".join(current.mapped("display_name"))
            current = current[:1]
            if "name" in current._fields:
                return current.name or ""
            return current.id
        return current

    def _get_remote_gateway_mqtt_user(self, raise_if_missing=False):
        self.ensure_one()
        instance = self.instance_id
        if not instance or instance.instance_type != "remote":
            return False

        edge_node = instance.edge_node_id
        gateway = edge_node if edge_node and edge_node.is_gateway else (edge_node.gateway_id if edge_node else False)
        if not gateway:
            if raise_if_missing:
                raise UserError("Remote instance has no gateway configured. Please configure the edge node gateway first.")
            return False

        user_model = self.env["fts.gateway.mqtt.user"].sudo()
        mqtt_user = user_model.search(
            [("gateway_id", "=", gateway.id), ("instance_id", "=", instance.id)],
            limit=1,
        )
        if not mqtt_user and edge_node:
            mqtt_user = user_model.search(
                [("gateway_id", "=", gateway.id), ("edge_node_id", "=", edge_node.id)],
                limit=1,
            )
        if mqtt_user:
            return mqtt_user
        if raise_if_missing:
            raise UserError(
                "No gateway MQTT user is assigned to this remote instance. "
                "Please initialize the edge node or create a gateway MQTT user first."
            )
        return False

    def _render_flow_param_value(self, param):
        raw_value = param.value or ""
        value_type = (param.type or "str").lower()
        if not isinstance(raw_value, str):
            return raw_value

        text = raw_value
        record_pattern = re.compile(r"%\s*record\.([a-zA-Z_][\w\.]*)\s*%")

        def _replace_record(match):
            resolved = self._resolve_record_path(self.instance_id, match.group(1))
            if isinstance(resolved, (dict, list)):
                return json.dumps(resolved, ensure_ascii=False)
            return "" if resolved is None else str(resolved)

        rendered = record_pattern.sub(_replace_record, text)

        if value_type == "num":
            try:
                value_text = str(rendered).strip()
                return int(value_text) if re.fullmatch(r"-?\d+", value_text) else float(value_text)
            except Exception:
                return rendered
        if value_type == "bool":
            value_text = str(rendered).strip().lower()
            if value_text in ("1", "true", "yes", "on"):
                return True
            if value_text in ("0", "false", "no", "off", ""):
                return False
            return rendered
        if value_type == "json":
            try:
                return json.loads(rendered) if isinstance(rendered, str) else rendered
            except Exception:
                return rendered
        return rendered

    def _set_value_with_reference(self, target, path_parts, value, node_by_id):
        current = target
        for index, part in enumerate(path_parts):
            if not isinstance(current, dict) or not part:
                return False
            is_last = index == len(path_parts) - 1
            if is_last:
                existing = current.get(part)
                if isinstance(existing, str) and existing in node_by_id and not isinstance(value, (dict, list)):
                    # Do not overwrite config-node reference IDs (e.g. mqtt out.broker).
                    return False
                current[part] = value
                return True
            nxt = current.get(part)
            if isinstance(nxt, str) and nxt in node_by_id:
                current = node_by_id[nxt]
                continue
            if not isinstance(nxt, dict):
                nxt = {}
                current[part] = nxt
            current = nxt
        return False

    def _sync_mqtt_credentials_in_payload(self, payload):
        credentials_map = payload.get("credentials")
        if not isinstance(credentials_map, dict):
            credentials_map = {}
        for item in (payload.get("nodes") or []) + (payload.get("configs") or []):
            if not isinstance(item, dict) or item.get("type") != "mqtt-broker":
                continue
            node_id = item.get("id")
            if not node_id:
                continue
            node_credentials = item.get("credentials") if isinstance(item.get("credentials"), dict) else {}
            user_value = node_credentials.get("user", item.get("user"))
            password_value = node_credentials.get("password", item.get("password"))
            if user_value is None and password_value is None:
                continue
            item["credentials"] = {
                "user": "" if user_value is None else user_value,
                "password": "" if password_value is None else password_value,
            }
            credentials_map[node_id] = dict(item["credentials"])
        if credentials_map:
            payload["credentials"] = credentials_map
        return payload

    def _apply_remote_instance_mqtt_credentials(self, payload):
        instance = self.instance_id
        if not isinstance(payload, dict) or not instance or instance.instance_type != "remote":
            return payload

        mqtt_user = self._get_remote_gateway_mqtt_user(raise_if_missing=True)
        username = (mqtt_user.username or "").strip()
        password = mqtt_user._get_plain_password() or ""
        if not username:
            raise UserError("Gateway MQTT user is empty. Please check gateway MQTT user configuration.")

        credentials_map = payload.get("credentials")
        if not isinstance(credentials_map, dict):
            credentials_map = {}
        for item in (payload.get("nodes") or []) + (payload.get("configs") or []):
            if not isinstance(item, dict) or item.get("type") != "mqtt-broker":
                continue
            if str(item.get("name") or "").strip().lower() == "iotdb":
                continue
            item["credentials"] = {
                "user": username,
                "password": password,
            }
            item["user"] = username
            item["password"] = password
            node_id = item.get("id")
            if node_id:
                credentials_map[node_id] = {
                    "user": username,
                    "password": password,
                }
        if credentials_map:
            payload["credentials"] = credentials_map
        return payload

    def _ensure_payload_configs_from_source_global(self, payload, source_instance):
        if not isinstance(payload, dict) or not source_instance:
            return payload
        nodes = [n for n in (payload.get("nodes") or []) if isinstance(n, dict)]
        configs = [c for c in (payload.get("configs") or []) if isinstance(c, dict) and c.get("id")]
        if not nodes:
            return payload

        refs = set()
        self._collect_strings(nodes, refs)
        if not refs:
            return payload

        existing_ids = {c.get("id") for c in configs if isinstance(c.get("id"), str)}
        global_configs = self._resolve_global_configs(source_instance, refs)
        for cfg in global_configs:
            cfg_id = cfg.get("id") if isinstance(cfg, dict) else None
            if not cfg_id or cfg_id in existing_ids:
                continue
            cfg_copy = dict(cfg)
            cfg_copy.pop("z", None)
            configs.append(cfg_copy)
            existing_ids.add(cfg_id)

        payload["configs"] = configs
        return payload

    def _apply_flow_params_to_payload(self, flow, payload):
        if not flow.param_ids or not isinstance(payload, dict):
            return payload
        nodes = [n for n in payload.get("nodes") or [] if isinstance(n, dict)]
        configs = [c for c in payload.get("configs") or [] if isinstance(c, dict)]
        all_items = nodes + configs
        if not all_items:
            return payload
        node_by_id = {n.get("id"): n for n in all_items if isinstance(n.get("id"), str)}

        for param in flow.param_ids:
            name = (param.name or "").strip()
            if not name:
                continue
            path_parts = [p.strip() for p in name.split("/") if p and p.strip()]
            if len(path_parts) < 2:
                continue
            node_type = path_parts[0]
            target_path = path_parts[1:]
            target_nodes = [n for n in all_items if n.get("type") == node_type]
            if not target_nodes:
                continue
            value = self._render_flow_param_value(param)
            for node in target_nodes:
                self._set_value_with_reference(node, target_path, value, node_by_id)

        return self._sync_mqtt_credentials_in_payload(payload)

    def _expand_subflow_deps_configs(self, deps, source_instance):
        deps = [d for d in deps if isinstance(d, dict)]
        if not deps or not source_instance:
            return deps

        Flow = self.env["fts.nr.flow"]
        global_flow = Flow.search(
            [
                ("instance_id", "=", source_instance.id),
                ("type", "=", "global"),
                ("nr_id", "=", "global"),
            ],
            limit=1,
        )
        if not global_flow or not global_flow.content:
            return deps
        try:
            global_parsed = json.loads(global_flow.content)
        except Exception:
            return deps
        if not isinstance(global_parsed, dict):
            return deps

        candidates = []
        for key in ("configs", "nodes", "subflows"):
            part = global_parsed.get(key)
            if isinstance(part, list):
                candidates.extend([i for i in part if isinstance(i, dict) and i.get("id")])
        global_by_id = {i["id"]: i for i in candidates if isinstance(i.get("id"), str)}
        if not global_by_id:
            return deps

        def _is_config_node(item):
            return (
                isinstance(item, dict)
                and item.get("id")
                and item.get("type") not in ("tab", "subflow")
                and "wires" not in item
            )

        for dep in deps:
            configs = dep.get("configs")
            if not isinstance(configs, list):
                configs = []
            configs = [c for c in configs if isinstance(c, dict) and c.get("id")]
            config_ids = {c.get("id") for c in configs if isinstance(c.get("id"), str)}

            refs = set()
            self._collect_strings(dep, refs)
            queue = [rid for rid in refs if rid in global_by_id]
            seen = set()
            while queue:
                rid = queue.pop(0)
                if rid in seen or rid in config_ids:
                    continue
                item = global_by_id.get(rid)
                if not item:
                    continue
                seen.add(rid)
                if _is_config_node(item):
                    configs.append(item)
                    config_ids.add(rid)
                    nested = set()
                    self._collect_strings(item, nested)
                    for nid in nested:
                        if nid in global_by_id and nid not in seen and nid not in config_ids:
                            queue.append(nid)

            dep["configs"] = configs

        return deps

    def _deploy_subflow_deps(self, deps, base_configs=None):
        self.ensure_one()
        deps = [d for d in deps if isinstance(d, dict)]
        if not deps:
            return {}
        base_configs = base_configs if isinstance(base_configs, list) else []
        base_configs = [c for c in base_configs if isinstance(c, dict) and c.get("id")]

        config_pool = {}
        for dep in deps:
            dep_configs = dep.get("configs")
            if not isinstance(dep_configs, list):
                continue
            for cfg in dep_configs:
                if isinstance(cfg, dict) and isinstance(cfg.get("id"), str):
                    config_pool[cfg["id"]] = cfg
        for cfg in base_configs:
            cfg_id = cfg.get("id")
            if isinstance(cfg_id, str) and cfg_id and cfg_id not in config_pool:
                config_pool[cfg_id] = cfg

        def _get_subflow_id(dep):
            subflow_def = dep.get("subflow") if isinstance(dep.get("subflow"), dict) else None
            if subflow_def and isinstance(subflow_def.get("id"), str) and subflow_def.get("id"):
                return subflow_def.get("id")
            if dep.get("type") == "subflow" and isinstance(dep.get("id"), str) and dep.get("id"):
                return dep.get("id")
            return None

        subflow_ids = []
        for dep in deps:
            sid = _get_subflow_id(dep)
            if sid:
                subflow_ids.append(sid)

        subflow_mapping = {}
        used_new = set()
        for sid in sorted(set(subflow_ids)):
            new_id = self.instance_id._nr_generate_id()
            while new_id in used_new:
                new_id = self.instance_id._nr_generate_id()
            subflow_mapping[sid] = new_id
            used_new.add(new_id)

        elements = []
        for dep in deps:
            dep_work = dict(dep)
            dep_configs = dep_work.get("configs")
            if not isinstance(dep_configs, list):
                dep_configs = []
            dep_configs = [c for c in dep_configs if isinstance(c, dict) and c.get("id")]
            dep_cfg_ids = {c.get("id") for c in dep_configs if isinstance(c.get("id"), str)}

            refs = set()
            self._collect_strings(dep_work, refs)
            queue = [rid for rid in refs if rid in config_pool]
            seen = set()
            while queue:
                rid = queue.pop(0)
                if rid in seen or rid in dep_cfg_ids:
                    continue
                cfg = config_pool.get(rid)
                if not cfg:
                    continue
                seen.add(rid)
                dep_configs.append(cfg)
                dep_cfg_ids.add(rid)
                nested = set()
                self._collect_strings(cfg, nested)
                for nid in nested:
                    if nid in config_pool and nid not in seen and nid not in dep_cfg_ids:
                        queue.append(nid)
            dep_work["configs"] = dep_configs

            ids = set()
            self._collect_ids(dep_work, ids)
            mapping = dict(subflow_mapping)
            used = set(mapping.values())
            for old in sorted(ids):
                if old in mapping:
                    continue
                new_id = self.instance_id._nr_generate_id()
                while new_id in used:
                    new_id = self.instance_id._nr_generate_id()
                mapping[old] = new_id
                used.add(new_id)

            remapped = self.instance_id._nr_replace_ids(dep_work, mapping)
            if not isinstance(remapped, dict):
                continue

            subflow_def = remapped.get("subflow") if isinstance(remapped.get("subflow"), dict) else None
            if not subflow_def and remapped.get("type") == "subflow":
                subflow_def = remapped

            nodes = remapped.get("nodes") if isinstance(remapped.get("nodes"), list) else []
            configs = remapped.get("configs") if isinstance(remapped.get("configs"), list) else []

            cred_payload = {"nodes": nodes, "configs": configs}
            cred_payload = self._inject_iotdb_mqtt_broker_credentials(cred_payload)
            nodes = cred_payload.get("nodes") if isinstance(cred_payload.get("nodes"), list) else []
            configs = cred_payload.get("configs") if isinstance(cred_payload.get("configs"), list) else []
            for c in configs:
                if isinstance(c, dict) and "z" in c:
                    c.pop("z", None)

            if isinstance(subflow_def, dict) and subflow_def.get("id"):
                elements.append(subflow_def)
            elements.extend([n for n in nodes if isinstance(n, dict) and n.get("id")])
            elements.extend([c for c in configs if isinstance(c, dict) and c.get("id")])

        if not elements:
            return subflow_mapping

        current = self._nr_get_json("/flows")
        if isinstance(current, dict):
            current_flows = current.get("flows") or []
        else:
            current_flows = current or []
        current_flows = [f for f in current_flows if isinstance(f, dict) and f.get("id")]

        by_id = {f["id"]: f for f in current_flows if isinstance(f.get("id"), str)}
        order = [f["id"] for f in current_flows if isinstance(f.get("id"), str)]
        for el in elements:
            el_id = el.get("id")
            if isinstance(el_id, str) and el_id:
                by_id[el_id] = el
                if el_id not in order:
                    order.append(el_id)

        merged = [by_id[i] for i in order if i in by_id]
        self._nr_post_json("/flows", {"flows": merged})
        return subflow_mapping

    def action_confirm(self):
        self.ensure_one()
        if self.operation == "add":
            if not self.template_flow_ids:
                raise UserError("Please select at least one template flow to add.")
            created_flow_nr_ids = []
            for tmpl in self.template_flow_ids:
                raw = tmpl.content or "{}"
                try:
                    parsed = json.loads(raw) if isinstance(raw, str) else raw
                except Exception:
                    parsed = {}
                if not isinstance(parsed, dict):
                    parsed = {}
                deps = parsed.get("subflow_deps") if isinstance(parsed.get("subflow_deps"), list) else []
                base_configs = parsed.get("configs") if isinstance(parsed.get("configs"), list) else []
                source_flow = self.env["fts.nr.flow"].search([("app_store_id", "=", tmpl.id)], limit=1)
                source_instance = source_flow.instance_id if source_flow and source_flow.instance_id else False

                payload = self._build_flow_payload(tmpl)
                payload["label"] = f"{tmpl.name} - {self.instance_id.name}"
                payload = self._ensure_payload_configs_from_source_global(payload, source_instance)

                subflow_mapping = {}
                if deps:
                    if source_instance:
                        deps = self._expand_subflow_deps_configs(deps, source_instance)
                    subflow_mapping = self._deploy_subflow_deps(deps, base_configs=base_configs)
                    if subflow_mapping:
                        for node in payload.get("nodes") or []:
                            if not isinstance(node, dict):
                                continue
                            node_type = node.get("type")
                            if isinstance(node_type, str) and node_type.startswith("subflow:"):
                                sid = node_type.split(":", 1)[1]
                                if sid in subflow_mapping:
                                    node["type"] = f"subflow:{subflow_mapping[sid]}"

                payload = self.instance_id._nr_remap_payload_ids(payload)
                payload = self._inject_iotdb_mqtt_broker_credentials(payload)
                payload = self._apply_flow_params_to_payload(tmpl, payload)
                payload = self._apply_remote_instance_mqtt_credentials(payload)
                result = self._nr_post_json("/flow", payload)
                new_nr_id = result.get("id") if isinstance(result, dict) else None
                if not new_nr_id:
                    new_nr_id = payload["id"]
                created_flow_nr_ids.append(new_nr_id)

            self.instance_id.api_sync_flows()
            created = self.env["fts.nr.flow"].search(
                [
                    ("instance_id", "=", self.instance_id.id),
                    ("type", "=", "tab"),
                    ("nr_id", "in", created_flow_nr_ids),
                ]
            ).mapped("display_name")

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
