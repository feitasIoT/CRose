import threading
import time
import re
import secrets
import string
import requests
import json
import logging
from urllib.parse import quote

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.tools import html2plaintext
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
    # TODO：用于复制边缘网关
    is_template = fields.Boolean(string="Is Template")
    template_id = fields.Many2one("fts.edge.node", string="Template", domain="[('is_template', '=', True)]")

    is_gateway = fields.Boolean(string="Is Gateway")
    gateway_id = fields.Many2one("fts.edge.node", string="Gateway", domain="[('is_gateway', '=', True)]")
    node_ids = fields.One2many("fts.edge.node", "gateway_id", string="Nodes")
    
    ip_address = fields.Char(string="IP Address")
    another_ip_address = fields.Char(string="Another IP Address")

    ssh_username = fields.Char(string="SSH Username")
    ssh_password = fields.Char(string="SSH Password")
    ssh_port = fields.Integer(string="SSH Port", default=22)
    
    is_frpc = fields.Boolean(string="Is FRPC")
    has_mqtt_broker = fields.Boolean(string="Has MQTT Broker")
    has_nodered = fields.Boolean()
    has_docker = fields.Boolean()

    nodered_username = fields.Char(string="Node-RED Username")
    nodered_password = fields.Char(string="Node-RED Password")

    frpc_webserver_port = fields.Integer(string="FRPC Webserver Port", default=7400)
    frpc_webserver_username = fields.Char(string="FRPC Webserver Username", default="admin")
    frpc_webserver_password = fields.Char(string="FRPC Webserver Password", default="admin")

    use_frp = fields.Boolean(string="Use FRP")

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

    def action_confirm(self):
        """
            gateway节点，确认时自动分配mqtt broker和account
        """
        for node in self:
            if node.status != "draft":
                continue
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

                domain = f"nr{node.id}.edge.local"
                proxy_name = domain.split(".", 1)[0]
                instance_host = node.ip_address if node.is_gateway else domain
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

                if node.is_gateway:
                    note_lines.append(_("Gateway node: FRPC proxy step skipped."))
                else:
                    if not gateway.is_frpc:
                        raise UserError(_("FRPC is not enabled on the selected Gateway."))
                    if not gateway.frpc_webserver_port:
                        raise UserError(_("Please configure FRPC Webserver Port on the selected Gateway."))

                    proxy_api_base = f"http://{gateway.ip_address}:{gateway.frpc_webserver_port}/api/store/proxies"
                    auth = (gateway.frpc_webserver_username or "", gateway.frpc_webserver_password or "")
                    payload = {
                        "name": proxy_name,
                        "type": "http",
                        "http": {
                            "localIP": node.ip_address,
                            "localPort": int(instance.port or 1880),
                            "customDomains": [domain],
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
                            _("Failed to check FRPC store proxy (HTTP %(status)s): %(detail)s", status=check_response.status_code, detail=check_response.text)
                        )

                    if response.status_code >= 400:
                        raise UserError(
                            _("FRPC store proxy upsert failed (HTTP %(status)s): %(detail)s", status=response.status_code, detail=response.text)
                        )
                    note_lines.append(_("FRPC proxy synchronized: %(proxy)s -> %(domain)s", proxy=proxy_name, domain=domain))

                username = f"nr{node.id}_mqtt"
                mqtt_user = GatewayMqttUser.search(
                    [("gateway_id", "=", gateway.id), ("username", "=", username)],
                    limit=1,
                )
                plain_password = mqtt_user._get_plain_password() if mqtt_user else ""
                if not plain_password:
                    alphabet = string.ascii_letters + string.digits
                    plain_password = "".join(secrets.choice(alphabet) for _ in range(12))

                mqtt_api_port_raw = (
                    self.env["ir.config_parameter"].sudo().get_param("feitas_iot.gateway_gmqtt_api_port") or "8083"
                )
                try:
                    mqtt_api_port = int(str(mqtt_api_port_raw).strip())
                except Exception:
                    mqtt_api_port = 8083
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

                vals = {
                    "gateway_id": gateway.id,
                    "edge_node_id": node.id,
                    "instance_id": instance.id,
                    "username": username,
                    "password_encrypted": plain_password,
                }
                if mqtt_user:
                    mqtt_user.write(vals)
                    note_lines.append(_("Gateway MQTT user synchronized: %(username)s", username=username))
                else:
                    GatewayMqttUser.create(vals)
                    note_lines.append(_("Gateway MQTT user created: %(username)s", username=username))
            except Exception as e:
                note_lines.append(_("Initialization failed: %(error)s", error=str(e)))

            node.message_post(
                body="<br/>".join(note_lines),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
        return False

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

    def message_post(self, **kwargs):
        message = super().message_post(**kwargs)

        # Check if we should trigger AI response
        # 1. Message is a comment
        # 2. Skip if explicitly asked to skip
        if kwargs.get('message_type') == 'comment' and not self.env.context.get('skip_ai_reply'):
            # Try to find AI partner via XML ID first, then name
            ai_partner = self.env.ref('feitas_iot.partner_ai_assistant', raise_if_not_found=False)
            if not ai_partner:
                ai_partner = self.env['res.partner'].sudo().search([('name', '=', 'AI Assistant')], limit=1)

            # Check if AI is mentioned (partner_ids OR text body)
            is_mentioned = False
            if ai_partner and ai_partner.id in message.partner_ids.ids:
                is_mentioned = True
            elif '@AI Assistant' in (message.body or ''):
                is_mentioned = True

            if is_mentioned:
                _logger.info(f"AI Assistant triggered for message {message.id}")

                # Check API Key immediately to give feedback
                api_key = self.env['ir.config_parameter'].sudo().get_param('feitas_iot.deepseek_api_key')
                if not api_key:
                    _logger.warning("DeepSeek API Key missing")
                    # Post warning to user
                    self.with_context(skip_ai_reply=True).message_post(
                        body=_("⚠️ System notice: the AI API key is not configured. Please set `feitas_iot.deepseek_api_key` in system parameters."),
                        message_type='comment',
                        subtype_xmlid='mail.mt_note'
                    )
                    return message

                if ai_partner:
                    # Use threading to avoid blocking the UI
                    # Register callback to run after commit to ensure message exists in DB for the new thread
                    def trigger_ai():
                        thread = threading.Thread(target=self._chat_with_ai_threaded, args=(message.id, ai_partner.id))
                        thread.start()
                    self.env.cr.postcommit.add(trigger_ai)

        return message

    def _chat_with_ai_threaded(self, message_id, ai_partner_id):
        """
        Threaded wrapper for AI chat
        """
        with self.pool.cursor() as new_cr:
            self = self.with_env(self.env(cr=new_cr))
            message = self.env['mail.message'].browse(message_id)
            ai_partner = self.env['res.partner'].browse(ai_partner_id)
            self._chat_with_ai(message, ai_partner)

    def _chat_with_ai(self, message, ai_partner):
        """
        Send message to LLM and post response (Streaming simulation)
        """
        _logger.info(f"Starting AI chat for message {message.id}")
        api_key = self.env['ir.config_parameter'].sudo().get_param('feitas_iot.deepseek_api_key')
        if not api_key:
            return

        base_url = self.env['ir.config_parameter'].sudo().get_param('feitas_iot.deepseek_base_url', 'https://api.deepseek.com')
        model = self.env['ir.config_parameter'].sudo().get_param('feitas_iot.deepseek_model', 'deepseek-chat')

        # Avoid replying to itself (double check)
        if message.author_id == ai_partner:
            return

        # 1. Post a placeholder "Thinking..." message immediately
        placeholder_content = "AI is thinking... <i class='fa fa-spinner fa-spin'></i>"
        reply_message = self.with_context(skip_ai_reply=True).message_post(
            body=placeholder_content,
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
            author_id=ai_partner.id,
            partner_ids=[] # Don't notify anyone for placeholder? Or maybe yes.
        )
        self.env.cr.commit() # Commit immediately to ensure "Thinking..." is visible via Bus

        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            }

            # Prepare conversation history
            # Ideally we should fetch previous messages in the thread
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant for IoT Edge Agent management."},
                    {"role": "user", "content": html2plaintext(message.body or "")}
                ],
                "stream": True # Enable streaming
            }

            response = requests.post(f"{base_url}/chat/completions", headers=headers, json=payload, stream=True, timeout=60)

            if response.status_code != 200:
                 reply_message.write({'body': f"AI API Error: {response.status_code} - {response.text}"})
                 return

            full_content = ""
            last_update_time = time.time()

            # 2. Process stream
            for line in response.iter_lines():
                if not line:
                    continue

                line_text = line.decode('utf-8')
                if line_text.startswith("data: "):
                    data_str = line_text[6:]
                    if data_str == "[DONE]":
                        break

                    try:
                        data = json.loads(data_str)
                        delta = data.get('choices', [{}])[0].get('delta', {}).get('content', '')
                        if delta:
                            full_content += delta

                            # Update DB every 0.5 seconds to simulate streaming without killing DB
                            if time.time() - last_update_time > 0.5:
                                reply_message.write({'body': full_content + " <i class='fa fa-spinner fa-spin'></i>"})
                                self.env.cr.commit() # Commit to make visible to other transactions/UI
                                last_update_time = time.time()

                    except json.JSONDecodeError:
                        continue

            # 3. Final update
            reply_message.write({'body': full_content})
            self.env.cr.commit()

        except Exception as e:
            _logger.error(f"Failed to call AI API: {str(e)}")
            reply_message.write({'body': f"AI Error: {str(e)}"})
            self.env.cr.commit()

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
        action = self.env.ref("feitas_iot.action_node_red_editor_client", raise_if_not_found=False)
        if action:
            res = action.read()[0]
            res["display_name"] = _("Remote Desktop")
            res["name"] = _("Remote Desktop")
            use_edge_proxy = not self.is_gateway
            res["params"] = {
                "node_red_url": f"http://{self.ip_address}:{self.port}/vnc.html",
                "use_edge_proxy": use_edge_proxy,
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
