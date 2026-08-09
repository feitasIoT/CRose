import re
import json
import time
import uuid
import logging
import requests
from urllib.parse import quote


from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


_logger = logging.getLogger(__name__)


class FtsNrInstance(models.Model):
    _name = "fts.nr.instance"
    _description = "IoT Instance"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="Name", required=True)
    ip_address = fields.Char(string="IP Address", required=True)
    port = fields.Integer(string="Port", required=True, default=1880)
    editor_port = fields.Integer(string="Editor Port")
    version = fields.Char(string="Version")
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
    gateway_id = fields.Many2one(
        "fts.edge.node",
        string="Gateway",
        related="edge_node_id.gateway_id",
        store=True,
        readonly=True,
    )
    component_id = fields.Many2one("crose.component", string="Component")
    mqtt_broker_id = fields.Many2one("crose.component", string="MQTT Broker", domain="[('component_type', '=', 'mqtt')]")
    mqtt_account_id = fields.Many2one(
        "crose.component.account",
        string="MQTT Account",
        domain="[('component_id', '=', mqtt_broker_id)]",
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
        tracking=True
    )
    flow_ids = fields.One2many("fts.nr.flow", "instance_id", string="Flows")
    flow_count = fields.Integer(compute="_compute_flow_count", string="Flow Count")
    flow_line_ids = fields.One2many("instance.flow.line", "instance_id", string="Flow Lines")
    npm_registry_id = fields.Many2one("crose.component", string="NPM Registry", domain=[('component_type', '=', 'npm')])

    _nr_token_cache = {}

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        account_model = self.env["crose.component.account"].sudo()
        for instance in records:
            if instance.mqtt_account_id:
                continue
            broker = instance.mqtt_broker_id
            if not broker or broker.component_type != "mqtt":
                continue
            username = f"mqtt_instance_{instance.id}"
            password = broker._generate_password(8)
            broker.api_create_users(username, password)
            account = account_model.search(
                [("component_id", "=", broker.id), ("username", "=", username)],
                limit=1,
            )
            if account:
                instance.mqtt_account_id = account.id
        records._sync_local_status_from_component()
        records._sync_edge_node_status()
        return records

    def write(self, vals):
        old_edge_node_ids = self.mapped("edge_node_id").ids
        result = super().write(vals)
        if not self.env.context.get("skip_local_status_sync"):
            self._sync_local_status_from_component()
        if "status" in vals or "edge_node_id" in vals:
            self._sync_edge_node_status(extra_node_ids=old_edge_node_ids)
        return result

    def unlink(self):
        edge_node_ids = self.mapped("edge_node_id").ids
        result = super().unlink()
        if edge_node_ids:
            self.env["fts.edge.node"].browse(edge_node_ids)._compute_status_from_instances()
        return result

    def _sync_edge_node_status(self, extra_node_ids=None):
        edge_nodes = self.mapped("edge_node_id")
        if extra_node_ids:
            edge_nodes |= self.env["fts.edge.node"].browse(extra_node_ids)
        if edge_nodes:
            edge_nodes._compute_status_from_instances()

    @api.onchange("instance_type", "component_id")
    def _onchange_local_status_from_component(self):
        for instance in self:
            if instance.instance_type == "local":
                instance.status = instance._get_local_component_status()

    def _get_local_component_status(self):
        self.ensure_one()
        valid_statuses = {"online", "offline", "error"}
        component_status = (self.component_id.status or "").strip()
        return component_status if component_status in valid_statuses else "offline"

    def _sync_local_status_from_component(self):
        for instance in self.filtered(lambda r: r.instance_type == "local"):
            target_status = instance._get_local_component_status()
            if instance.status != target_status:
                instance.with_context(skip_local_status_sync=True).write({"status": target_status})

    def update_status(self):
        for instance in self:
            if instance.instance_type == "local":
                instance.status = instance._get_local_component_status()
                continue
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
        FIXME: Development is suspended as this feature may be replaced by Node-RED workflows in the future, rendering it obsolete at that time.
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
        # FIXME：check component(nginx and frps) status before open editor
        self.ensure_one()
        action = self.env.ref("feitas_iot.action_node_red_editor_client", raise_if_not_found=False)
        if action:
            res = action.read()[0]
            res["display_name"] = _("Node-RED Editor")
            res["name"] = _("Node-RED Editor")
            editor_port = self.editor_port or self.port
            use_edge_proxy = self.instance_type == "remote" and bool(self.edge_node_id.use_frp)
            edge_proxy_port = 0
            if use_edge_proxy:
                edge_proxy_port = self.env["crose.component"]._get_mapped_port_by_type("nginx")
            res['params'] = {
                'instance_id': self.id,
                'node_red_url': f"http://{self.ip_address}:{editor_port}",
                'use_edge_proxy': use_edge_proxy,
                'edge_proxy_port': edge_proxy_port,
                'rewrite_browser_host': self.instance_type == "local",
            }
            return res
        return {}

    def _compute_flow_count(self):
        for record in self:
            record.flow_count = len(record.flow_ids)

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
            "view_mode": "kanban,list,form",
            "target": "current",
            "domain": [("id", "in", flows.ids)],
            "context": {
                "kanban_view_ref": "feitas_iot.view_fts_nr_flow_kanban"
            },
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
        raw_host = (self.ip_address or "").strip()
        port = int(self.port or 1880)
        if not raw_host:
            return []
        host = raw_host
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
        host = host.strip().lower()
        if not host:
            return []

        if self.instance_type == "local":
            return [f"http://{host}:{port}"]

        if not self.edge_node_id or not self.edge_node_id.use_frp:
            return [f"http://{host}:{port}"]

        config = self.env["ir.config_parameter"].sudo()
        proxy_base = (config.get_param("feitas_iot.nodered_proxy_base_url") or "http://nginx").strip()
        proxy_base = proxy_base.rstrip("/")
        encoded_host = quote(host, safe="")
        return [f"{proxy_base}/edge-proxy/{encoded_host}"]

    def _nr_auth(self):
        self.ensure_one()
        node = self.edge_node_id
        if not node:
            return None
        username = (node.nodered_username or "").strip()
        password = (node.nodered_password or "").strip()
        if not username or not password:
            return None
        return (username, password)

    def _nr_invalidate_token(self, base_url):
        self.ensure_one()
        username = (self.edge_node_id.nodered_username or "").strip() if self.edge_node_id else ""
        cache_key = (self.id, str(base_url), username)
        self._nr_token_cache.pop(cache_key, None)

    def _nr_get_bearer_token(self, base_url, *, timeout=10):
        self.ensure_one()
        creds = self._nr_auth()
        if not creds:
            return ""
        username, password = creds
        cache_key = (self.id, str(base_url), username)
        cached = self._nr_token_cache.get(cache_key) or {}
        token = cached.get("token") or ""
        expires_at = float(cached.get("expires_at") or 0.0)
        now = time.time()
        if token and expires_at > now:
            return token

        token_url = f"{str(base_url).rstrip('/')}/auth/token"
        data = {
            "client_id": "node-red-admin",
            "grant_type": "password",
            "scope": "*",
            "username": username,
            "password": password,
        }
        response = requests.post(
            token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout,
        )
        if response.status_code == 404:
            return ""
        response.raise_for_status()
        payload = response.json() if response.content else {}
        token = (payload.get("access_token") or "").strip() if isinstance(payload, dict) else ""
        expires_in = int(payload.get("expires_in") or 0) if isinstance(payload, dict) else 0
        if not token:
            return ""
        skew = 15
        expires_at = now + max(expires_in - skew, 0)
        self._nr_token_cache[cache_key] = {"token": token, "expires_at": expires_at}
        return token

    def _nr_headers_for(self, base_url):
        self.ensure_one()
        headers = {
            "Node-RED-API-Version": "v2",
        }
        token = self._nr_get_bearer_token(base_url)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _nr_get_json(self, path, timeout=15):
        self.ensure_one()
        last_error = None
        for base_url in self._nr_candidate_base_urls():
            url = f"{base_url}{path}"
            try:
                headers = self._nr_headers_for(base_url)
                response = requests.get(url, headers=headers, timeout=timeout)
                if response.status_code == 401 and "Authorization" in headers:
                    self._nr_invalidate_token(base_url)
                    headers = self._nr_headers_for(base_url)
                    response = requests.get(url, headers=headers, timeout=timeout)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                last_error = e
        raise UserError(_("Failed to call Node-RED API: %(error)s", error=str(last_error)))

    def _nr_post_json(self, path, body, timeout=15):
        """
        FIXME: 为什么要遍历出base_url？
        """
        self.ensure_one()
        last_error = None
        _logger.info(body)
        for base_url in self._nr_candidate_base_urls():
            url = f"{base_url}{path}"
            try:
                headers = self._nr_headers_for(base_url)
                response = requests.post(url, headers=headers, json=body, timeout=timeout)
                if response.status_code == 401 and "Authorization" in headers:
                    self._nr_invalidate_token(base_url)
                    headers = self._nr_headers_for(base_url)
                    response = requests.post(url, headers=headers, json=body, timeout=timeout)
                response.raise_for_status()
                try:
                    return response.json()
                except Exception:
                    return {}
            except Exception as e:
                last_error = e
        raise UserError(_("Failed to call Node-RED API: %(error)s", error=str(last_error)))

    def _nr_delete_json(self, path, timeout=15):
        self.ensure_one()
        last_error = None
        for base_url in self._nr_candidate_base_urls():
            url = f"{base_url}{path}"
            try:
                headers = self._nr_headers_for(base_url)
                response = requests.delete(url, headers=headers, timeout=timeout)
                if response.status_code == 401 and "Authorization" in headers:
                    self._nr_invalidate_token(base_url)
                    headers = self._nr_headers_for(base_url)
                    response = requests.delete(url, headers=headers, timeout=timeout)
                response.raise_for_status()
                return True
            except Exception as e:
                last_error = e
        raise UserError(_("Failed to call Node-RED API: %(error)s", error=str(last_error)))

    def _nr_put_json(self, path, body, timeout=15):
        self.ensure_one()
        last_error = None
        for base_url in self._nr_candidate_base_urls():
            url = f"{base_url}{path}"
            try:
                headers = self._nr_headers_for(base_url)
                response = requests.put(url, headers=headers, json=body, timeout=timeout)
                if response.status_code == 401 and "Authorization" in headers:
                    self._nr_invalidate_token(base_url)
                    headers = self._nr_headers_for(base_url)
                    response = requests.put(url, headers=headers, json=body, timeout=timeout)
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

    def _nr_replace_ids(self, value, mapping, preserve_subflow_refs=False):
        if isinstance(value, dict):
            replaced = {}
            for k, v in value.items():
                new_k = mapping.get(k, k) if isinstance(k, str) else k
                replaced[new_k] = self._nr_replace_ids(v, mapping, preserve_subflow_refs=preserve_subflow_refs)
            return replaced
        if isinstance(value, list):
            return [self._nr_replace_ids(v, mapping, preserve_subflow_refs=preserve_subflow_refs) for v in value]
        if isinstance(value, str):
            if value in mapping:
                return mapping[value]
            if not preserve_subflow_refs and value.startswith("subflow:"):
                subflow_id = value.split(":", 1)[1]
                if subflow_id in mapping:
                    return f"subflow:{mapping[subflow_id]}"
        return value

    def _nr_remap_payload_ids(self, payload, preserve_subflow_refs=False):
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
        payload["nodes"] = self._nr_replace_ids(nodes, mapping, preserve_subflow_refs=preserve_subflow_refs)
        payload["configs"] = self._nr_replace_ids(configs, mapping, preserve_subflow_refs=preserve_subflow_refs)
        if "credentials" in payload:
            payload["credentials"] = self._nr_replace_ids(payload.get("credentials"), mapping, preserve_subflow_refs=preserve_subflow_refs)
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

    def _get_remote_gateway_mqtt_user(self):
        self.ensure_one()
        if self.instance_type != "remote":
            return False

        edge_node = self.edge_node_id
        gateway = edge_node if edge_node and edge_node.is_gateway else (edge_node.gateway_id if edge_node else False)
        if not gateway:
            return False

        user_model = self.env["fts.gateway.mqtt.user"].sudo()
        mqtt_user = user_model.search(
            [("gateway_id", "=", gateway.id), ("instance_id", "=", self.id)],
            limit=1,
        )
        if not mqtt_user and edge_node:
            mqtt_user = user_model.search(
                [("gateway_id", "=", gateway.id), ("edge_node_id", "=", edge_node.id)],
                limit=1,
            )
        return mqtt_user

    def _apply_remote_instance_mqtt_credentials(self, payload, credentials_by_nr_id=None):
        self.ensure_one()
        if not isinstance(payload, dict) or self.instance_type != "remote":
            return payload, credentials_by_nr_id or {}

        mqtt_user = self._get_remote_gateway_mqtt_user()
        username = (mqtt_user.username or "").strip() if mqtt_user else ""
        password = mqtt_user._get_plain_password() if mqtt_user else ""
        if not username or not password:
            return payload, credentials_by_nr_id or {}

        credentials_by_nr_id = credentials_by_nr_id or {}
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
                credentials_by_nr_id[node_id] = {
                    "user": username,
                    "password": password,
                }
        return payload, credentials_by_nr_id

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

            payload, credentials_by_nr_id = self._apply_remote_instance_mqtt_credentials(
                payload,
                credentials_by_nr_id=credentials_by_nr_id,
            )

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

    # ========================================================================
    # Unified Parameter Resolution (shared by wizard & data_model)
    # ========================================================================

    def _nr_resolve_record_path(self, record, path):
        """Resolve a dotted path on a record or dict.
        
        When called from instance wizard, record is the fts.nr.instance record.
        When called from data_model, record is the fts.data.model record.
        Supports model fields with dot notation.
        """
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

    def _nr_resolve_placeholders(self, record, text):
        """Resolve {{record.field}} style placeholders in text.
        
        Supported prefixes:
          {{record.field}}  - resolves field on the record (instance or data_model)
          {{instance.field}} - resolves field on the instance (when record is data_model,
                               uses nr_instance_id/prod_instance_id)
          {{gateway.field}} - resolves field on the gateway
          {{node.field}}    - resolves field on the edge node
          {{data_asset_ids.field}} - resolves field on each data_asset (for data_model)
          {{field}}         - bare field, resolves on record directly
        """
        if not isinstance(text, str):
            return text

        pattern = re.compile(r"\{\{\s*([a-zA-Z_][\w\.]*)\s*\}\}")

        def _replace(match):
            expr = match.group(1)
            lower = expr.lower()

            # data_asset_ids references (data_model context)
            if expr == "data_asset_ids" or lower == "data_asset_ids":
                if hasattr(record, "data_asset_ids") and record.data_asset_ids:
                    return ",".join(str(a.id) for a in record.data_asset_ids)
                if hasattr(record, "data_asset_id") and record.data_asset_id:
                    return str(record.data_asset_id.id)
                return ""
            if expr.startswith("data_asset_ids."):
                rel_path = expr.split(".", 1)[1]
                if hasattr(record, "data_asset_ids") and record.data_asset_ids:
                    values = [
                        str(self._nr_resolve_record_path(a, rel_path) or "")
                        for a in record.data_asset_ids
                    ]
                    return ",".join(v for v in values if v)
                return ""

            # Gateway prefix
            if lower.startswith("gateway."):
                path = expr.split(".", 1)[1]
                gateway = None
                if hasattr(record, "gateway_id") and record.gateway_id:
                    gateway = record.gateway_id
                elif hasattr(record, "edge_node_id") and record.edge_node_id:
                    edge = record.edge_node_id
                    gateway = edge if edge.is_gateway else edge.gateway_id
                if gateway:
                    return str(self._nr_resolve_record_path(gateway, path) or "")
                return ""

            # Node prefix
            if lower.startswith("node."):
                path = expr.split(".", 1)[1]
                if hasattr(record, "edge_node_id") and record.edge_node_id:
                    return str(self._nr_resolve_record_path(record.edge_node_id, path) or "")
                return ""

            # Instance prefix (when record is data_model)
            if lower.startswith("instance."):
                path = expr.split(".", 1)[1]
                # try nr_instance_id first
                if hasattr(record, "nr_instance_id") and record.nr_instance_id:
                    return str(self._nr_resolve_record_path(record.nr_instance_id, path) or "")
                return ""

            # record prefix or bare field
            if lower.startswith("record."):
                path = expr.split(".", 1)[1]
            else:
                path = expr

            resolved = self._nr_resolve_record_path(record, path)
            if isinstance(resolved, (dict, list)):
                return json.dumps(resolved, ensure_ascii=False)
            return "" if resolved is None else str(resolved)

        return pattern.sub(_replace, text)

    def _nr_resolve_param_value(self, record, param):
        """Resolve a single flow param value against a record.
        
        Returns the rendered value cast to the param's type.
        """
        raw_value = param.value or ""
        value_type = (param.type or "str").lower()

        if not isinstance(raw_value, str):
            return raw_value

        rendered = self._nr_resolve_placeholders(record, raw_value)

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

    def _nr_preview_flow_params(self, flow, record):
        """Extract params from a template flow and resolve them against record.
        
        Returns list of dicts:
          {param_id, name, value (template), resolved_value, type}
        """
        results = []
        for param in flow.param_ids:
            resolved = self._nr_resolve_param_value(record, param)
            results.append({
                "param_id": param.id,
                "name": param.name or "",
                "value": param.value or "",
                "resolved_value": resolved,
                "type": param.type or "str",
            })
        return results

    # ========================================================================
    # Credential injection (moved from wizard)
    # ========================================================================

    def _nr_get_component_account_credentials(self, component_type, username):
        """Get credentials from a component account by type and username."""
        component = self.env["crose.component"].search(
            [("component_type", "=", component_type), ("status", "=", "online")],
            limit=1,
        )
        if not component:
            component = self.env["crose.component"].search(
                [("component_type", "=", component_type)], limit=1
            )
        if not component:
            raise UserError(_("Component '%(type)s' not found.", type=component_type))
        account = component.account_ids.filtered(
            lambda x: (x.username or "").strip() == username
        )[:1]
        if not account:
            raise UserError(
                _("Account '%(user)s' not found on component '%(comp)s'.",
                  user=username, comp=component.name)
            )
        password = account._get_plain_password()
        if not password:
            raise UserError(
                _("Account '%(user)s' has no decryptable password.", user=username)
            )
        return account.username, password

    def _nr_inject_iotdb_mqtt_broker_credentials(self, payload):
        """Inject IoTDB mqtt-broker credentials into payload."""
        if not isinstance(payload, dict):
            return payload
        target_items = []
        for section in ("configs", "nodes"):
            for item in payload.get(section) or []:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "mqtt-broker":
                    continue
                if str(item.get("name") or "").strip().lower() != "iotdb":
                    continue
                target_items.append(item)
        if not target_items:
            return payload

        username, password = self._nr_get_component_account_credentials("iotdb", "mqtt_client")
        credentials_map = payload.get("credentials")
        if not isinstance(credentials_map, dict):
            credentials_map = {}
        for item in target_items:
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

    # ========================================================================
    # Payload construction (shared by wizard & data_model)
    # ========================================================================

    def _nr_collect_subflow_refs(self, nodes):
        """Collect subflow IDs referenced in nodes."""
        subflow_ids = []
        seen = set()
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            node_type = node.get("type")
            if not isinstance(node_type, str) or not node_type.startswith("subflow:"):
                continue
            subflow_id = node_type.split(":", 1)[1]
            if not subflow_id or subflow_id in seen:
                continue
            seen.add(subflow_id)
            subflow_ids.append(subflow_id)
        return subflow_ids

    def _nr_apply_subflow_mapping(self, nodes, mapping):
        """Apply subflow id mapping to node references."""
        if not mapping:
            return
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            node_type = node.get("type")
            if not isinstance(node_type, str) or not node_type.startswith("subflow:"):
                continue
            subflow_id = node_type.split(":", 1)[1]
            if subflow_id in mapping:
                node["type"] = f"subflow:{mapping[subflow_id]}"
            node_subflow = node.get("subflow")
            if isinstance(node_subflow, str) and node_subflow in mapping:
                node["subflow"] = mapping[node_subflow]

    def _nr_collect_strings(self, value, out):
        """Collect all string values from a nested dict/list structure."""
        if isinstance(value, dict):
            for v in value.values():
                self._nr_collect_strings(v, out)
        elif isinstance(value, list):
            for v in value:
                self._nr_collect_strings(v, out)
        elif isinstance(value, str):
            out.add(value)

    def _nr_resolve_global_configs(self, source_instance, refs):
        """Resolve config nodes from source instance's global flow."""
        if not source_instance or not refs:
            return []
        global_flow = self.env["fts.nr.flow"].search(
            [("instance_id", "=", source_instance.id), ("nr_id", "=", "global")],
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
            self._nr_collect_strings(node, nested_refs)
            for nested in nested_refs:
                if nested in by_id and nested not in selected:
                    queue.append(nested)
        return list(selected.values())

    def _nr_ensure_payload_configs_from_source_global(self, payload, source_instance):
        """Ensure payload configs include required configs from source instance's global."""
        if not isinstance(payload, dict) or not source_instance:
            return payload
        nodes = [n for n in (payload.get("nodes") or []) if isinstance(n, dict)]
        configs = [c for c in (payload.get("configs") or []) if isinstance(c, dict) and c.get("id")]
        if not nodes:
            return payload

        refs = set()
        self._nr_collect_strings(nodes, refs)
        if not refs:
            return payload

        existing_ids = {c.get("id") for c in configs if isinstance(c.get("id"), str)}
        global_configs = self._nr_resolve_global_configs(source_instance, refs)
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

    def _nr_set_value_with_reference(self, target, path_parts, value, node_by_id):
        """Set a value in nested dict, following config node references."""
        current = target
        for index, part in enumerate(path_parts):
            if not isinstance(current, dict) or not part:
                return False
            is_last = index == len(path_parts) - 1
            if is_last:
                existing = current.get(part)
                if isinstance(existing, str) and existing in node_by_id and not isinstance(value, (dict, list)):
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

    def _nr_apply_flow_params_to_payload(self, flow, record, payload):
        """Apply flow parameters to payload, resolving placeholders against record."""
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
            value = self._nr_resolve_param_value(record, param)
            for node in target_nodes:
                self._nr_set_value_with_reference(node, target_path, value, node_by_id)

        # Sync mqtt credentials into the credentials map
        credentials_map = payload.get("credentials")
        if not isinstance(credentials_map, dict):
            credentials_map = {}
        for item in all_items:
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

    def _nr_payload_has_subflow_instances(self, payload):
        """Check if payload contains subflow instance nodes."""
        nodes = payload.get("nodes") if isinstance(payload, dict) else []
        for node in nodes or []:
            if isinstance(node, dict) and isinstance(node.get("type"), str) and node.get("type").startswith("subflow:"):
                return True
        return False

    def _nr_sanitize_main_flow_payload(self, payload):
        """Remove subflow definitions from main flow payload."""
        if not isinstance(payload, dict):
            return payload
        payload = dict(payload)
        payload["nodes"] = [
            node for node in (payload.get("nodes") or [])
            if isinstance(node, dict) and node.get("id") and node.get("type") != "subflow"
        ]
        payload["configs"] = [
            cfg for cfg in (payload.get("configs") or [])
            if isinstance(cfg, dict) and cfg.get("id") and cfg.get("type") != "subflow"
        ]
        return payload

    def _nr_post_main_flow_payload(self, payload):
        """Post a main flow payload to Node-RED, handling subflow instances by merging."""
        self.ensure_one()
        if not isinstance(payload, dict) or not payload.get("id"):
            raise UserError(_("Invalid flow payload."))

        if not self._nr_payload_has_subflow_instances(payload):
            result = self._nr_post_json("/flow", payload)
            new_nr_id = result.get("id") if isinstance(result, dict) else None
            return new_nr_id or payload["id"]

        current = self._nr_get_json("/flows")
        if isinstance(current, dict):
            current_flows = current.get("flows") or []
        else:
            current_flows = current or []
        current_flows = [f for f in current_flows if isinstance(f, dict) and f.get("id")]

        tab = {
            "id": payload["id"],
            "type": "tab",
            "label": payload.get("label") or "",
            "disabled": bool(payload.get("disabled", False)),
            "info": payload.get("info") or "",
        }
        if "env" in payload and isinstance(payload.get("env"), list):
            tab["env"] = payload.get("env")

        elements = [tab]
        elements.extend([n for n in (payload.get("nodes") or []) if isinstance(n, dict) and n.get("id")])
        elements.extend([c for c in (payload.get("configs") or []) if isinstance(c, dict) and c.get("id")])

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
        return payload["id"]

    # ========================================================================
    # Subflow deployment
    # ========================================================================

    def _nr_runtime_subflow_ids(self):
        """Get set of subflow IDs currently in NR runtime."""
        current = self._nr_get_json("/flows")
        if isinstance(current, dict):
            flows = current.get("flows") or []
        else:
            flows = current or []
        return {
            f.get("id")
            for f in flows
            if f.get("type") == "subflow" and isinstance(f.get("id"), str) and f.get("id")
        }

    def _nr_ensure_subflow_template(self, source_instance, subflow_id):
        """Find and ensure a subflow is published as a template."""
        Flow = self.env["fts.nr.flow"]
        source_subflow = Flow.search(
            [
                ("instance_id", "=", source_instance.id),
                ("type", "=", "subflow"),
                ("nr_id", "=", subflow_id),
            ],
            limit=1,
        )
        if not source_subflow:
            raise UserError(_("Subflow '%(id)s' not found in source instance.", id=subflow_id))
        if not source_subflow.app_store_id:
            source_subflow.action_publish_app()
            source_subflow = Flow.search([("id", "=", source_subflow.id)], limit=1)
        template = source_subflow.app_store_id
        if not template:
            raise UserError(
                _("Subflow '%(name)s' publish failed.", name=source_subflow.display_name)
            )
        return source_subflow, template

    def _nr_resolve_deployed_subflow_flow(self, template, source_subflow, expected_nr_id=None, runtime_subflow_ids=None):
        """Find an already-deployed subflow flow record."""
        Flow = self.env["fts.nr.flow"]
        existing = Flow.search(
            [
                ("instance_id", "=", self.id),
                ("type", "=", "subflow"),
                ("app_store_id", "=", template.id),
            ],
            limit=1,
        )
        if existing and (runtime_subflow_ids is None or existing.nr_id in runtime_subflow_ids):
            return existing

        search_names = []
        for candidate in (source_subflow.name, template.name, source_subflow.display_name):
            if candidate and candidate not in search_names:
                search_names.append(candidate)

        domain = [
            ("instance_id", "=", self.id),
            ("type", "=", "subflow"),
        ]
        if expected_nr_id:
            found = Flow.search(domain + [("nr_id", "=", expected_nr_id)], limit=1)
            if found and (runtime_subflow_ids is None or found.nr_id in runtime_subflow_ids):
                return found
        for candidate_name in search_names:
            found = Flow.search(domain + [("name", "=", candidate_name)], limit=1)
            if found and (runtime_subflow_ids is None or found.nr_id in runtime_subflow_ids):
                return found
        return Flow.browse()

    def _nr_parse_flow_content_dict(self, flow):
        """Parse flow content JSON into a dict."""
        raw = flow.content or "{}"
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _nr_expand_subflow_deps_configs(self, deps, source_instance):
        """Expand subflow dependencies with config nodes from source global."""
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
            self._nr_collect_strings(dep, refs)
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
                    self._nr_collect_strings(item, nested)
                    for nid in nested:
                        if nid in global_by_id and nid not in seen and nid not in config_ids:
                            queue.append(nid)

            dep["configs"] = configs

        return deps

    def _nr_collect_ids(self, value, out):
        """Collect all node IDs from a nested dict/list."""
        if isinstance(value, dict):
            node_id = value.get("id")
            if isinstance(node_id, str) and node_id:
                out.add(node_id)
            for v in value.values():
                self._nr_collect_ids(v, out)
        elif isinstance(value, list):
            for v in value:
                self._nr_collect_ids(v, out)

    def _nr_deploy_subflow_deps(self, deps, base_configs=None):
        """Deploy subflow dependencies to NR and return id mapping."""
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
            new_id = self._nr_generate_id()
            while new_id in used_new:
                new_id = self._nr_generate_id()
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
            self._nr_collect_strings(dep_work, refs)
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
                self._nr_collect_strings(cfg, nested)
                for nid in nested:
                    if nid in config_pool and nid not in seen and nid not in dep_cfg_ids:
                        queue.append(nid)
            dep_work["configs"] = dep_configs

            ids = set()
            self._nr_collect_ids(dep_work, ids)
            mapping = dict(subflow_mapping)
            used = set(mapping.values())
            for old in sorted(ids):
                if old in mapping:
                    continue
                new_id = self._nr_generate_id()
                while new_id in used:
                    new_id = self._nr_generate_id()
                mapping[old] = new_id
                used.add(new_id)

            remapped = self._nr_replace_ids(dep_work, mapping)
            if not isinstance(remapped, dict):
                continue

            subflow_def = remapped.get("subflow") if isinstance(remapped.get("subflow"), dict) else None
            if not subflow_def and remapped.get("type") == "subflow":
                subflow_def = remapped

            nodes = remapped.get("nodes") if isinstance(remapped.get("nodes"), list) else []
            configs = remapped.get("configs") if isinstance(remapped.get("configs"), list) else []

            cred_payload = {"nodes": nodes, "configs": configs}
            cred_payload = self._nr_inject_iotdb_mqtt_broker_credentials(cred_payload)
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

    def _nr_deploy_subflow_template(self, template, source_subflow, source_instance):
        """Deploy a single subflow template to this instance. Returns deployed nr_id."""
        runtime_subflow_ids = self._nr_runtime_subflow_ids()
        existing = self._nr_resolve_deployed_subflow_flow(
            template, source_subflow, runtime_subflow_ids=runtime_subflow_ids
        )
        if existing and existing.nr_id:
            return existing.nr_id

        dep = self._nr_parse_flow_content_dict(template)
        if not dep:
            raise UserError(
                _("Subflow template '%(name)s' has empty content.", name=template.display_name)
            )

        nested_subflow_ids = self._nr_collect_subflow_refs(dep.get("nodes") or [])
        nested_mapping = self._nr_ensure_subflows_deployed(source_instance, nested_subflow_ids)
        self._nr_apply_subflow_mapping(dep.get("nodes") or [], nested_mapping)

        deps = [dep]
        deps = self._nr_expand_subflow_deps_configs(deps, source_instance)
        subflow_mapping = self._nr_deploy_subflow_deps(deps)
        new_subflow_id = subflow_mapping.get(
            dep.get("subflow", {}).get("id")
            if isinstance(dep.get("subflow"), dict)
            else dep.get("id")
        )
        if not new_subflow_id:
            raise UserError(
                _("Subflow template '%(name)s' deployment failed.", name=template.display_name)
            )

        self.api_sync_flows()
        created = self._nr_resolve_deployed_subflow_flow(
            template,
            source_subflow,
            expected_nr_id=new_subflow_id,
            runtime_subflow_ids=self._nr_runtime_subflow_ids(),
        )
        if created:
            created.write({"app_store_id": template.id})
            return created.nr_id
        raise UserError(
            _("Subflow template '%(name)s' synced flow is not found.", name=template.display_name)
        )

    def _nr_ensure_subflows_deployed(self, source_instance, subflow_ids):
        """Ensure all referenced subflows are deployed to this instance. Returns mapping."""
        mapping = {}
        if not source_instance:
            return mapping
        if subflow_ids:
            self.api_sync_flows()
        for subflow_id in subflow_ids or []:
            if not subflow_id or subflow_id in mapping:
                continue
            source_subflow, template = self._nr_ensure_subflow_template(source_instance, subflow_id)
            deployed_id = self._nr_deploy_subflow_template(template, source_subflow, source_subflow.instance_id)
            mapping[subflow_id] = deployed_id
        return mapping

    # ========================================================================
    # Unified Deploy — single entry point for wizard & data_model
    # ========================================================================

    def _nr_build_template_flow_payload(self, template_flow):
        """Build a Node-RED flow payload from a template flow (app store flow).
        
        This is the wizard-style payload: resolving configs from global,
        but without applying placeholders (that's done separately).
        """
        raw = template_flow.content or "{}"
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            parsed = {}

        nodes = parsed.get("nodes", []) if isinstance(parsed, dict) else []
        configs = parsed.get("configs", []) if isinstance(parsed, dict) else []
        nodes = [n for n in nodes if isinstance(n, dict)]
        non_subflow_nodes = [n for n in nodes if n.get("type") != "subflow"]
        flow_content_id = parsed.get("id") if isinstance(parsed, dict) and isinstance(parsed.get("id"), str) else False
        if flow_content_id:
            filtered_nodes = [
                n for n in non_subflow_nodes
                if not isinstance(n.get("z"), str) or n.get("z") == flow_content_id
            ]
            nodes = filtered_nodes or non_subflow_nodes
        else:
            nodes = non_subflow_nodes
        configs = [c for c in configs if isinstance(c, dict) and c.get("id")]
        if configs:
            refs = set()
            self._nr_collect_strings(nodes, refs)
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
                self._nr_collect_strings(cfg, nested)
                for nid in nested:
                    if nid in by_id and nid not in seen:
                        queue.append(nid)
            configs = resolved
        for c in configs:
            if isinstance(c, dict) and "z" in c:
                c.pop("z", None)

        if not configs:
            refs = set()
            self._nr_collect_strings(nodes, refs)
            # Can't resolve global configs here without source_instance, 
            # caller should use _nr_ensure_payload_configs_from_source_global
            pass

        payload = {
            "id": self._nr_generate_id(),
            "label": template_flow.name or "",
            "nodes": nodes,
            "configs": configs,
        }
        if isinstance(parsed, dict):
            for key in ("disabled", "info", "env"):
                if key in parsed:
                    payload[key] = parsed.get(key)
        tab_id = payload.get("id")
        if tab_id:
            for node in payload.get("nodes") or []:
                if isinstance(node, dict) and isinstance(node.get("z"), str):
                    node["z"] = tab_id
        return payload

    # ------------------------------------------------------------------------
    # Payload Validation
    # ------------------------------------------------------------------------
    _NR_ID_RE = re.compile(r"^(?:[0-9a-fA-F]{7}\.[0-9a-fA-F]{7}|[0-9a-fA-F]{16})$")
    _NR_REF_IGNORED_KEYS = frozenset({"id", "z", "links", "x", "y", "wires", "hw", "info"})

    def _nr_collect_config_ref_ids(self, value, out):
        """Collect strings that look like Node-RED config node references.

        Skips known structural keys (id, z, wires, etc.) so we only capture
        property values that reference other nodes (e.g. mqtt-broker config).
        """
        if isinstance(value, dict):
            for key, item in value.items():
                if key in self._NR_REF_IGNORED_KEYS:
                    continue
                self._nr_collect_config_ref_ids(item, out)
        elif isinstance(value, list):
            for item in value:
                self._nr_collect_config_ref_ids(item, out)
        elif isinstance(value, str):
            text = value.strip()
            if self._NR_ID_RE.match(text):
                out.add(text)

    def _nr_validate_deploy_payload(self, payload, template_flow):
        """Validate deploy payload before sending to Node-RED.

        Checks that:
        1. The payload has deployable nodes (non-empty)
        2. Config nodes referenced by nodes are present in configs

        Raises UserError with a diagnostic explanation on failure.
        """
        if not isinstance(payload, dict):
            raise UserError(_("Invalid flow payload."))

        nodes = [n for n in (payload.get("nodes") or []) if isinstance(n, dict)]
        configs = [c for c in (payload.get("configs") or []) if isinstance(c, dict) and c.get("id")]
        existing_config_ids = {c.get("id") for c in configs if isinstance(c.get("id"), str)}

        # --- 1. Validate nodes are not empty ---
        if not nodes:
            raw = template_flow.content or ""
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                parsed = {}

            if not raw or (isinstance(parsed, dict) and not parsed):
                # content was not parsed, re-read with explicit parse for diagnostics
                raw_nodes_list = []
            elif isinstance(parsed, dict):
                raw_nodes_list = [n for n in (parsed.get("nodes") or []) if isinstance(n, dict)]
            else:
                raise UserError(
                    _("Flow '%(name)s' content is not a valid flow object and cannot be deployed.",
                      name=template_flow.display_name)
                )

            if not raw_nodes_list:
                raise UserError(
                    _("Flow '%(name)s' has no deployable content.\n"
                      "The flow contains no nodes and cannot be sent to Node-RED.\n"
                      "Please check that the flow has been configured with valid nodes.",
                      name=template_flow.display_name)
                )

            # Had nodes but all were filtered out — check if they are all subflows
            subflow_count = sum(1 for n in raw_nodes_list if n.get("type") == "subflow")
            if subflow_count == len(raw_nodes_list):
                raise UserError(
                    _("Flow '%(name)s' only contains subflow nodes and has no "
                      "deployable regular nodes.",
                      name=template_flow.display_name)
                )

            raise UserError(
                _("Flow '%(name)s' nodes are invalid or could not be processed for deployment.",
                  name=template_flow.display_name)
            )

        # --- 2. Validate configs: nodes should not reference missing config IDs ---
        # Collect wire IDs to exclude (wires are node-to-node connections, not config refs)
        wire_ids = set()
        for node in nodes:
            wires = node.get("wires")
            if isinstance(wires, list):
                for wire_list in wires:
                    if isinstance(wire_list, list):
                        for wid in wire_list:
                            if isinstance(wid, str):
                                wire_ids.add(wid)

        # Collect potential config node reference IDs from node property values
        refs = set()
        for node in nodes:
            self._nr_collect_config_ref_ids(node, refs)

        # Filter: exclude wire targets (node connections) and own IDs
        node_own_ids = {n.get("id") for n in nodes if isinstance(n.get("id"), str)}
        config_refs = refs - wire_ids - node_own_ids

        missing_config_refs = config_refs - existing_config_ids
        if not missing_config_refs:
            return

        # Format missing IDs for display
        missing_sorted = sorted(missing_config_refs)
        missing_display = ", ".join(missing_sorted[:10])
        if len(missing_sorted) > 10:
            missing_display += _(" ... (+%(count)s more)", count=len(missing_sorted) - 10)

        if not configs:
            raise UserError(
                _("Flow '%(name)s' references config nodes but has no configs.\n"
                  "Missing config IDs: %(ids)s\n\n"
                  "This usually means the flow template has no source instance to "
                  "resolve config nodes from. Try re-publishing the flow from its "
                  "source instance.",
                  name=template_flow.display_name, ids=missing_display)
            )
        else:
            raise UserError(
                _("Flow '%(name)s' is missing required config nodes:\n"
                  "Missing IDs: %(ids)s",
                  name=template_flow.display_name, ids=missing_display)
            )

    def _nr_deploy_single_flow(self, template_flow, record, source_instance=None):
        """Deploy a single template flow to this instance.
        
        Args:
            template_flow: fts.nr.flow record (app store template)
            record: the context record (fts.nr.instance or fts.data.model)
            source_instance: the source instance if template came from one
        """
        self.ensure_one()

        payload = self._nr_build_template_flow_payload(template_flow)
        payload["label"] = f"{template_flow.name} - {self.name}"
        payload = self._nr_ensure_payload_configs_from_source_global(payload, source_instance)

        # Validate payload before further processing
        self._nr_validate_deploy_payload(payload, template_flow)

        subflow_ids = self._nr_collect_subflow_refs(payload.get("nodes") or [])
        if subflow_ids and not source_instance:
            raise UserError(
                _("Template '%(name)s' has subflows but source flow is not found.",
                  name=template_flow.display_name)
            )
        subflow_mapping = self._nr_ensure_subflows_deployed(source_instance, subflow_ids)
        self._nr_apply_subflow_mapping(payload.get("nodes") or [], subflow_mapping)

        payload = self._nr_remap_payload_ids(payload, preserve_subflow_refs=True)
        payload = self._nr_inject_iotdb_mqtt_broker_credentials(payload)
        payload = self._nr_apply_flow_params_to_payload(template_flow, record, payload)
        payload, _ = self._apply_remote_instance_mqtt_credentials(payload)
        payload = self._nr_sanitize_main_flow_payload(payload)
        new_nr_id = self._nr_post_main_flow_payload(payload)
        return new_nr_id

    def action_deploy_flows(self, flow_ids=None, record=None):
        """Public action to deploy flows from wizard or data_model.
        
        Args:
            flow_ids: list of flow records, or template flow ids, or None to use self.flow_ids
            record: the context record for param resolution (defaults to self)
        
        Returns dict suitable for client action notification.
        """
        self.ensure_one()
        if record is None:
            record = self

        flows = flow_ids
        if flows is None:
            flows = self.flow_ids

        if not flows:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Deploy Complete"),
                    "message": _("No flows to deploy."),
                    "type": "warning",
                    "sticky": False,
                },
            }

        Flow = self.env["fts.nr.flow"]
        ok_count = 0
        fail_count = 0
        error_messages = []
        deployed_flow_nr_ids = []
        deployed_template_ids = {}

        for flow in flows:
            if not flow:
                continue
            try:
                # Determine source instance for the template
                source_flow = Flow.search([("app_store_id", "=", flow.id)], limit=1)
                source_instance = source_flow.instance_id if source_flow and source_flow.instance_id else False

                new_nr_id = self._nr_deploy_single_flow(flow, record, source_instance)
                deployed_flow_nr_ids.append(new_nr_id)
                deployed_template_ids[new_nr_id] = flow.id
                ok_count += 1
            except Exception as e:
                fail_count += 1
                error_messages.append(f"{flow.display_name}: {str(e)}")

        # Sync to create local flow records
        self.api_sync_flows()
        created_records = Flow.search(
            [
                ("instance_id", "=", self.id),
                ("type", "=", "tab"),
                ("nr_id", "in", deployed_flow_nr_ids),
            ]
        )
        for deployed_flow in created_records:
            template_id = deployed_template_ids.get(deployed_flow.nr_id)
            if template_id:
                deployed_flow.write({"app_store_id": template_id})

        message = _("Success: %(ok)s, Failed: %(fail)s", ok=ok_count, fail=fail_count)
        if error_messages:
            message = f"{message}\n" + "\n".join(error_messages[:5])

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Deploy Complete"),
                "message": message,
                "type": "success" if fail_count == 0 else "warning",
                "sticky": False,
            },
        }
