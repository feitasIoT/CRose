import base64
import contextlib
import json
import math
import logging


from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


_logger = logging.getLogger(__name__)

SPREADSHEET_VERSION = "18.5.1"
SPREADSHEET_SHEET_ID = "Sheet1"


class DataModel(models.Model):
    _name = 'fts.data.model'
    _description = 'Data Model'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'spreadsheet.mixin']

    name = fields.Char(string=_('Code'), required=True, copy=False, readonly=True, default=lambda self: _('New'))
    partner_id = fields.Many2one('res.partner', string=_('Requester'), required=True)
    provider_id = fields.Many2one('res.partner', string=_('Provider'), required=True)
    protocol = fields.Selection([
        ('mobus-tcp', 'Modbus-TCP'),
        ('mobus-rtu', 'Modbus-RTU'),
        ('mqtt', 'MQTT'),
        ('http', 'HTTP'),
        ('coap', 'CoAP'),
        ('smb', 'SMB2'),
    ], string=_('Protocol'), required=True)
    host = fields.Char(string=_('Host'))
    tcp_port = fields.Integer(string=_('Port'))
    serial_port = fields.Char(string=_('Serial Port'), default="/dev/ttyUSB0")
    tcp_type = fields.Selection([
        ('default', 'Default'),
        ('rtu-buffered', 'RTU Buffered'),
    ], string=_('TCP Type'))
    slave_id = fields.Integer(string=_('Slave ID'))
    smb_share = fields.Char(string=_('Shared Directory'), help=_('SMB shared directory path, for example: /share'))
    smb_username = fields.Char(string=_('Username'))
    smb_password = fields.Char(string=_('Password'))

    query_start_time = fields.Datetime(string=_('Start Time'))
    query_end_time = fields.Datetime(string=_('End Time'))
    query_interval = fields.Integer(string=_('Interval (Seconds)'), default=60)

    @api.onchange('query_start_time')
    def _onchange_query_start_time(self):
        if self.query_start_time and not self.query_end_time:
            self.query_end_time = fields.Datetime.now()
        if self.query_start_time and not self.query_interval:
            self.query_interval = 60

    description = fields.Text(string=_('Description'))
    mqtt_topic_id = fields.Many2one('fts.mqtt.topic', string=_('MQTT Topic'))
    nr_instance_id = fields.Many2one('fts.nr.instance', string=_('Runtime Instance'), help=_('Local instance responsible for data processing'))
    nr_flow_ids = fields.Many2many('fts.nr.flow', 'data_model_nr_flow_rel', string=_('Applications'))
    app_ids = fields.One2many("fts.data.app", "model_id", string=_('Applications'))
    app_param_ids = fields.One2many("fts.nr.flow.param", "model_id", string=_('Application Parameters'))
    log_ids = fields.One2many('fts.data.log', 'model_id', string='Logs')
    address_ids = fields.One2many('fts.data.address', 'model_id', string='Addresses')
    data_structure = fields.Text(string=_('Data Structure'), required=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('approval', 'Approval'),
        ('effective', 'Effective'),
        ('invalid', 'Invalid'),
    ], string=_('Status'), default='draft', required=True)

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
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('fts.data.model') or _('New')
            if 'data_structure' in vals:
                vals['data_structure'] = self._format_json_text(vals.get('data_structure'))
        records = super(DataModel, self).create(vals_list)
        for record in records.filtered(lambda s: s.protocol == "mqtt"):
            record._ensure_mqtt_setup()
        return records

    def write(self, vals):
        if 'data_structure' in vals:
            vals['data_structure'] = self._format_json_text(vals.get('data_structure'))
        res = super(DataModel, self).write(vals)
        if any(f in vals for f in ['partner_id', 'provider_id', 'name']):
            for record in self.filtered(lambda s: s.protocol == "mqtt"):
                record._ensure_mqtt_setup()
        return res

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
            
            existing_local = self.env['fts.mqtt.user'].search([
                ('name', '=', username),
                ('partner_id', '=', partner.id)
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
        ensure_user(self.provider_id)

        # 3. Create or update the topic
        topic_name = f"/{self.partner_id.name}/{self.provider_id.name}/{self.name}"
        topic_vals = {
            'name': topic_name,
            'broker_id': broker.id,
            'partner_ids': [(6, 0, [self.partner_id.id, self.provider_id.id])]
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
            _, _, count_sql, _ = self._build_iotdb_sql()
            count_df = self._execute_iotdb_query(count_sql)
            count = int(count_df.iloc[0, 0]) if len(count_df) > 0 else 0

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
            _, _, _, result_sql = self._build_iotdb_sql()
            result_df = self._execute_iotdb_query(result_sql)
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
        result_sql = f"SELECT ** FROM root.** WHERE {where_clause} LIMIT 10000"
        count_sql = f"SELECT COUNT(*) FROM root.** WHERE {where_clause}"
        return start_ts, end_ts, count_sql, result_sql

    def _get_iotdb_connection_params(self):
        iotdb = self.env["crose.component"].search([("component_type", "=", "iotdb"), ("status", "=", "online")], limit=1)
        if not iotdb:
            iotdb = self.env["crose.component"].search([("component_type", "=", "iotdb")], limit=1)
        if not iotdb:
            raise ValidationError(_("No online IoTDB component was found. Please create and activate one in System Components first."))
        host = iotdb.host or "iotdb"
        port = iotdb.port or 6667
        metadata = {}
        if iotdb.metadata:
            with contextlib.suppress(Exception):
                metadata = json.loads(iotdb.metadata)
        # TODO: password encryption
        username = metadata.get("username", "root")
        password = metadata.get("password", "root")
        return host, str(port), username, password

    def _execute_iotdb_query(self, sql):
        if not isinstance(sql, str):
            raise ValidationError(_("The query statement must be a string."))
        iotdb_ip, iotdb_port, iotdb_username, iotdb_password = self._get_iotdb_connection_params()
        from iotdb.Session import Session

        session = Session(iotdb_ip, iotdb_port, iotdb_username, iotdb_password)
        session.open(False)
        try:
            result = session.execute_query_statement(sql)
            return result.todf()
        finally:
            try:
                session.close()
            except Exception:
                pass

    def _build_spreadsheet_binary_data(self, dataframe):
        lang = self.env["res.lang"]._lang_get(self.env.user.lang)
        locale = lang._odoo_lang_to_spreadsheet_locale()
        headers = [str(col) for col in list(dataframe.columns)]
        cells = {}
        for col_idx, header in enumerate(headers):
            xc = f"{self._column_to_name(col_idx)}1"
            cells[xc] = header

        for row_idx, row in enumerate(dataframe.itertuples(index=False), start=2):
            for col_idx, value in enumerate(row):
                xc = f"{self._column_to_name(col_idx)}{row_idx}"
                cells[xc] = self._to_spreadsheet_text(value)

        sheet = {
            "id": SPREADSHEET_SHEET_ID,
            "name": "Sheet1",
            "colNumber": max(26, len(headers)),
            "rowNumber": max(100, len(dataframe) + 1),
            "cells": cells,
            "styles": {},
            "formats": {},
            "borders": {},
            "cols": {},
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

    name = fields.Char(string=_("Name"), required=True)
    value = fields.Text(string=_("Value"), required=True)
    model_id = fields.Many2one("fts.data.model", string=_("Data Model"), required=True, ondelete="cascade")
    flow_id = fields.Many2one("fts.nr.flow", string=_("Flow"), ondelete="set null")
