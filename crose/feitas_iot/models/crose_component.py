import json
import os
import requests
import socket
from odoo import models, fields, api, _

class CroseComponent(models.Model):
    _name = "crose.component"
    _description = "System Component"

    name = fields.Char(string=_("Component Name"), required=True)
    component_type = fields.Selection([
        ('mqtt', 'MQTT Service'),
        ('iotdb', 'IoTDB'),
        ('ai', 'AI Service'),
        ('nas', 'NAS'),
        ('npm', 'NPM Registry'),
        ('redis', 'Redis'),
        ('nodered', 'Node-RED')
    ], string=_("Component Type"), required=True)
    status = fields.Selection([
        ('online', 'Online'),
        ('offline', 'Offline'),
        ('error', 'Error')
    ], string=_("Status"), default='offline', readonly=True)
    host = fields.Char(string=_("Host"))
    port = fields.Integer(string=_("Port"))
    url = fields.Char(string=_("URL"))
    metadata = fields.Text(string=_("Metadata"))
    last_check_time = fields.Datetime(string=_("Last Check Time"), readonly=True)
    error_reason = fields.Text(string=_("Error Reason"), readonly=True)

    @api.onchange('component_type')
    def _onchange_component_type(self):
        """Set default metadata and port based on the component type."""
        if not self.component_type:
            return

        defaults = {
            'mqtt': {'metrics_port': 8082, 'tcp_port': 1883, 'ws_port': 8083},
            'iotdb': {'dn_rpc_port': 6667, 'dn_internal_port': 10730},
            'ai': {'health_endpoint': '/health'},
            'npm': {'registry_url': 'http://verdaccio-staging:4873'},
            'redis': {'db': 0},
            'nodered': {'admin_path': '/admin'}
        }
        
        port_defaults = {
            'mqtt': 1883,
            'iotdb': 6667,
            'npm': 4873,
            'redis': 6379,
            'nodered': 1880
        }

        # Default host names based on docker-compose service names
        host_defaults = {
            'mqtt': 'gmqtt',
            'iotdb': 'iotdb',
            'ai': 'crose-ai',
            'npm': 'verdaccio-staging',
            'nodered': 'nodered'
        }

        if self.component_type in defaults:
            self.metadata = json.dumps(defaults[self.component_type], indent=4)
        
        if self.component_type in port_defaults and not self.port:
            self.port = port_defaults[self.component_type]

        if self.component_type in host_defaults and not self.host:
            self.host = host_defaults[self.component_type]
            
        if not self.url:
            if self.component_type == 'npm':
                self.url = f"http://{host_defaults.get('npm')}:{port_defaults.get('npm')}"
            elif self.component_type == 'nodered':
                self.url = f"http://{host_defaults.get('nodered')}:{port_defaults.get('nodered')}"
            elif self.component_type == 'ai':
                self.url = f"http://{host_defaults.get('ai')}:8000/health"

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

    def _check_status_mqtt(self):
        try:
            metadata_dict = {}
            if self.metadata:
                try:
                    metadata_dict = json.loads(self.metadata)
                except json.JSONDecodeError:
                    pass
            metrics_port = metadata_dict.get('metrics_port', 8082)
            url = f"http://{self.host}:{metrics_port}/metrics"
            response = requests.get(url, timeout=5)
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
            if not self.url:
                raise ValueError(_("AI service URL is not configured."))
            response = requests.get(self.url, timeout=5)
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

    def _check_status_npm(self):
        try:
            if not self.url:
                raise ValueError(_("NPM registry URL is not configured."))
            response = requests.get(self.url, timeout=5)
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
            if not self.url:
                raise ValueError(_("Node-RED URL is not configured."))
            response = requests.get(self.url, timeout=5)
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

    def _get_staging_storage_path(self, component=None):
        return '/mnt/verdaccio-staging'

    def _get_prod_storage_path(self, component=None):
        return '/mnt/verdaccio-prod'
