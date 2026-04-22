import json
import socket
import base64
import hashlib
import secrets
import string
import requests

from odoo import models, fields, api, _
from odoo.exceptions import UserError


class CroseComponent(models.Model):
    _name = "crose.component"
    _description = "System Component"

    name = fields.Char(string="Component Name", required=True)
    component_type = fields.Selection([
        ('mqtt', 'MQTT Service'),
        ('iotdb', 'IoTDB'),
        ('ai', 'AI Service'),
        ('llama_factory', 'LLaMA-Factory'),
        ('vllm', 'vLLM'),
        ('llm_provider', 'LLM Provider'),
        ('nas', 'NAS'),
        ('webdav', 'WebDAV'),
        ('npm', 'NPM Registry'),
        ('redis', 'Redis'),
        ('nodered', 'Node-RED')
    ], string="Component Type", required=True)
    status = fields.Selection([
        ('online', 'Online'),
        ('offline', 'Offline'),
        ('error', 'Error')
    ], string="Status", default='offline', readonly=True)
    host = fields.Char(string="Host")
    port = fields.Integer(string="Port")
    metadata = fields.Text(string="Metadata")
    account_ids = fields.One2many("crose.component.account", "component_id", string="Accounts")
    last_check_time = fields.Datetime(string="Last Check Time", readonly=True)
    error_reason = fields.Text(string="Error Reason", readonly=True)

    @api.onchange('component_type')
    def _onchange_component_type(self):
        """Set default metadata and port based on the component type."""
        if not self.component_type:
            return

        defaults = {
            'mqtt': {'metrics_port': 8082, 'metrics_endpoint': '/metrics', 'tcp_port': 1883, 'ws_port': 8083},
            'iotdb': {'dn_rpc_port': 6667, 'dn_internal_port': 10730},
            'ai': {
                'health_endpoint': '/health',
                'embed_endpoint': '/embed',
                'train_api_path': '/v1/train',
                'train_status_api_path': '/v1/train/{job_id}',
                'load_adapter_api_path': '/v1/vllm/adapters/load',
                'unload_adapter_api_path': '/v1/vllm/adapters/unload',
                'infer_api_path': '/v1/vllm/chat'
            },
            'llama_factory': {'health_endpoint': '/health', 'train_api_path': '/v1/train', 'train_status_api_path': '/v1/train/{job_id}'},
            'vllm': {'health_endpoint': '/v1/models', 'chat_completions_path': '/v1/chat/completions', 'temperature': 0.1},
            'llm_provider': {'chat_completions_path': '/v1/chat/completions', 'models_endpoint': '/v1/models', 'base_path': '/v1'},
            'npm': {'registry_url': 'http://verdaccio-staging:4873'},
            'redis': {'db': 0},
            'webdav': {'health_endpoint': '/api/health'},
            'nodered': {'admin_path': '/admin', 'health_endpoint': '/'}
        }

        port_defaults = {
            'mqtt': 1883,
            'iotdb': 6667,
            'llama_factory': 8000,
            'vllm': 8000,
            'llm_provider': 443,
            'npm': 4873,
            'redis': 6379,
            'webdav': 6065,
            'nodered': 1880
        }

        # Default host names based on docker-compose service names
        host_defaults = {
            'mqtt': 'gmqtt',
            'iotdb': 'iotdb',
            'ai': 'crose-ai',
            'llama_factory': 'crose-ai-train',
            'vllm': 'crose-vllm',
            'npm': 'verdaccio-staging',
            'webdav': 'crose-webdav',
            'nodered': 'nodered'
        }

        if self.component_type in defaults:
            self.metadata = json.dumps(defaults[self.component_type], indent=4)

        if self.component_type in port_defaults and not self.port:
            self.port = port_defaults[self.component_type]

        if self.component_type in host_defaults and not self.host:
            self.host = host_defaults[self.component_type]

    def _metadata_dict(self):
        self.ensure_one()
        if not self.metadata:
            return {}
        raw = (self.metadata or "").strip()
        try:
            parsed = json.loads(raw)
        except Exception:
            try:
                import ast

                parsed = ast.literal_eval(raw)
            except Exception:
                return {}
        return parsed if isinstance(parsed, dict) else {}

    def _build_component_base_url(self):
        self.ensure_one()
        host = (self.host or "").strip()
        port = self.port
        if not host or not port:
            raise ValueError(_("Component host and port are required."))
        return f"http://{host}:{port}"

    def _resolve_metadata_endpoint(self, key_name):
        self.ensure_one()
        metadata_dict = self._metadata_dict()
        raw_value = str(metadata_dict.get(key_name) or "").strip()
        if not raw_value:
            raise ValueError(_("Metadata key %(key)s is empty.", key=key_name))
        lower_value = raw_value.lower()
        if lower_value.startswith("http://") or lower_value.startswith("https://"):
            return raw_value
        if not raw_value.startswith("/"):
            raw_value = f"/{raw_value}"
        return f"{self._build_component_base_url()}{raw_value}"

    def action_check_status(self):
        for component in self:
            component._check_status()
        self._sync_overview_metrics()

    @api.model
    def _sync_overview_metrics(self):
        key_name = "host:metrics:current"
        metrics = {
            "cpu": "-",
            "memory": "-",
            "disk": "-",
            "network": "-",
        }
        redis_comp = self.search([("component_type", "=", "redis"), ("status", "=", "online")], limit=1)
        if not redis_comp:
            redis_comp = self.search([("component_type", "=", "redis")], limit=1)
        if redis_comp:
            metadata = {}
            if redis_comp.metadata:
                try:
                    metadata = json.loads(redis_comp.metadata)
                except Exception:
                    metadata = {}
            username = metadata.get("username")
            password = metadata.get("password")
            db = metadata.get("db", 0)
            try:
                db = int(db)
            except Exception:
                db = 0
            try:
                import redis

                client = redis.Redis(
                    host=redis_comp.host or "localhost",
                    port=redis_comp.port or 6379,
                    username=username,
                    password=password,
                    db=db,
                    decode_responses=True,
                    socket_connect_timeout=5,
                )
                value = None
                key_type = client.type(key_name)
                if isinstance(key_type, bytes):
                    key_type = key_type.decode(errors="ignore")
                if key_type == "string":
                    value = client.get(key_name)
                elif key_type == "hash":
                    value = client.hgetall(key_name)

                data = value
                if isinstance(value, str):
                    try:
                        data = json.loads(value)
                    except Exception:
                        data = {}
                if isinstance(data, dict):
                    metrics["cpu"] = data.get("cpu", data.get("CPU", metrics["cpu"]))
                    metrics["memory"] = data.get("memory", data.get("mem", data.get("MEMORY", metrics["memory"])))
                    metrics["disk"] = data.get("disk", data.get("DISK", metrics["disk"]))
                    metrics["network"] = data.get("network", data.get("NETWORK", metrics["network"]))
            except Exception:
                pass

        self.env["ir.config_parameter"].sudo().set_param(
            "feitas_iot.overview.metrics", json.dumps(metrics, ensure_ascii=False)
        )

    def _check_status(self):
        self.ensure_one()
        check_func = getattr(self, f"_check_status_{self.component_type}", None)
        if check_func:
            check_func()
        else:
            self.write({
                'status': 'error',
                'last_check_time': fields.Datetime.now(),
                'error_reason': _('No status check method found for component type %(type)s', type=self.component_type)
            })

    def _check_status_llm_provider(self):
        try:
            metadata_dict = self._metadata_dict()
            endpoint = ""
            if metadata_dict.get("health_endpoint"):
                endpoint = self._resolve_metadata_endpoint("health_endpoint")
            elif metadata_dict.get("models_endpoint"):
                endpoint = self._resolve_metadata_endpoint("models_endpoint")
            else:
                chat_url = str(metadata_dict.get("chat_completions_path") or "").strip()
                if chat_url:
                    endpoint = self._resolve_metadata_endpoint("chat_completions_path")
                    if endpoint.endswith("/chat/completions"):
                        endpoint = endpoint[: -len("/chat/completions")] + "/models"

            if not endpoint:
                raise ValueError(_("Metadata key %(key)s is empty.", key="models_endpoint"))

            headers = {}
            account = self.account_ids.filtered(lambda r: r.is_primary)[:1] or self.account_ids[:1]
            if account:
                token = account._get_plain_password()
                if token:
                    headers["Authorization"] = f"Bearer {token}"
            response = requests.get(endpoint, headers=headers, timeout=10)
            response.raise_for_status()
            self.write(
                {
                    "status": "online",
                    "last_check_time": fields.Datetime.now(),
                    "error_reason": False,
                }
            )
        except Exception as error:
            self.write(
                {
                    "status": "error",
                    "last_check_time": fields.Datetime.now(),
                    "error_reason": str(error),
                }
            )

    def _check_status_mqtt(self):
        try:
            metadata_dict = {}
            if self.metadata:
                try:
                    metadata_dict = json.loads(self.metadata)
                except json.JSONDecodeError:
                    pass
            metrics_endpoint = str(metadata_dict.get("metrics_endpoint") or "").strip()
            if metrics_endpoint:
                endpoint = self._resolve_metadata_endpoint("metrics_endpoint")
            else:
                metrics_port = int(metadata_dict.get("metrics_port") or 8082)
                endpoint = f"http://{(self.host or '').strip()}:{metrics_port}/metrics"
            response = requests.get(endpoint, timeout=5)
            if response.status_code == 200:
                self.write({'status': 'online', 'last_check_time': fields.Datetime.now(), 'error_reason': False})
            else:
                self.write({
                    'status': 'offline',
                    'last_check_time': fields.Datetime.now(),
                    'error_reason': _('Unexpected HTTP status code: %(code)s', code=response.status_code)
                })
        except Exception as e:
            self.write({
                'status': 'error',
                'last_check_time': fields.Datetime.now(),
                'error_reason': str(e)
            })

    def _check_status_iotdb(self):
        try:
            with socket.create_connection((self.host, self.port), timeout=5):
                self.write({'status': 'online', 'last_check_time': fields.Datetime.now(), 'error_reason': False})
        except Exception as e:
            self.write({
                'status': 'offline',
                'last_check_time': fields.Datetime.now(),
                'error_reason': _('Unable to connect to %(host)s:%(port)s - %(error)s', host=self.host, port=self.port, error=str(e))
            })

    def _check_status_ai(self):
        try:
            endpoint = self._resolve_metadata_endpoint("health_endpoint")
            response = requests.get(endpoint, timeout=5)
            if response.status_code == 200:
                self.write({'status': 'online', 'last_check_time': fields.Datetime.now(), 'error_reason': False})
            else:
                self.write({
                    'status': 'offline',
                    'last_check_time': fields.Datetime.now(),
                    'error_reason': _('Unexpected AI service response: %(code)s', code=response.status_code)
                })
        except Exception as e:
            self.write({
                'status': 'error',
                'last_check_time': fields.Datetime.now(),
                'error_reason': str(e)
            })

    def _check_status_llama_factory(self):
        try:
            endpoint = self._resolve_metadata_endpoint("health_endpoint")
            response = requests.get(endpoint, timeout=5)
            if response.status_code == 200:
                self.write({'status': 'online', 'last_check_time': fields.Datetime.now(), 'error_reason': False})
            else:
                self.write({
                    'status': 'offline',
                    'last_check_time': fields.Datetime.now(),
                    'error_reason': _('Unexpected LLaMA-Factory response: %(code)s', code=response.status_code)
                })
        except Exception as e:
            self.write({
                'status': 'error',
                'last_check_time': fields.Datetime.now(),
                'error_reason': str(e)
            })

    def _check_status_vllm(self):
        try:
            endpoint = self._resolve_metadata_endpoint("health_endpoint")
            response = requests.get(endpoint, timeout=8)
            if response.status_code == 200:
                self.write({'status': 'online', 'last_check_time': fields.Datetime.now(), 'error_reason': False})
            else:
                self.write({
                    'status': 'offline',
                    'last_check_time': fields.Datetime.now(),
                    'error_reason': _('Unexpected vLLM response: %(code)s', code=response.status_code)
                })
        except Exception as e:
            self.write({
                'status': 'error',
                'last_check_time': fields.Datetime.now(),
                'error_reason': str(e)
            })

    def _check_status_npm(self):
        try:
            endpoint = self._resolve_metadata_endpoint("registry_url")
            response = requests.get(endpoint, timeout=5)
            if response.status_code == 200:
                self.write({'status': 'online', 'last_check_time': fields.Datetime.now(), 'error_reason': False})
            else:
                self.write({
                    'status': 'offline',
                    'last_check_time': fields.Datetime.now(),
                    'error_reason': _('Unexpected NPM registry response: %(code)s', code=response.status_code)
                })
        except Exception as e:
            self.write({
                'status': 'error',
                'last_check_time': fields.Datetime.now(),
                'error_reason': str(e)
            })

    def _check_status_redis(self):
        try:
            import redis
            r = redis.Redis(host=self.host, port=self.port, socket_connect_timeout=5)
            if r.ping():
                self.write({'status': 'online', 'last_check_time': fields.Datetime.now(), 'error_reason': False})
            else:
                self.write({
                    'status': 'offline',
                    'last_check_time': fields.Datetime.now(),
                    'error_reason': _('Redis PING failed')
                })
        except Exception as e:
            self.write({
                'status': 'error',
                'last_check_time': fields.Datetime.now(),
                'error_reason': str(e)
            })

    def _check_status_nodered(self):
        try:
            endpoint = self._resolve_metadata_endpoint("health_endpoint")
            response = requests.get(endpoint, timeout=5)
            if response.status_code == 200:
                self.write({'status': 'online', 'last_check_time': fields.Datetime.now(), 'error_reason': False})
            else:
                self.write({
                    'status': 'offline',
                    'last_check_time': fields.Datetime.now(),
                    'error_reason': _('Unexpected Node-RED response: %(code)s', code=response.status_code)
                })
        except Exception as e:
            self.write({
                'status': 'error',
                'last_check_time': fields.Datetime.now(),
                'error_reason': str(e)
            })

    def _check_status_webdav(self):
        try:
            metadata_dict = self._metadata_dict()
            health_path = str(metadata_dict.get("health_endpoint") or "").strip()
            if health_path:
                endpoint = self._resolve_metadata_endpoint("health_endpoint")
            else:
                endpoint = f"{self._build_component_base_url()}/api/health"

            response = requests.get(endpoint, timeout=5)
            if response.status_code == 200:
                self.write({'status': 'online', 'last_check_time': fields.Datetime.now(), 'error_reason': False})
            else:
                self.write({
                    'status': 'offline',
                    'last_check_time': fields.Datetime.now(),
                    'error_reason': _('Unexpected WebDAV response: %(code)s', code=response.status_code)
                })
        except Exception as e:
            self.write({
                'status': 'error',
                'last_check_time': fields.Datetime.now(),
                'error_reason': str(e)
            })

    def action_view_packages(self):
        self.ensure_one()
        return {
            'name': _('Packages'),
            'res_model': 'crose.nr.package',
            'type': 'ir.actions.act_window',
            'view_mode': 'list,form',
            'context': {'default_component_id': self.id},
            'domain': [('component_id', '=', self.id)],
            'target': 'current',
        }

    def _generate_password(self, length=16):
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        return "".join(secrets.choice(alphabet) for _ in range(length))

    def _get_iotdb_connection_meta(self):
        self.ensure_one()
        host = self.host or "iotdb"
        port = self.port or 6667
        return host, int(port)

    def _iotdb_exec_non_query(self, username, password, sql):
        from iotdb.Session import Session

        host, port = self._get_iotdb_connection_meta()
        session = Session(host, str(port), username, password)
        session.open(False)
        try:
            session.execute_non_query_statement(sql)
        finally:
            try:
                session.close()
            except Exception:
                pass

    def _iotdb_set_user_password(self, login_user, login_password, target_user, new_password):
        safe_user = (target_user or "").replace("'", "''")
        safe_pwd = (new_password or "").replace("'", "''")
        alter_sql = f"ALTER USER {safe_user} SET PASSWORD '{safe_pwd}'"
        try:
            self._iotdb_exec_non_query(login_user, login_password, alter_sql)
            return
        except Exception as e:
            error_text = str(e).lower()
            user_not_found = "not found" in error_text or "701" in error_text
            if not user_not_found:
                raise
        create_sql = f"CREATE USER {safe_user} '{safe_pwd}'"
        self._iotdb_exec_non_query(login_user, login_password, create_sql)

    def _iotdb_reset_accounts(self, primary_account, accounts_to_reset):
        current_user = (primary_account.username or "").strip()
        current_password = primary_account._get_plain_password()
        if not current_user or not current_password:
            raise UserError(_("Primary account password is not decryptable. Please set the primary password again."))

        new_password_map = {acc.id: self._generate_password(16) for acc in accounts_to_reset}
        non_primary = [acc for acc in accounts_to_reset if acc.id != primary_account.id]
        for account in non_primary:
            self._iotdb_set_user_password(
                current_user,
                current_password,
                account.username,
                new_password_map[account.id],
            )
            account.write({"password_encrypted": new_password_map[account.id]})

        if primary_account.id in new_password_map:
            new_primary_pwd = new_password_map[primary_account.id]
            self._iotdb_set_user_password(
                current_user,
                current_password,
                primary_account.username,
                new_primary_pwd,
            )
            primary_account.write({"password_encrypted": new_primary_pwd})

        return [(acc.username, acc.role, new_password_map[acc.id]) for acc in accounts_to_reset]

    def action_reset_credentials(self):
        for component in self:
            if not component.account_ids:
                raise UserError(_("No account records found for this component."))
            primary = component.account_ids.filtered(lambda x: x.is_primary)[:1]
            if not primary:
                raise UserError(_("Please mark one account as primary before resetting credentials."))
            accounts_to_reset = component.account_ids.filtered(lambda x: (x.username or "").lower() != "root")
            if primary.id not in accounts_to_reset.ids:
                accounts_to_reset |= primary
            if not accounts_to_reset:
                raise UserError(_("No account available for credential reset."))

            if component.component_type == "iotdb":
                generated = component._iotdb_reset_accounts(primary, accounts_to_reset)
            else:
                generated = []
                for account in accounts_to_reset:
                    plain_password = component._generate_password(16)
                    account.write({"password_encrypted": plain_password})
                    generated.append((account.username, account.role, plain_password))

            lines = [f"{username} ({role}): {pwd}" for username, role, pwd in generated]
            message = _("Credentials reset successfully.") + "\n" + "\n".join(lines)
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Reset Complete"),
                    "message": message,
                    "type": "success",
                    "sticky": True,
                },
            }

    def _get_default_iotdb_privileges_for_role(self, role):
        role = (role or "").strip().lower()
        if role == "admin":
            return ["ALL"]
        if role == "operator":
            return ["READ_DATA", "WRITE_DATA"]
        if role == "viewer":
            return ["READ_DATA"]
        return []

    def _iotdb_grant_privilege(self, login_user, login_password, target_user, privilege, path="root.**"):
        safe_user = (target_user or "").strip()
        safe_priv = (privilege or "").strip().upper()
        safe_path = (path or "").strip()
        if not safe_user or not safe_priv or not safe_path:
            return
        statements = [
            f"GRANT {safe_priv} ON {safe_path} TO USER {safe_user}",
            f"GRANT USER {safe_user} PRIVILEGES {safe_priv} ON {safe_path}",
            f"GRANT {safe_priv} ON {safe_path} TO {safe_user}",
        ]
        last_error = None
        for sql in statements:
            try:
                self._iotdb_exec_non_query(login_user, login_password, sql)
                return
            except Exception as e:
                last_error = e
        if last_error:
            raise last_error

    def action_update_privileges(self):
        for component in self:
            if component.component_type != "iotdb":
                continue
            if not component.account_ids:
                raise UserError(_("No account records found for this component."))
            primary = component.account_ids.filtered(lambda x: x.is_primary)[:1]
            if not primary:
                raise UserError(_("Please mark one account as primary before updating privileges."))
            login_user = (primary.username or "").strip()
            login_password = primary._get_plain_password()
            if not login_user or not login_password:
                raise UserError(_("Primary account password is not decryptable. Please set the primary password again."))

            updated_lines = []
            targets = component.account_ids.filtered(lambda x: (x.username or "").strip() and (x.username or "").lower() != "root")
            for account in targets:
                raw_privs = (account.iotdb_privileges or "").strip()
                if raw_privs:
                    privileges = [p.strip() for p in raw_privs.split(",") if p.strip()]
                else:
                    privileges = component._get_default_iotdb_privileges_for_role(account.role)
                for privilege in privileges:
                    component._iotdb_grant_privilege(login_user, login_password, account.username, privilege, "root.**")
                updated_lines.append(f"{account.username}: {', '.join(privileges) if privileges else '-'}")

            message = _("Privileges updated successfully.")
            if updated_lines:
                message = message + "\n" + "\n".join(updated_lines)
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Update Complete"),
                    "message": message,
                    "type": "success",
                    "sticky": True,
                },
            }

    def _get_staging_storage_path(self, component=None):
        return '/mnt/verdaccio-staging'

    def _get_prod_storage_path(self, component=None):
        return '/mnt/verdaccio-prod'


class CroseComponentAccount(models.Model):
    _name = "crose.component.account"
    _description = "Component Account"
    _order = "write_date desc, id desc"

    component_id = fields.Many2one("crose.component", string="Component", required=True, ondelete="cascade")
    username = fields.Char(string="Username", required=True)
    password_encrypted = fields.Char(string="Encrypted Password", required=True)
    is_primary = fields.Boolean(string="Primary Account")
    role = fields.Selection(
        [
            ("admin", "Admin"),
            ("operator", "Operator"),
            ("viewer", "Viewer"),
            ("custom", "Custom"),
        ],
        string="Role",
        required=True,
        default="viewer",
    )
    iotdb_privileges = fields.Char(string="IoTDB Privileges")
    modified_by = fields.Many2one("res.users", string="Modified By", related="write_uid", readonly=True)
    modified_at = fields.Datetime(string="Modified At", related="write_date", readonly=True)
    
    _component_username_unique = models.Constraint(
        "unique(component_id, username)",
        message="The username already exists in this component.",
    )

    def name_get(self):
        result = []
        for record in self:
            comp = record.component_id.name or ""
            username = record.username or ""
            role = record.role or ""
            label = f"{comp} - {username}"
            if role:
                label = f"{label} ({role})"
            result.append((record.id, label.strip()))
        return result

    def _get_cipher(self):
        try:
            from cryptography.fernet import Fernet
        except Exception as e:
            raise UserError(_("Missing dependency cryptography: %(error)s", error=str(e)))
        secret = (self.env["ir.config_parameter"].sudo().get_param("database.secret") or "").strip()
        if not secret:
            raise UserError(_("Missing encryption secret in system parameter database.secret."))
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        return Fernet(key)

    def _decrypt_password(self, value):
        if not value:
            return ""
        text = str(value).strip()
        if not text:
            return ""
        if text.startswith("enc$"):
            token = text[4:]
            try:
                return self._get_cipher().decrypt(token.encode("utf-8")).decode("utf-8")
            except Exception:
                return ""
        if text.startswith("sha256$"):
            return ""
        return text

    def _get_plain_password(self):
        self.ensure_one()
        return self._decrypt_password(self.password_encrypted)

    def _encrypt_password(self, value):
        if not value:
            return value
        text = str(value)
        if text.startswith("enc$"):
            return text
        token = self._get_cipher().encrypt(text.encode("utf-8")).decode("utf-8")
        return f"enc${token}"

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if "password_encrypted" in vals:
                vals["password_encrypted"] = self._encrypt_password(vals.get("password_encrypted"))
        return super().create(vals_list)

    def write(self, vals):
        if "password_encrypted" in vals:
            vals["password_encrypted"] = self._encrypt_password(vals.get("password_encrypted"))
        return super().write(vals)
