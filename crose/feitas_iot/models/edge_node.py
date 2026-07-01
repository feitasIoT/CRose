import re
import os
import secrets
import string
import subprocess
import requests
import logging

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
    has_mqtt_client = fields.Boolean(string="MQTT Client")
    has_nodered = fields.Boolean(string="Node-RED")
    has_docker = fields.Boolean(string="Docker")
    docker_version = fields.Char(string="Docker Version")
    docker_api_version = fields.Char(string="Docker API Version")
    docker_arch = fields.Char(string="Docker Architecture")
    docker_os = fields.Char(string="Docker OS")
    nodered_version = fields.Char(string="Node-RED Version")
    nodered_ports = fields.Text(string="Node-RED Ports")

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
    os_version = fields.Selection([
        ('rasp', 'Raspberry'), 
        ('ubuntu', 'Ubuntu'),
        ('win', 'Windows'),
        ('android', 'Android')], string="OS Distribution")
    npm_registry_id = fields.Many2one("crose.component", string="NPM Registry", domain=[('component_type', '=', 'npm')])
    # MQTT Config，当边缘节点为网关并且勾选了MQTT Broker，则会得到CRose的MQTT账户。
    mqtt_broker_id = fields.Many2one("crose.component", string="MQTT Broker", domain=[('component_type', '=', 'mqtt')])
    mqtt_account_id = fields.Many2one(
        "crose.component.account",
        string="MQTT Account",
        domain="[('component_id', '=', mqtt_broker_id)]",
    )
    # 当边缘节点选择了网关并且勾选MQTT Client，则会得到Gateway MQTT账户。
    mqtt_user_id = fields.Many2one('fts.gateway.mqtt.user')
    mqtt_topic = fields.Char()

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

    def _ensure_gateway_mqtt_user(self, gateway=None, instance=None, require_mqtt_client=False):
        self.ensure_one()
        if require_mqtt_client and not self.has_mqtt_client:
            return False

        gateway = gateway or (self if self.is_gateway else self.gateway_id)
        if not gateway:
            raise UserError(_("Please set a Gateway first."))
        if not gateway.ip_address:
            raise UserError(_("Please configure Gateway IP Address first."))
        if not gateway.has_mqtt_broker:
            raise UserError(_("The selected Gateway has no MQTT broker enabled."))

        GatewayMqttUser = self.env["fts.gateway.mqtt.user"].sudo()
        mqtt_user = self.mqtt_user_id
        if mqtt_user and mqtt_user.gateway_id != gateway:
            mqtt_user = False
        if not mqtt_user:
            mqtt_user = GatewayMqttUser.search(
                [("gateway_id", "=", gateway.id), ("edge_node_id", "=", self.id)],
                limit=1,
            )

        username = (mqtt_user.username or "").strip() if mqtt_user else f"nr{self.id}_mqtt"
        plain_password = mqtt_user._get_plain_password() if mqtt_user else ""
        if not plain_password:
            plain_password = self._generate_gateway_mqtt_password()

        vals = {
            "gateway_id": gateway.id,
            "edge_node_id": self.id,
            "instance_id": instance.id if instance else False,
            "username": username,
            "password_encrypted": plain_password,
        }
        if mqtt_user:
            mqtt_user.write(vals)
            mqtt_user._sync_create_to_gmqtt()
        else:
            mqtt_user = GatewayMqttUser.create(vals)

        if self.mqtt_user_id != mqtt_user:
            self.mqtt_user_id = mqtt_user.id
        return mqtt_user

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

        _logger.warning("Ping to %s succeeded", self.ip_address)
        return True

    def _check_docker_installed(self):
        """
            Check if Docker is installed on the edge node via Docker Engine API.
            部署前预检：通过 Docker API 检查网关是否安装了 Docker
        """
        self.ensure_one()

        if not self.ip_address:
            raise UserError(_("Please set the edge node IP Address first."))

        docker_port = 2375
        endpoint = f"http://{self.ip_address}:{docker_port}/version"
        _logger.info("Checking Docker API: %s", endpoint)

        try:
            response = requests.get(endpoint, timeout=5)
            if response.status_code == 200:
                version_data = response.json()
                self.has_docker = True
                self.docker_version = version_data.get("Version", "")
                self.docker_api_version = version_data.get("ApiVersion", "")
                self.docker_arch = version_data.get("Arch", "")
                self.docker_os = version_data.get("Os", "")
                _logger.warning("Docker API reachable on %s", self.ip_address)
                return True
            if self.has_docker:
                raise UserError(
                    _(
                        "Docker API on %(ip)s returned HTTP %(code)s.",
                        ip=self.ip_address,
                        code=response.status_code,
                    )
                )
        except requests.RequestException as e:
            _logger.warning("Docker API check failed on %s: %s", self.ip_address, str(e))
            if self.has_docker:
                raise UserError(
                    _(
                        "Docker is not accessible on %(ip)s:%(port)s.\n"
                        "Please ensure Docker is installed and its API is exposed on port 2375, "
                        "or enable SSH for further check.",
                        ip=self.ip_address,
                        port=docker_port,
                    )
                )

    def _list_docker_containers(self):
        """
            公共方法：通过 Docker API 列出边缘节点上的所有容器。
            返回容器列表（list of dict），失败时返回空列表。
        """
        self.ensure_one()

        if not self.ip_address:
            return []

        docker_port = 2375
        list_endpoint = f"http://{self.ip_address}:{docker_port}/containers/json"
        _logger.info("Listing Docker containers on %s", self.ip_address)

        try:
            response = requests.get(list_endpoint, timeout=5)
            if response.status_code != 200:
                _logger.warning("Failed to list containers on %s: HTTP %s", self.ip_address, response.status_code)
                return []
        except requests.RequestException as e:
            _logger.warning("Failed to list containers on %s: %s", self.ip_address, str(e))
            return []

        return response.json()

    def _find_docker_container(self, keywords):
        """
            公共方法：在容器列表中查找镜像名或容器名包含指定关键字的容器。
            keywords: 关键字列表（全部转为小写匹配），如 ["node-red", "nodered"]
            返回匹配的容器 dict，未找到返回 None。
        """
        containers = self._list_docker_containers()
        if not containers:
            return None

        for container in containers:
            names = [n.lower().strip("/") for n in container.get("Names", [])]
            image = container.get("Image", "").lower()
            for name in names:
                if any(kw in name for kw in keywords):
                    return container
            if any(kw in image for kw in keywords):
                return container
        return None

    def _check_nodered_container(self):
        """
            Check if a Node-RED container exists on the edge node via Docker API,
            and record its version and port mappings.
            部署前预检：通过 Docker API 检查是否有 node-red 容器，记录版本和映射端口
        """
        self.ensure_one()

        if not self.ip_address:
            return

        nodered_container = self._find_docker_container(["node-red", "nodered"])

        if not nodered_container:
            self.has_nodered = False
            _logger.info("No Node-RED container found on %s", self.ip_address)
            return

        _logger.info("Node-RED container found on %s: %s", self.ip_address, nodered_container.get("Names"))

        # 检查容器详情，获取版本和端口映射
        container_id = nodered_container.get("Id")
        if not container_id:
            return

        docker_port = 2375
        inspect_endpoint = f"http://{self.ip_address}:{docker_port}/containers/{container_id}/json"
        try:
            inspect_response = requests.get(inspect_endpoint, timeout=5)
            if inspect_response.status_code != 200:
                _logger.warning("Failed to inspect container on %s: HTTP %s", self.ip_address, inspect_response.status_code)
                return
        except requests.RequestException as e:
            _logger.warning("Failed to inspect container on %s: %s", self.ip_address, str(e))
            return

        inspect_data = inspect_response.json()

        # 从镜像 tag 提取 Node-RED 版本
        config = inspect_data.get("Config", {})
        image = config.get("Image", "")
        version = ""
        if ":" in image:
            version = image.split(":")[-1]

        # 获取端口映射（可能有多个）
        network_settings = inspect_data.get("NetworkSettings", {})
        ports = network_settings.get("Ports", {})
        port_mappings = []
        for container_port, host_bindings in ports.items():
            if host_bindings:
                for binding in host_bindings:
                    host_port = binding.get("HostPort", "")
                    if host_port:
                        port_mappings.append(f"{host_port}:{container_port}")
            else:
                # 端口已暴露但未映射到宿主机
                port_mappings.append(container_port)

        self.has_nodered = True
        self.nodered_version = version
        self.nodered_ports = ", ".join(port_mappings) if port_mappings else ""
        _logger.info(
            "Node-RED container recorded: version=%s, ports=%s",
            self.nodered_version,
            self.nodered_ports,
        )

    def _check_frpc_container(self):
        """
            Check if a FRPC container exists on the edge node via Docker API.
            部署前预检：通过 Docker API 检查是否有 frpc 容器
        """
        self.ensure_one()

        if not self.ip_address:
            return

        frpc_container = self._find_docker_container(["frpc"])

        if not frpc_container:
            self.is_frpc = False
            _logger.info("No FRPC container found on %s", self.ip_address)
            return

        _logger.info("FRPC container found on %s: %s", self.ip_address, frpc_container.get("Names"))
        self.is_frpc = True

    def _check_gmqtt_container(self):
        """
            Check if a MQTT Broker (gmqtt) container exists on the edge node via Docker API.
            部署前预检：通过 Docker API 检查是否有 gmqtt 容器
        """
        self.ensure_one()

        if not self.ip_address:
            return

        gmqtt_container = self._find_docker_container(["gmqtt"])

        if not gmqtt_container:
            self.has_mqtt_broker = False
            _logger.info("No MQTT Broker (gmqtt) container found on %s", self.ip_address)
            return

        _logger.info("MQTT Broker (gmqtt) container found on %s: %s", self.ip_address, gmqtt_container.get("Names"))
        self.has_mqtt_broker = True

    def _get_gateway_template_dir(self):
        """
            根据 edge node 的 os_version 返回 static/files 下对应的模板目录路径。
            目录命名规则：gateway_docker_<os_distribution>
        """
        self.ensure_one()
        os_map = {
            "rasp": "rasp",
            "ubuntu": "ubuntu",
            "win": "win",
        }
        os_suffix = os_map.get(self.os_version)
        if not os_suffix:
            raise UserError(_("Unsupported OS Distribution: %(os)s", os=self.os_version))
        return f"static/files/gateway_docker_{os_suffix}"

    def _render_gateway_files(self, host_url=""):
        """
            读取模板目录下所有文件，替换模板变量后返回 {相对路径: 文件内容(bytes)} 字典。
            模板变量：
              - {{URL}} / {{url}}   → host_url（请求来源的 host，不含端口和路径）
              - {{Nexus Mapped Port}} → 所选 Repository 的 mapped_port
              - {{OS Distribution}} → os_version 的显示值
        """
        from odoo.modules import get_module_path

        self.ensure_one()

        rel_dir = self._get_gateway_template_dir()
        module_path = get_module_path("feitas_iot")
        if not module_path:
            raise UserError(_("Module feitas_iot not found."))
        base_dir = os.path.join(module_path, *rel_dir.split("/"))
        if not os.path.isdir(base_dir):
            raise UserError(_("Template directory not found: %(dir)s", dir=rel_dir))

        # 收集所有需替换的变量
        nexus_port = ""
        if self.repository_id:
            nexus_port = str(self.repository_id.mapped_port or "")
        os_distribution = self.os_version or ""

        rendered = {}
        # 递归遍历目录下所有文件
        for root, dirs, files in os.walk(base_dir):
            for filename in files:
                file_path = os.path.join(root, filename)
                rel_path = os.path.relpath(file_path, base_dir).replace("\\", "/")
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                except UnicodeDecodeError:
                    with open(file_path, "rb") as f:
                        content_bytes = f.read()
                    rendered[rel_path] = content_bytes
                    continue

                # 替换模板变量
                content = content.replace("{{URL}}", host_url)
                content = content.replace("{{url}}", host_url)
                content = content.replace("{{Nexus Mapped Port}}", nexus_port)
                content = content.replace("{{OS Distribution}}", os_distribution)
                rendered[rel_path] = content.encode("utf-8")

        return rendered

    def _build_gateway_download_url(self):
        """
            构建当前 edge node 的网关部署包下载 URL。
            返回完整的下载 URL 字符串，供部署命令和模板变量使用。
        """
        self.ensure_one()
        base_url = (
            self.env["ir.config_parameter"].sudo().get_param("web.base.url")
            or "http://localhost:8069"
        )
        base_url = base_url.rstrip("/")
        return f"{base_url}/crose/gateway/{self.id}"

    def _generate_deployment_commands(self):
        """
            根据 edge node 的 os_version 生成相应的下载命令，并写入 agent_cmd 字段。
        """
        self.ensure_one()
        download_url = self._build_gateway_download_url()

        os_commands = {
            "win": [
                f'Invoke-WebRequest -Uri "{download_url}" -OutFile "crose_gateway.zip"',
                "Expand-Archive -Path crose_gateway.zip -DestinationPath .",
                "docker compose up -d",
            ],
            "ubuntu": [
                f'wget -O crose_gateway.zip "{download_url}"',
                "unzip -o crose_gateway.zip",
                "docker compose up -d",
            ],
            "rasp": [
                f'wget -O crose_gateway.zip "{download_url}"',
                "unzip -o crose_gateway.zip",
                "docker compose up -d",
            ],
        }
        commands = os_commands.get(self.os_version)
        if not commands:
            raise UserError(_("Unsupported OS Distribution for deployment commands: %(os)s", os=self.os_version))

        self.agent_cmd = "\n".join(commands)

    def _generate_file_manifest(self):
        """
            生成压缩包文件清单，写入 config 字段。
        """
        self.ensure_one()
        files = self._render_gateway_files()
        lines = [f"{rel_path}" for rel_path in sorted(files.keys())]
        self.config = "\n".join(lines)

    def action_deploy(self):
        """
            部署按钮，具体功能参见： edge_management.md
        """
        for node in self:
            if node.status != "draft":
                continue
            node._check_ip_reachable()
            node._check_docker_installed()
            node._check_nodered_container()
            node._check_frpc_container()
            node._check_gmqtt_container()
            node._generate_deployment_commands()
            node._generate_file_manifest()
            if node.gateway_id and node.has_mqtt_client and not node.mqtt_user_id:
                node._ensure_gateway_mqtt_user(require_mqtt_client=True)


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

                mqtt_user = node._ensure_gateway_mqtt_user(gateway=gateway, instance=instance)
                note_lines.append(_("Gateway MQTT user synchronized: %(username)s", username=mqtt_user.username))

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
