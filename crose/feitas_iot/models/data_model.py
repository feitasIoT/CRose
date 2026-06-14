import re
import json
import math
import uuid
import redis
import base64
import logging
import requests
import contextlib
from urllib.parse import quote

from datetime import datetime


from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError


_logger = logging.getLogger(__name__)

SPREADSHEET_VERSION = "18.5.1"
SPREADSHEET_SHEET_ID = "Sheet1"


class DataModel(models.Model):
    _name = 'fts.data.model'
    _description = 'Data Model'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'spreadsheet.mixin']

    name = fields.Char(string='Code', required=True, copy=False)
    data_direction = fields.Selection([('pub', 'Publish'), ('con', 'Consumption')], string="Data Direction")
    partner_id = fields.Many2one('res.partner', string='Requester', required=False)  # 废弃
    data_asset_id = fields.Many2one('fts.data.asset', string='Data Asset', required=False) 
    partner_id = fields.Many2one('res.partner', string='Partner', related='data_asset_id.partner_id', store=True, readonly=True)
    data_asset_ids = fields.Many2many("fts.data.asset", string="Assets", relation="rel_data_asset_modeling")  
    query_data_asset_ids = fields.Many2many("fts.data.asset", relation="rel_query_data_asset", string="Query Assets")  
    protocol = fields.Selection([
        ('modbus-tcp', 'Modbus-TCP'),
        ('modbus-rtu', 'Modbus-RTU'),
        ('mqtt', 'MQTT'),
        ('http', 'HTTP'),
        ('coap', 'CoAP'),
        ('smb', 'SMB2'),
        ('webdav', 'WebDAV'),
    ], string='Protocol', required=True)
    host = fields.Char(string='Host')
    tcp_port = fields.Integer(string='Port')
    serial_port = fields.Char(string='Serial Port', default="/dev/ttyUSB0")
    tcp_type = fields.Selection([
        ('default', 'Default'),
        ('rtu-buffered', 'RTU Buffered'),
    ], string='TCP Type')
    slave_id = fields.Integer(string='Slave ID')
    # 原则：约定优于配置，{{asset_name}}
    smb_share = fields.Char(string='Shared Directory', help='SMB shared directory path, for example: /share')
    username = fields.Char(string='Username')
    password = fields.Char(string='Password')

    log_database = fields.Many2one("crose.component", string="Redis")
    time_series_database = fields.Many2one("crose.component", string="IoTDB")
    query_type = fields.Selection([
        ('data', 'Time-Series Data'),
        ('log', 'Logs'),
    ], string='Query Type', default='data', required=True)
    query_start_time = fields.Datetime(string='Start Time')
    query_end_time = fields.Datetime(string='End Time')
    query_interval = fields.Integer(string='Interval (Seconds)', default=60)

    description = fields.Text(string='Description')
    mqtt_topic_id = fields.Many2one('fts.mqtt.topic', string='MQTT Topic')
    nr_instance_id = fields.Many2one('fts.nr.instance', string='Stage Instance', help='Local instance responsible for data processing')
    prod_instance_id = fields.Many2one('fts.nr.instance', string='Prod Instance', help='Local instance responsible for data processing')
    gateway_id = fields.Many2one('fts.nr.instance', string='Gateway Instance')
    nr_flow_ids = fields.Many2many('fts.nr.flow', 'data_model_nr_flow_rel', string='Flows')
    app_ids = fields.One2many("fts.data.app", "model_id", string='Applications')
    app_param_ids = fields.One2many("fts.nr.flow.param", "model_id", string='Application Parameters')
    log_ids = fields.One2many('fts.data.log', 'model_id', string='Logs')
    address_ids = fields.One2many('fts.data.address', 'model_id', string='Addresses')
    data_structure = fields.Text(string='Data Structure', required=False, default="{}")
    # FIXME: remove
    state = fields.Selection([
        ('draft', 'Draft'),
        ('approval', 'Approval'),
        ('effective', 'Effective'),
        ('invalid', 'Invalid'),
    ], string='Status', default='draft', required=True)
    data_status = fields.Selection([
        ('normal', 'Normal'),
        ('abnormal', 'Exceptional'),
    ], string='Data Status', default='normal', required=True)

    # FIXME：
    data_asset = fields.Char(string='Data Asset?', compute='_compute_data_asset', store=True)
    topic = fields.Char(string='Topic', compute='_compute_topic', store=True)
    iotdb_topic = fields.Char(string='IoTDB Topic', compute='_compute_topic', store=True)
    is_demo = fields.Boolean(string='Demo', default=False)

    @api.model
    def _get_default_local_instance(self, xmlid_name):
        instance = self.env.ref(xmlid_name, raise_if_not_found=False)
        if instance and instance.exists():
            return instance
        return self.env['fts.nr.instance'].search([('instance_type', '=', 'local')], limit=1)

    @api.model
    def _get_default_component_by_type(self, component_type):
        component = self.env['crose.component'].search(
            [('component_type', '=', component_type), ('status', '=', 'online')],
            limit=1,
        )
        if not component:
            component = self.env['crose.component'].search([('component_type', '=', component_type)], limit=1)
        return component

    @api.model
    def _generate_default_code(self):
        today = fields.Date.context_today(self)
        date_part = today.strftime('%y%m%d')
        prefix = f"DM{date_part}"
        latest = self.search([('name', '=like', f'{prefix}%')], order='name desc', limit=1)
        seq = 1
        if latest and latest.name and re.fullmatch(rf"{prefix}\d{{3}}", latest.name):
            seq = int(latest.name[-3:]) + 1
        return f"{prefix}{seq:03d}"

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if 'name' in fields_list and not values.get('name'):
            values['name'] = self._generate_default_code()
        if 'nr_instance_id' in fields_list and not values.get('nr_instance_id'):
            stage_instance = self._get_default_local_instance('feitas_iot.fts_nr_instance_staging')
            if stage_instance:
                values['nr_instance_id'] = stage_instance.id
        if 'prod_instance_id' in fields_list and not values.get('prod_instance_id'):
            prod_instance = self.env.ref('feitas_iot.fts_nr_instance_prod', raise_if_not_found=False)
            if not prod_instance or not prod_instance.exists():
                prod_instance = self._get_default_local_instance('feitas_iot.fts_nr_instance_prod')
            if prod_instance:
                values['prod_instance_id'] = prod_instance.id
        if 'log_database' in fields_list and not values.get('log_database'):
            log_db = self._get_default_component_by_type('redis')
            if log_db:
                values['log_database'] = log_db.id
        if 'time_series_database' in fields_list and not values.get('time_series_database'):
            ts_db = self._get_default_component_by_type('iotdb')
            if ts_db:
                values['time_series_database'] = ts_db.id
        return values

    @api.constrains('name', 'partner_id', 'data_direction')
    def _check_name_provider_unique(self):
        for record in self:
            existing = self.search_count([
                ('name', '=', record.name),
                ('partner_id', '=', record.partner_id.id),
                ('data_direction', '=', record.data_direction),
                ('id', '!=', record.id),
            ])
            if existing:
                raise ValidationError(_('The combination of Code, Partner and Direction must be unique.'))

    @api.constrains('log_database', 'time_series_database')
    def _check_storage_component_types(self):
        for record in self:
            if record.log_database and record.log_database.component_type != 'redis':
                raise ValidationError(_('Log Storage must be a Redis system component.'))
            if record.time_series_database and record.time_series_database.component_type != 'iotdb':
                raise ValidationError(_('Time-Series Storage must be an IoTDB system component.'))
    
    @api.onchange('query_start_time')
    def _onchange_query_start_time(self):
        if self.query_start_time and not self.query_end_time:
            self.query_end_time = fields.Datetime.now()
        if self.query_start_time and not self.query_interval:
            self.query_interval = 60

    @api.onchange("data_asset_ids")
    def _onchange_data_asset_ids(self):
        self.query_data_asset_ids = [(6, 0, self.data_asset_ids.ids)]

    @api.depends('partner_id.name', 'name')
    def _compute_data_asset(self):
        for record in self:
            record.data_asset = f'{record.partner_id.name}.{record.name}' if record.partner_id and record.name else False

    @api.depends('partner_id.name', 'data_direction', 'name')
    def _compute_topic(self):
        for record in self:
            partner_name = (record.partner_id.name or '').strip()
            direction = 'provider' if record.data_direction == 'pub' else 'requester'
            if record.name and partner_name:
                record.topic = f'upload/{partner_name}/{direction}/{record.name}'
                record.iotdb_topic = f'root.{partner_name}.{direction}.{record.name}'
            elif record.name:
                record.topic = f'upload/{direction}/{record.name}'
                record.iotdb_topic = f'root.{direction}.{record.name}'
            else:
                record.topic = False
                record.iotdb_topic = False

    def _format_json_text(self, value):
        if value is None:
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, indent=2)
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except Exception:
                raise ValidationError(_("The data structure is not valid JSON."))
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        raise ValidationError(_("The data structure is not valid JSON."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not (vals.get('name') or '').strip():
                vals['name'] = self._generate_default_code()
            if 'data_structure' in vals:
                vals['data_structure'] = self._format_json_text(vals.get('data_structure'))
            if not vals.get('nr_instance_id'):
                stage_instance = self.env.ref('feitas_iot.fts_nr_instance_staging', raise_if_not_found=False)
                if stage_instance and stage_instance.exists():
                    vals['nr_instance_id'] = stage_instance.id
            if not vals.get('prod_instance_id'):
                prod_instance = self.env.ref('feitas_iot.fts_nr_instance_prod', raise_if_not_found=False)
                if prod_instance and prod_instance.exists():
                    vals['prod_instance_id'] = prod_instance.id
            if not vals.get('log_database'):
                log_db = self._get_default_component_by_type('redis')
                if log_db:
                    vals['log_database'] = log_db.id
            if not vals.get('time_series_database'):
                ts_db = self._get_default_component_by_type('iotdb')
                if ts_db:
                    vals['time_series_database'] = ts_db.id
        records = super(DataModel, self).create(vals_list)
        for record in records.filtered(lambda s: s.protocol == "mqtt"):
            record._ensure_mqtt_setup()
        for record in records.filtered(lambda s: s.protocol == "webdav" and s.state == "effective"):
            record._ensure_webdav_setup()
        return records

    def write(self, vals):
        if 'data_structure' in vals:
            vals['data_structure'] = self._format_json_text(vals.get('data_structure'))
        res = super(DataModel, self).write(vals)
        if any(f in vals for f in ['data_asset_id', 'data_direction', 'name']):
            for record in self.filtered(lambda s: s.protocol == "mqtt"):
                record._ensure_mqtt_setup()
        if any(f in vals for f in ['state', 'protocol', 'data_asset_id', 'data_asset_ids']):
            for record in self.filtered(lambda s: s.protocol == "webdav" and s.state == "effective"):
                record._ensure_webdav_setup()
        return res

    def copy(self, default=None):
        self.ensure_one()
        default = dict(default or {})
        if not default.get("name"):
            original_name = (self.name or "").strip() or "copy"
            base_name = re.sub(r"\(\d+\)$", "", original_name).strip() or "copy"
            partner_id = self.partner_id.id or self.data_asset_id.partner_id.id or False
            data_direction = self.data_direction
            i = 1
            while True:
                candidate = f"{base_name}({i})"
                exists = self.search_count([
                    ("name", "=", candidate),
                    ("partner_id", "=", partner_id),
                    ("data_direction", "=", data_direction),
                ]) > 0
                if not exists:
                    default["name"] = candidate
                    break
                i += 1
        return super(DataModel, self).copy(default)

    def _ensure_mqtt_setup(self):
        """
        After saving, create the MQTT topic based on the configured rules.
        1. Find the first online broker.
        2. Check or create MQTT users for the requester and provider.
        3. Create or update the MQTT topic.
        4. Post connection parameters to the chatter.
        """
        self.ensure_one()
        # 1. Find the first online broker
        broker = self.env['crose.component'].search([('component_type', '=', 'mqtt'), ('status', '=', 'online')], limit=1)
        if not broker:
            return

        # 2. Check and create users
        def ensure_user(partner):
            if not partner or partner.mqtt_username:
                return

            username = "".join(filter(str.isalnum, partner.name or ""))
            if not username:
                username = f"user_{partner.id}"

            existing_local = self.env['crose.component.account'].search([
                ('component_id', '=', broker.id),
                ('username', '=', username),
            ], limit=1)

            if existing_local:
                partner.sudo().write({'mqtt_username': username})
                return

            try:
                broker.create_gmqtt_user(username, partner.id)
                partner.sudo().write({'mqtt_username': username})
            except Exception as e:
                self.message_post(body=_("Failed to create MQTT user for %(partner)s: %(error)s", partner=partner.name, error=str(e)))

        ensure_user(self.partner_id)

        # 3. Create or update the topic
        direction = 'provider' if self.data_direction == 'pub' else 'requester'
        partner_name = self.partner_id.name or ''
        topic_name = f"/{partner_name}/{direction}/{self.name}"
        topic_vals = {
            'name': topic_name,
            'broker_id': broker.id,
            'partner_ids': [(6, 0, [self.partner_id.id])] if self.partner_id else [(6, 0, [])]
        }
        if self.mqtt_topic_id:
            self.mqtt_topic_id.sudo().write(topic_vals)
        else:
            new_topic = self.env['fts.mqtt.topic'].sudo().create(topic_vals)
            self.sudo().write({'mqtt_topic_id': new_topic.id})

        # 4. Post connection parameters to the chatter
        msg = f"<b>{_('MQTT connection parameters have been generated:')}</b><br/><br/>" \
              f"{_('Server IP')}: {broker.host}<br/>" \
              f"{_('TCP Port')}: {broker.port}<br/>" \
              f"{_('Protocol')}: MQTT v3.1.1 / v5<br/>" \
              f"{_('Current Topic')}: {topic_name}<br/><br/>" \
              f"{_('Please provide the above parameters to the device or client for configuration.')}"
        self.message_post(body=msg)

    def _ensure_webdav_setup(self):
        """
        Ensure WebDAV users/directories are provisioned for selected assets
        once the data model becomes effective.
        """
        self.ensure_one()
        webdav_comp = self.env['crose.component'].search(
            [('component_type', '=', 'webdav'), ('status', '=', 'online')],
            limit=1,
        )
        if not webdav_comp:
            webdav_comp = self.env['crose.component'].search([('component_type', '=', 'webdav')], limit=1)
        if not webdav_comp:
            return

        account = webdav_comp.account_ids.filtered(lambda a: a.is_primary)[:1] or webdav_comp.account_ids[:1]
        token = account._get_plain_password() if account else ""
        if not token:
            raise ValidationError(
                _("WebDAV management token is missing. Please configure it in the WebDAV component account password.")
            )

        metadata = webdav_comp._metadata_dict()
        api_prefix = str(metadata.get("management_prefix") or metadata.get("api_prefix") or "/api").strip() or "/api"
        if not api_prefix.startswith("/"):
            api_prefix = f"/{api_prefix}"
        base_url = webdav_comp._build_component_base_url()
        users_endpoint = f"{base_url}{api_prefix}/users"

        assets = self.data_asset_ids or self.data_asset_id
        if not assets:
            return

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        provisioned = []
        for asset in assets:
            nick_name = (asset.nick_name or "").strip()
            if not nick_name:
                continue
            if "/" in nick_name or "\\" in nick_name:
                raise ValidationError(
                    _("Data Asset nick_name '%(name)s' cannot contain '/' or '\\\\' for WebDAV provisioning.", name=nick_name)
                )
            model_code = (self.name or "").strip()
            if "/" in model_code or "\\" in model_code:
                raise ValidationError(
                    _("Data Model code '%(code)s' cannot contain '/' or '\\\\' for WebDAV provisioning.", code=model_code)
                )
            if not model_code:
                raise ValidationError(_("Data Model code is empty, cannot provision WebDAV directories."))

            model_asset_dir = f"{model_code}{nick_name}"

            bootstrap_payload = {
                "username": nick_name,
                "password": nick_name,
                "directory": "/data",
                "permissions": "CRUD",
            }
            final_payload = {
                "directory": "/data/%s" % model_asset_dir,
                "permissions": "CRUD",
            }
            
            try:
                # Step 1: ensure user exists and can create under /data.
                response = requests.post(users_endpoint, headers=headers, json=bootstrap_payload, timeout=10)
                response.raise_for_status()

                # Step 2: create model-asset directory and business sub-directories.
                to_create = [
                    f"/{model_asset_dir}",
                    f"/{model_asset_dir}/上传",
                    f"/{model_asset_dir}/成功",
                    f"/{model_asset_dir}/失败",
                ]
                for rel_path in to_create:
                    mkcol_url = f"{base_url}{quote(rel_path)}"
                    mkcol_resp = requests.request(
                        "MKCOL",
                        mkcol_url,
                        auth=(nick_name, nick_name),
                        timeout=10,
                    )
                    if mkcol_resp.status_code not in (201, 405):
                        raise ValidationError(
                            _(
                                "WebDAV MKCOL failed for %(asset)s path %(path)s, status=%(status)s, body=%(body)s",
                                asset=asset.display_name,
                                path=rel_path,
                                status=mkcol_resp.status_code,
                                body=(mkcol_resp.text or "")[:300],
                            )
                        )

                # Step 3: switch user's root directory to its dedicated path.
                patch_resp = requests.patch(
                    f"{users_endpoint}/{quote(nick_name)}",
                    headers=headers,
                    json=final_payload,
                    timeout=10,
                )
                patch_resp.raise_for_status()
                provisioned.append(f"{nick_name} -> /data/{model_asset_dir}")
            except Exception as error:
                raise ValidationError(
                    _("Failed to provision WebDAV account for %(asset)s: %(error)s", asset=asset.display_name, error=str(error))
                )

        if provisioned:
            self.message_post(
                body=_(
                    "WebDAV users/directories are ready: %(users)s. "
                    "Rule: username=password=asset nick_name, and create "
                    "/data/<data_model_code><nick_name>/{上传,成功,失败}.",
                    users=", ".join(provisioned),
                )
            )

    @api.onchange('nr_flow_ids')
    def _onchange_nr_flow_ids(self):
        """When the selected flows change, automatically copy flow parameters into app_param_ids."""
        if not self.nr_flow_ids:
            return

        existing_names = set()
        for param in self.app_param_ids:
            if param.name:
                existing_names.add(param.name)

        new_params_vals = []
        for flow in self.nr_flow_ids:
            for param in flow.param_ids:
                if param.name not in existing_names:
                    new_params_vals.append((0, 0, {
                        'name': param.name,
                        'value': param.value,
                        'type': param.type,
                        'description': param.description,
                        'flow_id': flow.id,
                    }))
                    existing_names.add(param.name)

        if new_params_vals:
            self.update({'app_param_ids': new_params_vals})

    def action_test_query(self):
        """
            Users often do not know how many rows match the selected conditions.
            Spreadsheet rendering is limited in size (<10000 rows), so this
            helper returns the row count before opening the spreadsheet.
        """
        try:
            if self.query_type == 'data':
                start_ts, end_ts, count_sql, result_sql = self._build_iotdb_sql()
                result_df = self._execute_iotdb_query(result_sql)
                count = int(len(result_df)) if result_df is not None else 0
            else:
                result_df = self._execute_redis_query_dataframe()
                count = int(len(result_df)) if result_df is not None else 0

            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Query Complete"),
                    "message": _("There are %(count)s rows in the selected time range.", count=count),
                    "type": "success",
                    "sticky": False,
                },
            }

        except Exception as e:
            raise ValidationError(_("Query failed: %(error)s", error=str(e)))

    def action_open_spreadsheet(self):
        try:
            if self.query_type == 'data':
                _, _, _, result_sql = self._build_iotdb_sql()
                result_df = self._execute_iotdb_query(result_sql)
                result_df = self._prepare_iotdb_dataframe(result_df)
            else:
                result_df = self._execute_redis_query_dataframe()
            self.spreadsheet_binary_data = self._build_spreadsheet_binary_data(result_df)
        except Exception as e:
            raise ValidationError(_("Failed to generate spreadsheet: %(error)s", error=str(e)))
        return {
            "type": "ir.actions.client",
            "tag": "feitas_iot.action_open_spreadsheet",
            "params": {
                "resId": self.id,
            },
        }

    def action_start_demo(self):
        """
            Demo mode: do not copy flows. Instead, directly trigger the
            selected template flows on their associated runtime instance via
            Node-RED's /inject/:id endpoint.

            Node-RED admin API for manual node trigger:
                POST /inject/:id  (needsPermission("inject.write"))
                -> calls node.receive() and returns 200
        """
        self.ensure_one()
        if not self.nr_instance_id:
            raise ValidationError(_("Please select a runtime instance before starting."))
        flows = self.nr_flow_ids
        if not flows:
            raise ValidationError(_("Please select at least one flow template in Applications."))

        triggered = []
        failed = []
        for flow in flows:
            if not flow.nr_id:
                failed.append(f"{flow.name} (no Flow ID)")
                continue
            node_ids = self._get_inject_node_ids(flow)
            if not node_ids:
                failed.append(f"{flow.name} (no inject node found)")
                continue
            for node_id in node_ids:
                ok = self.nr_instance_id._nr_post_json(
                    f"/inject/{node_id}", {}, timeout=10
                )
                if ok is not False:
                    triggered.append(flow.name)
                else:
                    failed.append(f"{flow.name}/node:{node_id}")

        msg = _("Triggered: %(ok)s.", ok=", ".join(triggered) if triggered else "none")
        if failed:
            msg += " " + _("Failed: %(fail)s.", fail=", ".join(failed))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Start Complete"),
                "message": msg,
                "type": "warning" if failed else "success",
                "sticky": False,
            },
        }

    def _get_inject_node_ids(self, flow):
        """
            Parse flow content JSON and return all node ids that have
            type == 'inject'. Sub-flows (tabs) are excluded.
        """
        if not flow.content:
            _logger.warning("Flow %s (%s) has empty content", flow.name, flow.id)
            return []
        try:
            data = json.loads(flow.content)
        except Exception as e:
            _logger.warning("Flow %s content is not valid JSON: %s", flow.name, e)
            return []
        nodes = data
        if isinstance(data, dict):
            nodes = data.get("nodes", data.get("flows", data.get("array", [])))
        if not isinstance(nodes, list):
            _logger.warning("Flow %s parsed to non-list type %s, content starts: %.200s",
                            flow.name, type(nodes).__name__, flow.content[:200])
            return []
        flow_nr_id = flow.nr_id
        result = [n["id"] for n in nodes if isinstance(n, dict) and n.get("type") == "inject" and n.get("z") in (None, flow_nr_id)]
        if not result:
            types = set((n.get("type") for n in nodes if isinstance(n, dict)))
            _logger.warning("No inject nodes found in flow %s. Available types: %s", flow.name, types)
        return result


    def action_generate_params(self):
        """
            Generate parameters for the selected flows based on this data model record.
            Resolves {{record.field}} placeholders and populates app_param_ids.
        """
        self.ensure_one()
        if not self.nr_flow_ids:
            raise UserError(_("Please select at least one flow template in Applications."))

        Param = self.env["fts.nr.flow.param"]
        existing_names = {}
        for param in self.app_param_ids:
            if param.name:
                existing_names[param.name] = param

        new_params_vals = []
        for flow in self.nr_flow_ids:
            # Use the staging instance for parameter preview
            instance = self.nr_instance_id
            if not instance:
                instance = self.env["fts.nr.instance"].browse()
            # Resolve params against data_model record
            preview = instance._nr_preview_flow_params(flow, self)
            for item in preview:
                if item["name"] in existing_names:
                    # Update existing param
                    existing = existing_names[item["name"]]
                    existing.write({
                        "value": str(item["resolved_value"]) if item["resolved_value"] is not None else "",
                        "type": item["type"],
                        "flow_id": flow.id,
                    })
                else:
                    new_params_vals.append((0, 0, {
                        "name": item["name"],
                        "value": str(item["resolved_value"]) if item["resolved_value"] is not None else "",
                        "type": item["type"],
                        "description": "",
                        "flow_id": flow.id,
                    }))
                    existing_names[item["name"]] = True

        if new_params_vals:
            self.write({"app_param_ids": new_params_vals})

        param_count = len(self.app_param_ids)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Parameters Generated"),
                "message": _("%(count)s parameter(s) generated from selected flows.", count=param_count),
                "type": "success",
                "sticky": False,
            },
        }

    def action_deploy_to_stage(self):
        """Deploy selected flows to the staging instance."""
        self.ensure_one()
        if not self.nr_instance_id:
            raise UserError(_("Please select a Stage Instance before deploying."))
        if not self.nr_flow_ids:
            raise UserError(_("Please select at least one flow template in Applications."))

        result = self.nr_instance_id.action_deploy_flows(
            flow_ids=self.nr_flow_ids,
            record=self,
        )
        return result

    def action_deploy_to_prod(self):
        """Deploy selected flows to the prod instance."""
        self.ensure_one()
        if not self.prod_instance_id:
            raise UserError(_("Please select a Prod Instance before deploying."))
        if not self.nr_flow_ids:
            raise UserError(_("Please select at least one flow template in Applications."))

        result = self.prod_instance_id.action_deploy_flows(
            flow_ids=self.nr_flow_ids,
            record=self,
        )
        return result

    def action_send_flow(self):
        """
            场景；data modeling的data assets发生变化，需要向node-red发送特定节点的最新数据。包括：
            1、name=data assets的function节点为特殊节点，msg.payload数据来自于app_param_ids，格式如下：
            {
                "15" : { 
                    redisKey: "device:15:files",
                    mqttTopic: "iot/device15"
                },
                "14" : {
                    redisKey: "device:15:files",
                    topic: "iot/device15"
                }
            }
            app_param_ids的value字段可以写如下可格式化字符串：
                {{name}}   name字段的值
                {{provider_id.name}}   provider_id记录的name字段的值
                {{data_asset_ids.name}}    遍历data_asset_ids时，对应data asset记录的name字段的值
            2、待补充
        """
        self.ensure_one()
        if not self.nr_instance_id:
            raise ValidationError(_("Please select a runtime instance before starting."))
        if not self.nr_flow_ids:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Update Complete"),
                    "message": _("No flows are linked to this data model."),
                    "type": "warning",
                    "sticky": False,
                },
            }

        placeholder_pattern = re.compile(r"\{\{\s*([a-zA-Z_][\w\.]*)\s*\}\}")

        def _resolve_path(record, path):
            current = record
            for part in str(path).split("."):
                if not part:
                    return ""
                if isinstance(current, models.BaseModel):
                    if not current:
                        return ""
                    current = current[part] if part in current._fields else getattr(current, part, None)
                else:
                    current = getattr(current, part, None) if hasattr(current, part) else None
                if current is None:
                    return ""
            if isinstance(current, models.BaseModel):
                if not current:
                    return ""
                if len(current) > 1:
                    return ", ".join(current.mapped("display_name"))
                if "name" in current._fields:
                    return current.name or ""
                return current.id
            return current

        def _render_template(raw_value, asset):
            if not isinstance(raw_value, str):
                return raw_value

            def _replace(match):
                expr = match.group(1)
                if expr == "data_asset_ids":
                    resolved = asset.id if asset else ""
                elif expr.startswith("data_asset_ids."):
                    rel_path = expr.split(".", 1)[1]
                    resolved = _resolve_path(asset, rel_path) if asset else ""
                else:
                    resolved = _resolve_path(self, expr)
                if isinstance(resolved, (dict, list)):
                    return json.dumps(resolved, ensure_ascii=False)
                return "" if resolved is None else str(resolved)

            return placeholder_pattern.sub(_replace, raw_value)

        def _convert_param_value(param, asset):
            rendered = _render_template(param.value or "", asset)
            value_type = (param.type or "str").lower()

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

        def _build_data_assets_payload():
            assets = self.data_asset_ids or self.data_asset_id
            payload = {}
            for asset in assets:
                item = {}
                for param in self.app_param_ids:
                    if not param.name:
                        continue
                    item[param.name] = _convert_param_value(param, asset)
                payload[str(asset.id)] = item
            return payload

        def _is_data_assets_function_node(node):
            if not isinstance(node, dict):
                return False
            if node.get("type") != "function":
                return False
            node_name = (node.get("name") or node.get("label") or "").strip().lower()
            return node_name == "data assets"

        def _nr_put_json(path, body, timeout=30):
            last_error = None
            for base_url in self.nr_instance_id._nr_candidate_base_urls():
                url = f"{base_url}{path}"
                try:
                    headers = self.nr_instance_id._nr_headers_for(base_url)
                    response = requests.put(url, headers=headers, json=body, timeout=timeout)
                    response.raise_for_status()
                    try:
                        return response.json()
                    except Exception:
                        return {}
                except Exception as e:
                    last_error = e
            raise ValidationError(_("Failed to call Node-RED API: %(error)s", error=str(last_error)))

        payload = _build_data_assets_payload()
        func_value = "msg.payload = %s;\nreturn msg;" % json.dumps(payload, ensure_ascii=False, indent=2)

        updated_flow_count = 0
        updated_node_count = 0
        not_found_flows = []
        failed_flows = []

        for flow in self.nr_flow_ids:
            if not flow.nr_id:
                failed_flows.append(_("%(flow)s (missing Flow ID)", flow=flow.display_name))
                continue
            try:
                flow_detail = self.nr_instance_id.api_sync_flow_by_id(flow.nr_id)
                nodes = flow_detail.get("nodes") if isinstance(flow_detail, dict) else None
                if not isinstance(nodes, list):
                    failed_flows.append(_("%(flow)s (invalid flow payload)", flow=flow.display_name))
                    continue

                matched = 0
                for node in nodes:
                    if _is_data_assets_function_node(node):
                        node["func"] = func_value
                        matched += 1

                if matched <= 0:
                    not_found_flows.append(flow.display_name)
                    continue

                flow_detail["nodes"] = nodes
                _nr_put_json(f"/flow/{flow.nr_id}", flow_detail, timeout=30)
                flow.sudo().write({"content": flow_detail})
                updated_flow_count += 1
                updated_node_count += matched
            except Exception as e:
                failed_flows.append(_("%(flow)s (%(error)s)", flow=flow.display_name, error=str(e)))

        message_parts = [
            _("Updated %(flow_count)s flows, %(node_count)s nodes.", flow_count=updated_flow_count, node_count=updated_node_count)
        ]
        if not_found_flows:
            message_parts.append(
                _("No matched node in: %(flows)s", flows=", ".join(not_found_flows[:10]))
            )
        if failed_flows:
            message_parts.append(
                _("Failed: %(flows)s", flows=", ".join(failed_flows[:10]))
            )

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Update Complete"),
                "message": "\n".join(message_parts),
                "type": "success" if not failed_flows and updated_flow_count > 0 else "warning",
                "sticky": False,
            },
        }

    def action_open_flow(self):
        self.ensure_one()
        flows = self.nr_flow_ids
        if not flows:
            raise ValidationError(_("No flows are linked to this data model."))
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

    def _get_writable_record_name_field(self):
        return "name"

    def _build_iotdb_sql(self):
        self.ensure_one()
        if not self.query_start_time:
            raise ValidationError(_("Please select a start time."))
        if not self.query_end_time:
            self.query_end_time = fields.Datetime.now()
        if not self.query_interval or self.query_interval <= 0:
            raise ValidationError(_("Please enter a valid interval in seconds."))

        start_dt = fields.Datetime.to_datetime(self.query_start_time)
        end_dt = fields.Datetime.to_datetime(self.query_end_time)
        start_ts = int(start_dt.timestamp() * 1000)
        end_ts = int(end_dt.timestamp() * 1000)

        where_clause = f"time >= {start_ts} AND time <= {end_ts}"
        mqtt_topic_param = self.app_param_ids.filtered(lambda p: (p.name or "").strip() == "iotDBDevice")[:1]

        def _resolve_path(record, path):
            current = record
            for part in str(path).split("."):
                if not part:
                    return ""
                if isinstance(current, models.BaseModel):
                    if not current:
                        return ""
                    current = getattr(current, part, None)
                else:
                    current = getattr(current, part, None) if hasattr(current, part) else None
                if current is None:
                    return ""
            if isinstance(current, models.BaseModel):
                if not current:
                    return ""
                if len(current) > 1:
                    names = current.mapped("display_name")
                    return ", ".join([n for n in names if n])
                if "name" in current._fields:
                    return current.name or ""
                return current.id
            return current

        def _render_topic_template(raw_value, asset):
            if not isinstance(raw_value, str):
                return raw_value
            pattern = re.compile(r"\{\{\s*([a-zA-Z_][\w\.]*)\s*\}\}")

            def _replace(match):
                expr = match.group(1)
                if expr == "data_asset_ids":
                    return str(asset.id) if asset else ""
                if expr.startswith("data_asset_ids."):
                    rel = expr.split(".", 1)[1]
                    return str(_resolve_path(asset, rel) or "") if asset else ""
                resolved = _resolve_path(self, expr)
                if isinstance(resolved, (dict, list)):
                    return json.dumps(resolved, ensure_ascii=False)
                return "" if resolved is None else str(resolved)

            return pattern.sub(_replace, raw_value)

        def _mqtt_to_iotdb_path(topic):
            text = (topic or "").strip()
            if not text:
                return ""
            converted = text.strip("/").replace("/", ".").strip(".")
            if converted.startswith("root."):
                converted = converted[5:]
            elif converted == "root":
                converted = ""
            segments = [seg for seg in converted.split(".") if seg]
            if not segments:
                return ""

            def _normalize_segment(seg):
                raw = str(seg).strip()
                if not raw:
                    return ""
                if raw in ("*", "**"):
                    return raw
                if raw.startswith("`") and raw.endswith("`") and len(raw) >= 2:
                    return raw
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw):
                    return raw
                return "`%s`" % raw.replace("`", "``")

            normalized = [_normalize_segment(seg) for seg in segments]
            normalized = [seg for seg in normalized if seg]
            if not normalized:
                return ""
            return "root.%s" % ".".join(normalized)

        topics = []
        assets = self.query_data_asset_ids or self.data_asset_ids or self.data_asset_id
        if mqtt_topic_param and assets:
            for asset in assets:
                mqtt_topic = str(_render_topic_template(mqtt_topic_param.value or "", asset)).strip()
                iotdb_topic = _mqtt_to_iotdb_path(mqtt_topic)
                if iotdb_topic:
                    topics.append(iotdb_topic)

        if not topics and self.iotdb_topic:
            topics.append(self.iotdb_topic)
        topics = list(dict.fromkeys([t for t in topics if t]))
        if not topics:
            raise ValidationError(_("Please configure iotDBDevice in app_param_ids or IoTDB Topic before querying."))

        result_sql = [f"SELECT * FROM {topic} WHERE {where_clause} LIMIT 10000" for topic in topics]
        count_sql = [f"SELECT COUNT(*) FROM {topic} WHERE {where_clause}" for topic in topics]
        if len(result_sql) == 1:
            result_sql = result_sql[0]
            count_sql = count_sql[0]
        return start_ts, end_ts, count_sql, result_sql

    def _get_iotdb_connection_params(self):
        self.ensure_one()
        iotdb = self.time_series_database
        if iotdb and iotdb.component_type != "iotdb":
            raise ValidationError(_("Time-Series Storage must be an IoTDB system component."))
        if not iotdb:
            iotdb = self.env["crose.component"].search([("component_type", "=", "iotdb"), ("status", "=", "online")], limit=1)
        if not iotdb:
            iotdb = self.env["crose.component"].search([("component_type", "=", "iotdb")], limit=1)
        if not iotdb:
            raise ValidationError(_("No online IoTDB component was found. Please create and activate one in System Components first."))
        host = iotdb.host or "iotdb"
        port = iotdb.port or 6667
        account = iotdb.account_ids.filtered(lambda x: (x.username or "").strip() == "crose_app")[:1]
        if not account:
            raise ValidationError(_("IoTDB account 'crose_app' was not found on the component."))
        username = account.username
        password = account._get_plain_password()
        if not password:
            raise ValidationError(_("IoTDB account 'crose_app' has no decryptable password. Please set it again."))
        return host, str(port), username, password

    def _execute_iotdb_query(self, sql):
        sql_list = []
        if isinstance(sql, str):
            sql_list = [sql]
        elif isinstance(sql, (list, tuple, set)):
            sql_list = [s for s in sql if isinstance(s, str) and s.strip()]
        if not sql_list:
            raise ValidationError(_("The query statement must be a string or a list of strings."))
        iotdb_ip, iotdb_port, iotdb_username, iotdb_password = self._get_iotdb_connection_params()
        from iotdb.Session import Session
        import pandas as pd

        session = Session(iotdb_ip, iotdb_port, iotdb_username, iotdb_password)
        session.open(False)
        try:
            frames = []
            for one_sql in sql_list:
                result = session.execute_query_statement(one_sql)
                frames.append(result.todf())
            if len(frames) == 1:
                return frames[0]
            if not frames:
                return pd.DataFrame()
            return pd.concat(frames, ignore_index=True, sort=False)
        finally:
            try:
                session.close()
            except Exception:
                pass

    def _prepare_iotdb_dataframe(self, dataframe):
        if dataframe is None or getattr(dataframe, "empty", False):
            return dataframe

        rename_dict = {}
        with contextlib.suppress(Exception):
            parsed = json.loads(self.data_structure or "{}")
            if isinstance(parsed, dict):
                rename_dict = {str(k): str(v) for k, v in parsed.items()}

        def _friendly_name(column_name):
            col = str(column_name)
            if col in rename_dict:
                return rename_dict[col]
            last = col.split(".")[-1]
            if last in rename_dict:
                return rename_dict[last]
            if col.lower() == "time":
                for key in ("Time", "time", "TIME"):
                    if key in rename_dict:
                        return rename_dict[key]
            return col

        def _format_time_value(value):
            if value is None:
                return ""
            if isinstance(value, float) and math.isnan(value):
                return ""
            ts = None
            if isinstance(value, (int, float)):
                ts = float(value)
            elif isinstance(value, str):
                text = value.strip()
                if text.isdigit():
                    ts = float(text)
                else:
                    return value
            else:
                return value
            if ts is None:
                return value
            if ts > 1e12:
                dt = datetime.fromtimestamp(ts / 1000.0)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            if ts > 1e9:
                dt = datetime.fromtimestamp(ts)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            return value

        columns = list(dataframe.columns)
        for column in columns:
            if str(column).lower() == "time":
                dataframe[column] = dataframe[column].map(_format_time_value)

        friendly_columns = [_friendly_name(col) for col in columns]
        dataframe.columns = friendly_columns

        # Merge same-name columns into one column by taking the first non-null
        # value row-wise. This prevents suffixes like _2 for multi-asset queries.
        merged_data = {}
        for name in friendly_columns:
            if name in merged_data:
                continue
            same_name_cols = dataframe.loc[:, dataframe.columns == name]
            if same_name_cols.shape[1] == 1:
                merged_data[name] = same_name_cols.iloc[:, 0]
            else:
                merged_data[name] = same_name_cols.bfill(axis=1).iloc[:, 0]
        dataframe = dataframe.__class__(merged_data)
        return dataframe

    def _get_redis_connection_params(self):
        self.ensure_one()
        redis_comp = self.log_database
        if redis_comp and redis_comp.component_type != "redis":
            raise ValidationError(_("Log Storage must be a Redis system component."))
        if not redis_comp:
            redis_comp = self.env["crose.component"].search(
                [("component_type", "=", "redis"), ("status", "=", "online")], limit=1
            )
        if not redis_comp:
            redis_comp = self.env["crose.component"].search(
                [("component_type", "=", "redis")], limit=1
            )
        if not redis_comp:
            raise ValidationError(_("No Redis component was found. Please create and activate one in System Components first."))
        host = redis_comp.host or "localhost"
        port = redis_comp.port or 6379
        metadata = {}
        if redis_comp.metadata:
            with contextlib.suppress(Exception):
                metadata = json.loads(redis_comp.metadata)
        username = metadata.get("username")
        password = metadata.get("password", None)
        db = metadata.get("db", 0)
        with contextlib.suppress(Exception):
            db = int(db)
        return host, port, username, password, db

    def _execute_redis_query_dataframe(self):
        import pandas as pd

        self.ensure_one()

        def _reserve_column(df, column_name):
            if column_name not in df.columns:
                return df
            idx = 2
            while f"{column_name}_data_{idx}" in df.columns:
                idx += 1
            return df.rename(columns={column_name: f"{column_name}_data_{idx}"})

        def _resolve_path(record, path):
            current = record
            for part in str(path).split("."):
                if not part:
                    return ""
                if isinstance(current, models.BaseModel):
                    if not current:
                        return ""
                    current = getattr(current, part, None)
                else:
                    current = getattr(current, part, None) if hasattr(current, part) else None
                if current is None:
                    return ""
            if isinstance(current, models.BaseModel):
                if not current:
                    return ""
                if len(current) > 1:
                    names = current.mapped("display_name")
                    return ", ".join([n for n in names if n])
                if "name" in current._fields:
                    return current.name or ""
                return current.id
            return current

        def _render_redis_key(raw_value, asset, assets):
            if not isinstance(raw_value, str):
                return raw_value
            pattern = re.compile(r"\{\{\s*([a-zA-Z_][\w\.]*)\s*\}\}")

            def _replace(match):
                expr = match.group(1)
                if expr == "data_asset_ids":
                    if asset:
                        return str(asset.id)
                    return ",".join([str(i) for i in assets.ids]) if assets else ""
                if expr.startswith("data_asset_ids."):
                    rel = expr.split(".", 1)[1]
                    if asset:
                        return str(_resolve_path(asset, rel) or "")
                    values = [str(_resolve_path(one, rel) or "") for one in assets] if assets else []
                    return ",".join([v for v in values if v])
                resolved = _resolve_path(self, expr)
                if isinstance(resolved, (dict, list)):
                    return json.dumps(resolved, ensure_ascii=False)
                return "" if resolved is None else str(resolved)

            return pattern.sub(_replace, raw_value)

        def _read_key(client, key_name, db):
            key_type = client.type(key_name)
            if isinstance(key_type, bytes):
                key_type = key_type.decode(errors="ignore")
            if key_type in (None, "none"):
                return None
            if key_type == "string":
                return client.get(key_name)
            if key_type == "set":
                return list(client.smembers(key_name))
            if key_type == "hash":
                return client.hgetall(key_name)
            if key_type == "list":
                return client.lrange(key_name, 0, -1)
            if key_type == "zset":
                return client.zrange(key_name, 0, -1, withscores=True)
            if key_type == "stream":
                return client.xrange(key_name, count=100)
            raise ValidationError(
                _(
                    "Redis key %(key)s in db %(db)s has unsupported type %(type)s.",
                    key=key_name,
                    db=db,
                    type=key_type,
                )
            )

        host, port, username, password, db = self._get_redis_connection_params()
        redis_key_param = self.app_param_ids.filtered(lambda p: (p.name or "").strip() == "redisKey")[:1]
        if not redis_key_param:
            raise ValidationError(_("Please configure app_param_ids with name 'redisKey'."))

        assets = self.query_data_asset_ids or self.data_asset_ids or self.data_asset_id
        asset_list = list(assets) if assets else [None]

        if password:
            client = redis.Redis(host=host, port=port, username=username, password=password, db=db, decode_responses=True)
        else:
            client = redis.Redis(host=host, port=port, username=username, db=db, decode_responses=True)

        frames = []
        for asset in asset_list:
            key_name = str(_render_redis_key(redis_key_param.value or "", asset, assets)).strip()
            if not key_name:
                if asset:
                    raise ValidationError(
                        _("The computed redisKey is empty for asset %(asset)s. Please check app_param_ids value.", asset=asset.display_name)
                    )
                raise ValidationError(_("The computed redisKey is empty. Please check app_param_ids value."))

            redis_value = _read_key(client, key_name, db)
            df = self._build_redis_dataframe(redis_value)

            df = _reserve_column(df, "query_asset_id")
            df = _reserve_column(df, "query_asset")
            df = _reserve_column(df, "query_redis_key")

            df.insert(0, "query_asset_id", asset.id if asset else "")
            df.insert(1, "query_asset", asset.display_name if asset else "")
            df.insert(2, "query_redis_key", key_name)

            frames.append(df)

        if not frames:
            return pd.DataFrame()
        if len(frames) == 1:
            return frames[0]
        return pd.concat(frames, ignore_index=True, sort=False)

    def _execute_redis_query(self):
        self.ensure_one()

        def _resolve_path(record, path):
            current = record
            for part in str(path).split("."):
                if not part:
                    return ""
                if isinstance(current, models.BaseModel):
                    if not current:
                        return ""
                    current = getattr(current, part, None)
                else:
                    current = getattr(current, part, None) if hasattr(current, part) else None
                if current is None:
                    return ""
            if isinstance(current, models.BaseModel):
                if not current:
                    return ""
                if len(current) > 1:
                    names = current.mapped("display_name")
                    return ", ".join([n for n in names if n])
                if "name" in current._fields:
                    return current.name or ""
                return current.id
            return current

        def _render_redis_key(raw_value):
            if not isinstance(raw_value, str):
                return raw_value
            pattern = re.compile(r"\{\{\s*([a-zA-Z_][\w\.]*)\s*\}\}")
            assets = self.query_data_asset_ids or self.data_asset_ids or self.data_asset_id

            def _replace(match):
                expr = match.group(1)
                if expr == "data_asset_ids":
                    return ",".join([str(i) for i in assets.ids]) if assets else ""
                if expr.startswith("data_asset_ids."):
                    rel = expr.split(".", 1)[1]
                    values = [str(_resolve_path(asset, rel) or "") for asset in assets] if assets else []
                    return ",".join([v for v in values if v])
                resolved = _resolve_path(self, expr)
                if isinstance(resolved, (dict, list)):
                    return json.dumps(resolved, ensure_ascii=False)
                return "" if resolved is None else str(resolved)

            return pattern.sub(_replace, raw_value)

        host, port, username, password, db = self._get_redis_connection_params()
        redis_key_param = self.app_param_ids.filtered(lambda p: (p.name or "").strip() == "redisKey")[:1]
        if not redis_key_param:
            raise ValidationError(_("Please configure app_param_ids with name 'redisKey'."))
        key_name = str(_render_redis_key(redis_key_param.value or "")).strip()
        if not key_name:
            raise ValidationError(_("The computed redisKey is empty. Please check app_param_ids value."))
        if password:
            client = redis.Redis(host=host, port=port, username=username, password=password, db=db, decode_responses=True)
        else:
            client = redis.Redis(host=host, port=port, username=username, db=db, decode_responses=True)

        key_type = client.type(key_name)
        if isinstance(key_type, bytes):
            key_type = key_type.decode(errors="ignore")
        if key_type in (None, "none"):
            return None
        if key_type == "string":
            return client.get(key_name)
        if key_type == "set":
            return list(client.smembers(key_name))
        if key_type == "hash":
            return client.hgetall(key_name)
        if key_type == "list":
            return client.lrange(key_name, 0, -1)
        if key_type == "zset":
            return client.zrange(key_name, 0, -1, withscores=True)
        if key_type == "stream":
            return client.xrange(key_name, count=100)
        raise ValidationError(
            _(
                "Redis key %(key)s in db %(db)s has unsupported type %(type)s.",
                key=key_name,
                db=db,
                type=key_type,
            )
        )

    def _build_redis_dataframe(self, redis_value):
        import pandas as pd

        def _parse_item_to_row(item):
            if isinstance(item, dict):
                return item
            if isinstance(item, str):
                with contextlib.suppress(Exception):
                    parsed = json.loads(item)
                    if isinstance(parsed, dict):
                        return parsed
            return {"value": item}

        if redis_value is None:
            return pd.DataFrame()
        if isinstance(redis_value, dict):
            return pd.DataFrame([redis_value])
        if isinstance(redis_value, (list, tuple, set)):
            rows = [_parse_item_to_row(item) for item in redis_value]
            return pd.DataFrame(rows)
        row = _parse_item_to_row(redis_value)
        return pd.DataFrame([row])

    def _build_spreadsheet_binary_data(self, dataframe):
        lang = self.env["res.lang"]._lang_get(self.env.user.lang)
        locale = lang._odoo_lang_to_spreadsheet_locale()
        headers = [str(col) for col in list(dataframe.columns)]
        max_lengths = [len(h) for h in headers]
        cells = {}
        for col_idx, header in enumerate(headers):
            xc = f"{self._column_to_name(col_idx)}1"
            cells[xc] = header

        for row_idx, row in enumerate(dataframe.itertuples(index=False), start=2):
            for col_idx, value in enumerate(row):
                xc = f"{self._column_to_name(col_idx)}{row_idx}"
                text_value = self._to_spreadsheet_text(value)
                cells[xc] = text_value
                if col_idx < len(max_lengths) and len(text_value) > max_lengths[col_idx]:
                    max_lengths[col_idx] = len(text_value)

        cols = {}
        for col_idx, max_len in enumerate(max_lengths):
            width = 24 + (max_len * 7)
            width = max(80, width)
            cols[str(col_idx)] = {"size": int(width)}

        sheet = {
            "id": SPREADSHEET_SHEET_ID,
            "name": "Sheet1",
            "colNumber": max(26, len(headers)),
            "rowNumber": max(100, len(dataframe) + 1),
            "cells": cells,
            "styles": {},
            "formats": {},
            "borders": {},
            "cols": cols,
            "rows": {},
            "merges": [],
            "conditionalFormats": [],
            "dataValidationRules": [],
            "figures": [],
            "tables": [],
            "isVisible": True,
        }

        data = {
            "version": SPREADSHEET_VERSION,
            "sheets": [sheet],
            "styles": {},
            "formats": {},
            "borders": {},
            "settings": {"locale": locale},
            "revisionId": "START_REVISION",
            "uniqueFigureIds": True,
            "pivots": {},
            "pivotNextId": 1,
            "customTableStyles": {},
        }
        return base64.b64encode(json.dumps(data).encode()).decode()

    def _to_spreadsheet_text(self, value):
        if value is None:
            return ""
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, float) and math.isnan(value):
            return ""
        if hasattr(value, "isoformat"):
            return value.isoformat()
        text = str(value)
        return "".join(ch for ch in text if ch >= " " or ch in "\t\n\r")

    def _column_to_name(self, index):
        name = ""
        current = index
        while True:
            current, remainder = divmod(current, 26)
            name = chr(65 + remainder) + name
            if current == 0:
                break
            current -= 1
        return name


class DataApp(models.Model):
    _name = "fts.data.app"
    _description = "Data App"

    name = fields.Char(string="Name", required=True)
    value = fields.Text(string="Value", required=True)
    model_id = fields.Many2one("fts.data.model", string="Data Model", required=True, ondelete="cascade")
    flow_id = fields.Many2one("fts.nr.flow", string="Flow", ondelete="set null")
