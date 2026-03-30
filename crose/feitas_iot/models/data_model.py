import base64
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

    name = fields.Char(string='编号', required=True, copy=False, readonly=True, default=lambda self: _('New'))
    partner_id = fields.Many2one('res.partner', string='需求方', required=True)
    provider_id = fields.Many2one('res.partner', string='提供方', required=True)
    # 协议
    protocol = fields.Selection([
        ('mobus-tcp', 'Modbus-TCP'),
        ('mobus-rtu', 'Modbus-RTU'),
        ('mqtt', 'MQTT'),
        ('http', 'HTTP'),
        ('coap', 'CoAP'),
        ('smb', 'SMB2'),
    ], string='协议', required=True)
    host = fields.Char(string='主机')
    tcp_port = fields.Integer(string='端口')
    # Note: 串口端口号，例如：/dev/ttyUSB0
    serial_port = fields.Char(string='串口', default="/dev/ttyUSB0")
    tcp_type = fields.Selection([
        ('default', '默认'),
        ('rtu-buffered', 'RTU-缓存'),
    ], string='TCP类型')
    slave_id = fields.Integer(string='从站号')
    # Note：智能模式，就是需要人选择的内容非常少，大量使用默认的，如果不满足，则需要到NR中进行配置，我们的系统会提供知识库、大模型分析。
    smb_share = fields.Char(string='共享目录', help='SMB共享目录路径，例如：/share')
    smb_username = fields.Char(string='用户名')
    smb_password = fields.Char(string='密码')

    query_start_time = fields.Datetime(string='开始时间')
    query_end_time = fields.Datetime(string='结束时间')
    query_interval = fields.Integer(string='间隔（秒）', default=60)

    @api.onchange('query_start_time')
    def _onchange_query_start_time(self):
        if self.query_start_time and not self.query_end_time:
            self.query_end_time = fields.Datetime.now()
        if self.query_start_time and not self.query_interval:
            self.query_interval = 60

    description = fields.Text(string='描述')
    # FIXME：MQTT主题应该是根据填写的其他字段的内容自动创建的，虽然是m2o类型，broker如何选择？
    mqtt_topic_id = fields.Many2one('fts.mqtt.topic', string='MQTT主题')
    nr_instance_id = fields.Many2one('fts.nr.instance', string='运行实例', help="承担数据治理的本地实例")
    nr_flow_ids = fields.Many2many('fts.nr.flow', 'data_model_nr_flow_rel', string='应用') # 废弃，修改类型为m2m
    app_ids = fields.One2many("fts.data.app", "model_id", string="应用") # 可能要废弃
    app_param_ids = fields.One2many("fts.nr.flow.param", "model_id", string="应用参数")
    log_ids = fields.One2many('fts.data.log', 'model_id', string='Logs')
    address_ids = fields.One2many('fts.data.address', 'model_id', string='Addresses') # 根据所选的流程生成参数
    data_structure = fields.Text(string='数据结构', required=True)
    state = fields.Selection([
        ('draft', '草稿'),
        ('approval', '审批'),
        ('effective', '生效'),
        ('invalid', '失效'),
    ], string='状态', default='draft', required=True)

    def _format_json_text(self, value):
        if value is None:
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, indent=2)
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except Exception:
                raise ValidationError("数据结构不是有效的JSON")
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        raise ValidationError("数据结构不是有效的JSON")

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
        保存后，系统根据规则创建MQTT Topic
        1. 寻找在线 Broker
        2. 检查/创建需求方和提供方的 MQTT 用户
        3. 创建/更新 MQTT Topic
        4. 发布连接参数到 Chatter
        """
        self.ensure_one()
        # 1. 寻找第一个在线的 Broker
        broker = self.env['crose.component'].search([('component_type', '=', 'mqtt'), ('status', '=', 'online')], limit=1)
        if not broker:
            return

        # 2. 检查并创建用户
        def ensure_user(partner):
            if not partner or partner.mqtt_username:
                return # 已有用户则忽略

            # 只有没有用户名时才尝试处理
            # 使用名称作为基础，去掉非字母数字字符
            username = "".join(filter(str.isalnum, partner.name or ""))
            if not username:
                username = f"user_{partner.id}"
            
            # 检查这个用户名是否已经由于某种原因在 fts.mqtt.user 中存在了
            existing_local = self.env['fts.mqtt.user'].search([
                ('name', '=', username),
                ('partner_id', '=', partner.id)
            ], limit=1)
            
            if existing_local:
                partner.sudo().write({'mqtt_username': username})
                return

            try:
                # 调用 broker 方法创建
                broker.create_gmqtt_user(username, partner.id)
                partner.sudo().write({'mqtt_username': username})
            except Exception as e:
                self.message_post(body=f"为 {partner.name} 创建 MQTT 用户失败: {str(e)}")

        ensure_user(self.partner_id)
        ensure_user(self.provider_id)

        # 3. 创建/更新 Topic
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

        # 4. 发布连接参数到 Chatter
        msg = f"<b>MQTT 连接参数已生成：</b><br/><br/>" \
              f"服务端IP：{broker.host}<br/>" \
              f"TCP端口：{broker.port}<br/>" \
              f"协议：MQTT v3.1.1 / v5<br/>" \
              f"当前 Topic：{topic_name}<br/><br/>" \
              f"请将以上参数提供给设备或客户端进行配置。"
        self.message_post(body=msg)

    @api.onchange('nr_flow_ids')
    def _onchange_nr_flow_ids(self):
        """当选择的流程发生变化时，自动将流程的参数带入到app_param_ids中"""
        if not self.nr_flow_ids:
            return

        # 获取当前已经存在的参数名，避免重复添加
        # 注意：在onchange中，One2many字段可能包含NewId记录
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
                        'flow_id': flow.id, # 记录来源flow
                    }))
                    existing_names.add(param.name)
        
        if new_params_vals:
            self.update({'app_param_ids': new_params_vals})

    def action_test_query(self):
        """
            用户查询数据时往往不知道自己选择的条件会有多少数据，然而spreadsheet能够打开的行数是有限的（<10000）
            所以，提供一个测试方法返回数据条数，以便用户根据条数调整自己的条件。
        """
        try:
            _, _, count_sql, _ = self._build_iotdb_sql()
            count_df = self._execute_iotdb_query(count_sql)
            count = int(count_df.iloc[0, 0]) if len(count_df) > 0 else 0

            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "查询完成",
                    "message": f'在指定时间范围内共有 {count} 条数据',
                    "type": "success",
                    "sticky": False,
                },
            }

        except Exception as e:
            raise ValidationError(f"查询失败: {str(e)}")

    def action_open_spreadsheet(self):
        try:
            _, _, _, result_sql = self._build_iotdb_sql()
            result_df = self._execute_iotdb_query(result_sql)
            self.spreadsheet_binary_data = self._build_spreadsheet_binary_data(result_df)
        except Exception as e:
            raise ValidationError(f"生成电子表格失败: {str(e)}")
        return {
            "type": "ir.actions.client",
            "tag": "feitas_iot.action_open_spreadsheet",
            "params": {
                "resId": self.id,
                "readonly": True,
            },
        }

    def _get_writable_record_name_field(self):
        return "name"

    def _build_iotdb_sql(self):
        self.ensure_one()
        if not self.query_start_time:
            raise ValidationError("请选择开始时间")
        if not self.query_end_time:
            self.query_end_time = fields.Datetime.now()
        if not self.query_interval or self.query_interval <= 0:
            raise ValidationError("请输入有效的间隔时间（秒）")

        start_dt = fields.Datetime.to_datetime(self.query_start_time)
        end_dt = fields.Datetime.to_datetime(self.query_end_time)
        start_ts = int(start_dt.timestamp() * 1000)
        end_ts = int(end_dt.timestamp() * 1000)

        where_clause = f"time >= {start_ts} AND time <= {end_ts}"
        result_sql = f"SELECT ** FROM root.** WHERE {where_clause} LIMIT 10000"
        count_sql = f"SELECT COUNT(*) FROM root.** WHERE {where_clause}"
        return start_ts, end_ts, count_sql, result_sql

    def _get_iotdb_connection_params(self):
        return "192.168.0.104", "6667", "root", "root"

    def _execute_iotdb_query(self, sql):
        if not isinstance(sql, str):
            raise ValidationError("查询语句必须是字符串")
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
        _logger.warning(str(sheet))
        #{'id': 'sheet1', 'name': 'Sheet1', 'colNumber': 26, 'rowNumber': 100, 'cells': {'A1': 'Time', 'B1': 'root.__system.password_history._root.password', 'C1': 'root.__system.password_history._root.oldPassword', 'D1': 'root.factory01.machine01.high', 'E1': 'root.factory01.machine01.total', 'F1': 'root.factory01.machine01.task', 'G1': 'root.factory01.machine01.low', 'H1': 'root.factory01.machine01.pass', 'I1': 'root.factory01.machine01.spec', 'A2': '1772719740000', 'B2': '', 'C2': '', 'D2': '22.0', 'E2': '58.0', 'F2': 'BK26-38', 'G2': '5.0', 'H2': '53.0', 'I2': 'WPC710三层', 'A3': '1773063060000', 'B3': '', 'C3': '', 'D3': '0.0', 'E3': '0.0', 'F3': 'BK26-38', 'G3': '0.0', 'H3': '0.0', 'I3': 'WPC710三层', 'A4': '1773065040000', 'B4': '', 'C4': '', 'D4': '0.0', 'E4': '0.0', 'F4': 'BK26-38', 'G4': '0.0', 'H4': '0.0', 'I4': 'WPC710三层', 'A5': '1774774316043', 'B5': 'HIM~1��լ�n{��t���VV^�svw�', 'C5': '', 'D5': '', 'E5': '', 'F5': '', 'G5': '', 'H5': '', 'I5': ''}, 'styles': {}, 'formats': {}, 'borders': {}, 'cols': {}, 'rows': {}, 'merges': [], 'conditionalFormats': [], 'dataValidationRules': [], 'figures': [], 'tables': [], 'isVisible': True} 
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

    name = fields.Char(string="名称", required=True)
    value = fields.Text(string="值", required=True)
    model_id = fields.Many2one("fts.data.model", string="数据模型", required=True, ondelete="cascade")
    flow_id = fields.Many2one("fts.nr.flow", string="流程", ondelete="set null")
