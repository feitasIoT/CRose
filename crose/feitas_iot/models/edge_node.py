import re
import os
import secrets
import string
import subprocess
import requests
import logging
from urllib.parse import quote

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import file_open

_logger = logging.getLogger(__name__)


class FtsEdgeAgent(models.Model):
    _name = "fts.edge.agent"
    _description = "Edge Agent"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # 改名fts.edge.node
    name = fields.Char(string="Name", required=True)


class FtsEdgeNode(models.Model):
    _name = "fts.edge.node"
    _description = "Edge Node"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="Name", required=True)
    version = fields.Char(string="Version")
    repository_id = fields.Many2one("crose.component", string="Repository")
    # TODO：用于复制边缘网关
    is_template = fields.Boolean(string="Is Template")
    template_id = fields.Many2one("fts.edge.node", string="Template", domain="[('is_template', '=', True)]")

    is_gateway = fields.Boolean(string="Gateway")
    gateway_id = fields.Many2one("fts.edge.node", string="Gateway", domain="[('is_gateway', '=', True)]")
    node_ids = fields.One2many("fts.edge.node", "gateway_id", string="Nodes")
    
    ip_address = fields.Char(string="IP Address")
    another_ip_address = fields.Char(string="Another IP Address")

    use_ssh = fields.Boolean(string="SSH")
    ssh_username = fields.Char(string="SSH Username")
    ssh_password = fields.Char(string="SSH Password")
    ssh_port = fields.Integer(string="SSH Port", default=22)
    
    is_frpc = fields.Boolean(string="FRPC")
    has_mqtt_broker = fields.Boolean(string="MQTT Broker")
    has_nodered = fields.Boolean(string="Node-RED")
    has_docker = fields.Boolean(string="Docker")

    nodered_username = fields.Char(string="Node-RED Username")
    nodered_password = fields.Char(string="Node-RED Password")

    frpc_webserver_port = fields.Integer(string="FRPC Webserver Port", default=7400)
    frpc_webserver_username = fields.Char(string="FRPC Webserver Username", default="admin")
    frpc_webserver_password = fields.Char(string="FRPC Webserver Password", default="admin")

    use_frp = fields.Boolean(string="FRP")
    use_vnc = fields.Boolean(string="VNC")
    use_redis = fields.Boolean(string="Redis", help="checked will assign redis database and account.")

    domain = fields.Char(string="Domain")
    vnc_port = fields.Integer(string="VNC Port", default=6080)
    port = fields.Integer(string="Port", default=6080)
    agent_port = fields.Integer(string="Agent Port", default=18080)
    os_version = fields.Selection([('rasp', 'Raspberry'), ('ubuntu', 'Ubuntu')], string="OS Distribution")
    npm_registry_id = fields.Many2one("crose.component", string="NPM Registry", domain=[('component_type', '=', 'npm')])
    # MQTT Config
    mqtt_broker_id = fields.Many2one("crose.component", string="MQTT Broker", domain=[('component_type', '=', 'mqtt')])
    mqtt_account_id = fields.Many2one(
        "crose.component.account",
        string="MQTT Account",
        domain="[('component_id', '=', mqtt_broker_id)]",
    )
    redis_account_id = fields.Many2one(
        "crose.component.account",
        string="Redis Account",
        domain="[('component_id.component_type', '=', 'redis')]",
    )

    # Instance Config
    instance_ids = fields.One2many(
        "fts.nr.instance",
        "edge_node_id",
        string="Instances",
    )
    gateway_mqtt_user_ids = fields.One2many(
        "fts.gateway.mqtt.user",
        "gateway_id",
        string="Gateway MQTT Users",
    )
    instance_count = fields.Integer(string="Instance Count", compute="_compute_instance_count")
    gateway_mqtt_user_count = fields.Integer(
        string="Gateway MQTT User Count",
        compute="_compute_gateway_mqtt_user_count",
    )
    instance_id = fields.Many2one(
        "fts.nr.instance",
        string="Related Instance",
        domain=[('instance_type', '=', 'local')],
        help="Only local instances can be selected."
    )
    nr_node = fields.Text(string="NR Node")

    config = fields.Text("Configuration File")
    agent_cmd = fields.Text("Command")
    status = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirm", "Confirm"),
            ("online", "Online"),
            ("offline", "Offline"),
            ("error", "Error"),
        ],
        string="Status",
        default="draft",
        required=True,
    )
    flow_ids = fields.One2many("agent.flow.line", "agent_id", string="Flows")

#-------------onchange--------------
    @api.depends("instance_ids")
    def _compute_instance_count(self):
        for node in self:
            node.instance_count = len(node.instance_ids)

    def _compute_gateway_mqtt_user_count(self):
        user_model = self.env["fts.gateway.mqtt.user"].sudo()
        for node in self:
            if not node.is_gateway:
                node.gateway_mqtt_user_count = 0
                continue
            node.gateway_mqtt_user_count = user_model.search_count([("gateway_id", "=", node.id)])

    @api.onchange('template_id')
    def _onchange_template_id(self):
        if self.template_id:
            self.version = self.template_id.version

    @api.onchange("mqtt_broker_id")
    def _onchange_mqtt_broker_id(self):
        for node in self:
            if node.mqtt_account_id and node.mqtt_account_id.component_id != node.mqtt_broker_id:
                node.mqtt_account_id = False

    @api.constrains("use_vnc", "vnc_port")
    def _check_vnc_port_required(self):
        for node in self:
            if node.use_vnc and not node.vnc_port:
                raise ValidationError(_("VNC Port is required when Use VNC is enabled."))

    def _get_gateway_gmqtt_api_port(self):
        self.ensure_one()
        mqtt_api_port_raw = (
            self.env["ir.config_parameter"].sudo().get_param("feitas_iot.gateway_gmqtt_api_port") or "8083"
        )
        try:
            return int(str(mqtt_api_port_raw).strip())
        except Exception:
            return 8083

    def _build_gateway_gmqtt_api_base_url(self):
        self.ensure_one()
        if not self.is_gateway:
            raise UserError(_("Only gateway nodes support broker account synchronization."))
        if not self.has_mqtt_broker:
            raise UserError(_("The selected gateway has no MQTT broker enabled."))
        if not self.ip_address:
            raise UserError(_("Please configure Gateway IP Address first."))
        return f"http://{self.ip_address}:{self._get_gateway_gmqtt_api_port()}"

    def _generate_gateway_mqtt_password(self, length=12):
        self.ensure_one()
        if self.mqtt_broker_id and self.mqtt_broker_id.component_type == "mqtt":
            return self.mqtt_broker_id._generate_password(length)
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    def _default_instance_domain(self):
        self.ensure_one()
        return f"ni{self.id}.edge.local"

    def _default_vnc_domain(self):
        self.ensure_one()
        return f"nr{self.id}.edge.local"

    def _build_gateway_frpc_proxy_api_base(self, gateway):
        gateway.ensure_one()
        if not gateway.is_frpc:
            raise UserError(_("FRPC is not enabled on the selected Gateway."))
        if not gateway.frpc_webserver_port:
            raise UserError(_("Please configure FRPC Webserver Port on the selected Gateway."))
        proxy_api_base = f"http://{gateway.ip_address}:{gateway.frpc_webserver_port}/api/store/proxies"
        auth = (gateway.frpc_webserver_username or "", gateway.frpc_webserver_password or "")
        return proxy_api_base, auth

    def _upsert_gateway_frpc_http_proxy(self, gateway, proxy_name, local_ip, local_port, custom_domain):
        gateway.ensure_one()
        proxy_api_base, auth = self._build_gateway_frpc_proxy_api_base(gateway)
        payload = {
            "name": proxy_name,
            "type": "http",
            "http": {
                "localIP": local_ip,
                "localPort": int(local_port),
                "customDomains": [custom_domain],
            },
        }
        check_response = requests.get(
            f"{proxy_api_base}/{proxy_name}",
            auth=auth,
            timeout=15,
        )
        if check_response.status_code == 200:
            response = requests.put(
                f"{proxy_api_base}/{proxy_name}",
                auth=auth,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=15,
            )
        elif check_response.status_code == 404:
            response = requests.post(
                proxy_api_base,
                auth=auth,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=15,
            )
        else:
            raise UserError(
                _(
                    "Failed to check FRPC store proxy (HTTP %(status)s): %(detail)s",
                    status=check_response.status_code,
                    detail=check_response.text,
                )
            )

        if response.status_code >= 400:
            raise UserError(
                _(
                    "FRPC store proxy upsert failed (HTTP %(status)s): %(detail)s",
                    status=response.status_code,
                    detail=response.text,
                )
            )
        return True


#-------------actions--------------

    def _compute_status_from_instances(self):
        for node in self:
            statuses = set(node.instance_ids.mapped("status"))
            if not statuses:
                continue
            if statuses == {"online"}:
                target_status = "online"
            elif statuses == {"offline"}:
                target_status = "offline"
            elif "online" in statuses and ("offline" in statuses or "error" in statuses):
                target_status = "error"
            elif "error" in statuses:
                target_status = "error"
            else:
                target_status = "offline"
            if node.status != target_status:
                node.status = target_status

    def _check_ip_reachable(self):
        """
            Check if the node's IP address is reachable via ping.
            部署前预检：能否ping通IP地址1
        """
        self.ensure_one()
        if not self.ip_address:
            raise UserError(_("Please set the edge node IP Address first."))

        param = "-n 1 -w 2000" if os.name == "nt" else "-c 1 -W 2"
        cmd = f"ping {param} {self.ip_address}"
        _logger.info("Pinging IP address: %s", self.ip_address)

        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                error_output = result.stderr.strip() or result.stdout.strip()
                _logger.warning(
                    "Ping to %(ip)s failed (code %(code)s): %(detail)s",
                    {"ip": self.ip_address, "code": result.returncode, "detail": error_output},
                )
                raise UserError(
                    _(
                        "IP Address %(ip)s is not reachable (ping failed).\n%(detail)s",
                        ip=self.ip_address,
                        detail=error_output,
                    )
                )
        except subprocess.TimeoutExpired:
            _logger.warning("Ping to %s timed out", self.ip_address)
            raise UserError(_("Ping to IP Address %(ip)s timed out.", ip=self.ip_address))

        _logger.info("Ping to %s succeeded", self.ip_address)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Ping Complete'),
                'message': _('Ping synchronized.'),
                'type': 'success',
                'sticky': False
            }
        }

    def _check_repository_reachable(self):
        """
            Check if the node's Repository (NPM registry) is reachable.
            部署前预检：能否访问Repository
            能ping通IP地址1就表示网关可以访问Repository。
        """
        self.ensure_one()
        

    def action_deploy(self):
        """
            部署按钮，具体功能参见： edge_management.md
        """
        for node in self:
            if node.status != "draft":
                continue
            node._check_ip_reachable()
            

    def old_action_deploy(self):
        for node in self:
            broker = node.mqtt_broker_id
            if not broker or broker.component_type != "mqtt":
                broker = self.env["crose.component"].search(
                    [("component_type", "=", "mqtt"), ("status", "=", "online")],
                    limit=1,
                )
                if not broker:
                    broker = self.env["crose.component"].search(
                        [("component_type", "=", "mqtt")],
                        limit=1,
                    )
            if not broker:
                raise UserError(_("No MQTT broker found in System Components."))

            username = f"mqtt_edgenode_{node.id}"
            password = broker._generate_password(8)
            broker.api_create_users(username, password)
            account = self.env["crose.component.account"].sudo().search(
                [("component_id", "=", broker.id), ("username", "=", username)],
                limit=1,
            )

            node.write(
                {
                    "status": "confirm",
                    "mqtt_broker_id": broker.id,
                    "mqtt_account_id": account.id if account else False,
                }
            )

    def action_initialize(self):
        """
            初始化边缘节点：
            1) 创建 Node-RED 实例
            2) 非网关节点：调用网关 FRPC API为 Node-RED 实例创建 HTTP 代理
            3) 调用网关 Gmqtt API为 Node-RED 实例创建 MQTT client 账户
        """
        Instance = self.env["fts.nr.instance"].sudo()
        GatewayMqttUser = self.env["fts.gateway.mqtt.user"].sudo()

        for node in self:
            note_lines = []
            try:
                if not node.ip_address:
                    raise UserError(_("Please set the edge node IP Address first."))

                gateway = node if node.is_gateway else node.gateway_id
                if not gateway:
                    raise UserError(_("Please set a Gateway first."))
                if not gateway.ip_address:
                    raise UserError(_("Please configure Gateway IP Address first."))
                if not gateway.has_mqtt_broker:
                    raise UserError(_("The selected Gateway has no MQTT broker enabled."))

                instance_domain = node._default_instance_domain()
                instance_proxy_name = instance_domain.split(".", 1)[0]
                instance_host = instance_domain if node.use_frp else node.ip_address
                instance_name = f"{node.name}-NR"

                instance = Instance.search(
                    [("edge_node_id", "=", node.id), ("instance_type", "=", "remote"), ("name", "=", instance_name)],
                    limit=1,
                )
                if instance:
                    update_vals = {}
                    if instance.ip_address != instance_host:
                        update_vals["ip_address"] = instance_host
                    if not instance.port:
                        update_vals["port"] = 1880
                    if update_vals:
                        instance.write(update_vals)
                    note_lines.append(_("Node-RED instance reused: %(name)s", name=instance.display_name))
                else:
                    vals = {
                        "name": instance_name,
                        "instance_type": "remote",
                        "edge_node_id": node.id,
                        "ip_address": instance_host,
                        "port": 1880,
                    }
                    if node.mqtt_broker_id:
                        vals["mqtt_broker_id"] = node.mqtt_broker_id.id
                    instance = Instance.create(vals)
                    note_lines.append(_("Node-RED instance created: %(name)s", name=instance.display_name))

                if node.is_gateway or not node.use_frp:
                    note_lines.append(_("Gateway node: FRPC proxy step skipped."))
                else:
                    node._upsert_gateway_frpc_http_proxy(
                        gateway,
                        instance_proxy_name,
                        node.ip_address,
                        int(instance.port or 1880),
                        instance_domain,
                    )
                    note_lines.append(
                        _("FRPC proxy synchronized: %(proxy)s -> %(domain)s", proxy=instance_proxy_name, domain=instance_domain)
                    )

                    if node.use_vnc:
                        vnc_domain = (node.domain or "").strip() or node._default_vnc_domain()
                        if node.domain != vnc_domain:
                            node.domain = vnc_domain
                        node._upsert_gateway_frpc_http_proxy(
                            gateway,
                            vnc_domain.split(".", 1)[0],
                            node.ip_address,
                            int(node.vnc_port or node.port or 680),
                            vnc_domain,
                        )
                        note_lines.append(
                            _("FRPC proxy synchronized: %(proxy)s -> %(domain)s", proxy=vnc_domain.split(".", 1)[0], domain=vnc_domain)
                        )

                username = f"nr{node.id}_mqtt"
                mqtt_user = GatewayMqttUser.search(
                    [("gateway_id", "=", gateway.id), ("username", "=", username)],
                    limit=1,
                )
                plain_password = mqtt_user._get_plain_password() if mqtt_user else ""
                if not plain_password:
                    alphabet = string.ascii_letters + string.digits
                    plain_password = "".join(secrets.choice(alphabet) for _ in range(12))

                vals = {
                    "gateway_id": gateway.id,
                    "edge_node_id": node.id,
                    "instance_id": instance.id,
                    "username": username,
                    "password_encrypted": plain_password,
                }
                if mqtt_user:
                    mqtt_api_port = gateway._get_gateway_gmqtt_api_port()
                    mqtt_response = requests.post(
                        f"http://{gateway.ip_address}:{mqtt_api_port}/v1/accounts/{quote(username)}",
                        json={"password": plain_password},
                        timeout=15,
                    )
                    if mqtt_response.status_code >= 400:
                        raise UserError(
                            _(
                                "Gateway GMQTT account upsert failed (HTTP %(status)s): %(detail)s",
                                status=mqtt_response.status_code,
                                detail=mqtt_response.text,
                            )
                        )
                    mqtt_user.write(vals)
                    note_lines.append(_("Gateway MQTT user synchronized: %(username)s", username=username))
                else:
                    GatewayMqttUser.create(vals)
                    note_lines.append(_("Gateway MQTT user created: %(username)s", username=username))

                if node.use_redis:
                    redis_comp = self.env["crose.component"].search(
                        [("component_type", "=", "redis"), ("status", "=", "online")], limit=1
                    )
                    if not redis_comp:
                        redis_comp = self.env["crose.component"].search([("component_type", "=", "redis")], limit=1)
                    if not redis_comp:
                        raise UserError(_("No Redis component found in System Components."))

                    redis_username = ""
                    redis_password = ""
                    if (
                        node.redis_account_id
                        and node.redis_account_id.component_id
                        and node.redis_account_id.component_id.component_type == "redis"
                        and (node.redis_account_id.username or "").strip()
                    ):
                        redis_username = (node.redis_account_id.username or "").strip()
                        redis_password = node.redis_account_id._get_plain_password() or ""

                    if not redis_username:
                        redis_username = f"nr{node.id}_redis"
                    if not redis_password:
                        redis_password = redis_comp._generate_password(16)

                    redis_comp.api_create_redis_user(redis_username, redis_password)
                    redis_account = self.env["crose.component.account"].sudo().search(
                        [("component_id", "=", redis_comp.id), ("username", "=", redis_username)],
                        limit=1,
                    )
                    node.write({"redis_account_id": redis_account.id if redis_account else False})
                    note_lines.append(_("Redis account synchronized: %(username)s", username=redis_username))
            except Exception as e:
                note_lines.append(_("Initialization failed: %(error)s", error=str(e)))

            node.message_post(
                body="<br/>".join(note_lines),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
        return False

    def action_update(self):
        pass

    def action_view_gateway_mqtt_users(self):
        self.ensure_one()
        if not self.is_gateway:
            raise UserError(_("Only gateway nodes have gateway MQTT users."))
        return {
            "name": _("MQTT Users"),
            "res_model": "fts.gateway.mqtt.user",
            "type": "ir.actions.act_window",
            "view_mode": "list,form",
            "domain": [("gateway_id", "=", self.id)],
            "context": {"default_gateway_id": self.id},
            "target": "current",
        }

    def action_view_logs(self):
        self.ensure_one()
        action = self.env.ref("feitas_iot.action_node_red_logs_client", raise_if_not_found=False)
        if action:
            res = action.read()[0]
            res["params"] = {"agent_id": self.id, "title": _("Logs - %(name)s", name=self.name)}
            return res

    def action_generate_config(self):
        self.ensure_one()
        module_rel_path = "feitas_iot/data/edge_node_config.yaml.template"
        try:
            with file_open(module_rel_path, "rb") as f:
                data = f.read()
        except Exception:
            raise UserError(_("Template file not found or not readable: %(path)s", path=module_rel_path))

        try:
            template_text = data.decode("utf-8")
        except Exception:
            template_text = data.decode("utf-8", errors="ignore")

        if not template_text.strip():
            raise UserError(_("The template content is empty: edge_node_config.yaml.template"))

        def _placeholder_value(field_name):
            if not hasattr(self, field_name):
                return ""
            value = getattr(self, field_name)
            if value is None or value is False:
                return ""
            if isinstance(value, models.BaseModel):
                return value.display_name or ""
            return str(value)

        pattern = re.compile(r"\{\{\s*(?:record\.)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
        rendered = pattern.sub(lambda m: _placeholder_value(m.group(1)), template_text)
        self.config = rendered

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Generation Complete"),
                "message": _("The configuration has been generated from edge_node_config.yaml.template."),
                "type": "success",
                "sticky": False,
            },
        }

    def action_open_vnc(self):
        self.ensure_one()
        if not self.use_vnc:
            raise UserError(_("VNC is not enabled for this edge node."))
        action = self.env.ref("feitas_iot.action_node_red_editor_client", raise_if_not_found=False)
        if action:
            res = action.read()[0]
            res["display_name"] = _("Remote Desktop")
            res["name"] = _("Remote Desktop")
            use_edge_proxy = bool(self.use_frp)
            edge_proxy_port = 0
            target_host = (self.domain or "").strip() if use_edge_proxy else (self.ip_address or "").strip()
            if use_edge_proxy and not target_host:
                raise UserError(_("Please set Domain or initialize the edge node first."))
            if use_edge_proxy:
                edge_proxy_port = self.env["crose.component"]._get_mapped_port_by_type("nginx")
            res["params"] = {
                "node_red_url": f"http://{target_host}:{int(self.vnc_port or self.port or 680)}/vnc.html",
                "use_edge_proxy": use_edge_proxy,
                "edge_proxy_port": edge_proxy_port,
                "rewrite_browser_host": False,
            }
            return res
        return {}

    def action_view_instances(self):
        self.ensure_one()
        action = self.env.ref("feitas_iot.action_fts_nr_instance", raise_if_not_found=False)
        if action:
            res = action.read()[0]
            res["display_name"] = _("Instances")
            res["name"] = _("Instances")
            res["domain"] = [("edge_node_id", "=", self.id)]
            res["context"] = {
                "default_edge_node_id": self.id,
                "default_instance_type": "remote",
                "default_name": f"{self.name}-?",
                "default_ip_address": self.ip_address
            }
            return res
        return {}
