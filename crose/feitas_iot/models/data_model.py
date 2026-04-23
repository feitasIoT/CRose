import base64
import contextlib
import json
import math
import logging
import re
import uuid
from datetime import datetime
import requests


from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


_logger = logging.getLogger(__name__)

SPREADSHEET_VERSION = "18.5.1"
SPREADSHEET_SHEET_ID = "Sheet1"


class DataModel(models.Model):
    _name = 'fts.data.model'
    _description = 'Data Model'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'spreadsheet.mixin']

    name = fields.Char(string='Code', required=True, copy=False)
    partner_id = fields.Many2one('res.partner', string='Requester', required=True)
    data_asset_id = fields.Many2one('fts.data.asset', string='Data Asset', required=True)
    data_asset_ids = fields.Many2many("fts.data.asset", string="Assets", relation="rel_data_asset_modeling")
    query_data_asset_ids = fields.Many2many("fts.data.asset", relation="rel_query_data_asset", string="Query Assets")
    provider_id = fields.Many2one('res.partner', string='Provider', related='data_asset_id.partner_id', store=True, readonly=True)
    protocol = fields.Selection([
        ('mobus-tcp', 'Modbus-TCP'),
        ('mobus-rtu', 'Modbus-RTU'),
        ('mqtt', 'MQTT'),
        ('http', 'HTTP'),
        ('coap', 'CoAP'),
        ('smb', 'SMB2'),
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

    query_type = fields.Selection([
        ('data', 'Time-Series Data'),
        ('log', 'Logs'),
    ], string='Query Type', default='data', required=True)
    query_start_time = fields.Datetime(string='Start Time')
    query_end_time = fields.Datetime(string='End Time')
    query_interval = fields.Integer(string='Interval (Seconds)', default=60)

    redis_key = fields.Char(string='Redis Key', help='Fixed Redis key to query, e.g. check_db')

    @api.onchange('query_start_time')
    def _onchange_query_start_time(self):
        if self.query_start_time and not self.query_end_time:
            self.query_end_time = fields.Datetime.now()
        if self.query_start_time and not self.query_interval:
            self.query_interval = 60

    description = fields.Text(string='Description')
    mqtt_topic_id = fields.Many2one('fts.mqtt.topic', string='MQTT Topic')
    nr_instance_id = fields.Many2one('fts.nr.instance', string='Runtime Instance', help='Local instance responsible for data processing')
    nr_flow_ids = fields.Many2many('fts.nr.flow', 'data_model_nr_flow_rel', string='Flows')
    app_ids = fields.One2many("fts.data.app", "model_id", string='Applications')
    app_param_ids = fields.One2many("fts.nr.flow.param", "model_id", string='Application Parameters')
    log_ids = fields.One2many('fts.data.log', 'model_id', string='Logs')
    address_ids = fields.One2many('fts.data.address', 'model_id', string='Addresses')
    data_structure = fields.Text(string='Data Structure', required=True)
    # FIXME: remove
    ai_model_name = fields.Char(string='AI Model', help='Model alias used by AI Flow inference, usually a loaded LoRA alias in vLLM.')
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


    @api.constrains('name', 'provider_id')
    def _check_name_provider_unique(self):
        for record in self:
            existing = self.search_count([
                ('name', '=', record.name),
                ('provider_id', '=', record.provider_id.id),
                ('id', '!=', record.id),
            ])
            if existing:
                raise ValidationError(_('The combination of Code and Provider must be unique.'))
    
    @api.onchange("data_asset_ids")
    def _onchange_data_asset_ids(self):
        self.query_data_asset_ids = [(6, 0, self.data_asset_ids.ids)]

    @api.depends('provider_id.name', 'name')
    def _compute_data_asset(self):
        for record in self:
            record.data_asset = f'{record.provider_id.name}.{record.name}' if record.provider_id and record.name else False

    @api.depends('provider_id.name', 'name')
    def _compute_topic(self):
        for record in self:
            provider_name = record.provider_id.name or ''
            record.topic = f'upload/{provider_name}/{record.name}' if record.name else False
            record.iotdb_topic = f'root.{provider_name}.{record.name}' if record.name else False

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

    def copy(self, default=None):
        self.ensure_one()
        default = dict(default or {})
        if not default.get("name"):
            original_name = (self.name or "").strip() or "copy"
            base_name = re.sub(r"\(\d+\)$", "", original_name).strip() or "copy"
            provider_id = self.provider_id.id or self.data_asset_id.partner_id.id or False
            i = 1
            while True:
                candidate = f"{base_name}({i})"
                exists = self.search_count([("name", "=", candidate), ("provider_id", "=", provider_id)]) > 0
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
            if self.query_type == 'data':
                start_ts, end_ts, count_sql, result_sql = self._build_iotdb_sql()
                count_df = self._execute_iotdb_query(count_sql)
                count = int(count_df.iloc[0, 0]) if len(count_df) > 0 else 0
            else:
                redis_value = self._execute_redis_query()
                if redis_value is None:
                    count = 0
                elif isinstance(redis_value, dict):
                    count = len(redis_value)
                elif isinstance(redis_value, (list, tuple, set)):
                    count = len(redis_value)
                else:
                    count = 1

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
                redis_value = self._execute_redis_query()
                result_df = self._build_redis_dataframe(redis_value)
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
            headers = {
                "Node-RED-API-Version": "v2",
            }
            last_error = None
            for base_url in self.nr_instance_id._nr_candidate_base_urls():
                url = f"{base_url}{path}"
                try:
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

    def _get_vllm_component(self):
        component = self.env["crose.component"].search(
            [("component_type", "=", "vllm"), ("status", "=", "online")],
            limit=1,
        )
        if not component:
            component = self.env["crose.component"].search([("component_type", "=", "vllm")], limit=1)
        if not component:
            raise ValidationError(_("No vLLM component was found. Please configure it in System Components."))
        return component

    def _get_vllm_endpoint_and_payload(self):
        # FIXME: Delete
        self.ensure_one()
        component = self._get_vllm_component()
        metadata = {}
        if component.metadata:
            with contextlib.suppress(Exception):
                metadata = json.loads(component.metadata)
        if not isinstance(metadata, dict):
            metadata = {}
        try:
            endpoint = component._resolve_metadata_endpoint("chat_completions_path")
        except Exception as error:
            raise ValidationError(_("vLLM component metadata must provide chat_completions_path. Error: %(error)s", error=str(error)))

        model_name = (self.ai_model_name or "").strip()
        if not model_name:
            raise ValidationError(_("Please set AI Model before running AI Flow."))
        temperature = metadata.get("temperature", 0.1)
        with contextlib.suppress(Exception):
            temperature = float(temperature)

        system_prompt = str(
            metadata.get("system_prompt")
            or "你是一个 Node-RED 专家，只输出 JSON 流程。"
        ).strip()
        user_prompt = _(
            "请根据以下数据模型生成可导入 Node-RED 的流程 JSON。"
            "\n名称: %(name)s"
            "\n协议: %(protocol)s"
            "\n运行实例: %(instance)s"
            "\n主题: %(topic)s"
            "\nIoTDB Topic: %(iotdb_topic)s"
            "\n数据结构: %(schema)s"
            "\n要求: 仅输出 JSON，不要 Markdown。"
        ) % {
            "name": self.name or "",
            "protocol": self.protocol or "",
            "instance": self.nr_instance_id.display_name if self.nr_instance_id else "",
            "topic": self.topic or "",
            "iotdb_topic": self.iotdb_topic or "",
            "schema": self.data_structure or "{}",
        }
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        return endpoint, payload

    def _extract_json_from_llm_text(self, text):
        if not isinstance(text, str):
            return None
        stripped = text.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            with contextlib.suppress(Exception):
                return json.loads(stripped)
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", stripped, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip()
            with contextlib.suppress(Exception):
                return json.loads(candidate)
        first_brace = stripped.find("{")
        last_brace = stripped.rfind("}")
        if first_brace >= 0 and last_brace > first_brace:
            candidate = stripped[first_brace:last_brace + 1]
            with contextlib.suppress(Exception):
                return json.loads(candidate)
        return None

    def action_generate_flow_ai(self):
        self.ensure_one()
        if not self.nr_instance_id:
            raise ValidationError(_("Please select a runtime instance before generating a flow."))
        return {
            "type": "ir.actions.act_window",
            "name": _("AI Flow"),
            "res_model": "fts.data.model.ai.flow.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "active_id": self.id,
                "active_model": self._name,
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

    def _action_generate_flow_ai_with_model(self, model, temperature=0.1, max_tokens=4096):
        self.ensure_one()
        if not self.nr_instance_id:
            raise ValidationError(_("Please select a runtime instance before generating a flow."))
        if not model:
            raise ValidationError(_("Model is required."))

        system_prompt = "你是一个 Node-RED 专家，只输出 JSON 流程。"
        user_prompt = _(
            "请根据以下数据模型生成可导入 Node-RED 的流程 JSON。"
            "\n名称: %(name)s"
            "\n协议: %(protocol)s"
            "\n运行实例: %(instance)s"
            "\n主题: %(topic)s"
            "\nIoTDB Topic: %(iotdb_topic)s"
            "\n数据结构: %(schema)s"
            "\n要求: 仅输出 JSON，不要 Markdown。"
        ) % {
            "name": self.name or "",
            "protocol": self.protocol or "",
            "instance": self.nr_instance_id.display_name if self.nr_instance_id else "",
            "topic": self.topic or "",
            "iotdb_topic": self.iotdb_topic or "",
            "schema": self.data_structure or "{}",
        }
        data = model.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )

        content_text = ""
        if isinstance(data, dict):
            choices = data.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0] if isinstance(choices[0], dict) else {}
                message = first.get("message") if isinstance(first, dict) else {}
                if isinstance(message, dict):
                    content_text = message.get("content") or ""
        
        parsed_json = self._extract_json_from_llm_text(content_text)
        if parsed_json is None:
            raise ValidationError(_("vLLM response does not contain valid flow JSON."))

        flow_name = f"{self.name} - AI Flow"
        if isinstance(parsed_json, dict):
            flow_name = parsed_json.get("label") or parsed_json.get("name") or flow_name
        
        created_flow = self.env["fts.nr.flow"].create(
            {
                "name": flow_name,
                "nr_id": f"{uuid.uuid4().hex[:7]}.{uuid.uuid4().hex[:7]}",
                "type": "tab",
                "is_template": False,
                "content": json.dumps(parsed_json, ensure_ascii=False),
                "instance_id": self.nr_instance_id.id,
                "data_model_id": self.id,
                "prompt": user_prompt,
                "description": _("Generated by LLaMA-Factory"),
            }
        )
        self.write({"nr_flow_ids": [(4, created_flow.id)]})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Generation Complete"),
                "message": _("Generated flow %(flow)s and linked it to this data model.", flow=created_flow.display_name),
                "type": "success",
                "sticky": False,
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
        if not self.iotdb_topic:
            raise ValidationError(_("Please configure the IoTDB Topic field before querying."))

        start_dt = fields.Datetime.to_datetime(self.query_start_time)
        end_dt = fields.Datetime.to_datetime(self.query_end_time)
        start_ts = int(start_dt.timestamp() * 1000)
        end_ts = int(end_dt.timestamp() * 1000)

        where_clause = f"time >= {start_ts} AND time <= {end_ts}"
        result_sql = f"SELECT * FROM {self.iotdb_topic} WHERE {where_clause} LIMIT 10000"
        count_sql = f"SELECT COUNT(*) FROM {self.iotdb_topic} WHERE {where_clause}"
        return start_ts, end_ts, count_sql, result_sql

    def _get_iotdb_connection_params(self):
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
        used = {}
        deduped = []
        for name in friendly_columns:
            if name not in used:
                used[name] = 1
                deduped.append(name)
            else:
                used[name] += 1
                deduped.append(f"{name}_{used[name]}")
        dataframe.columns = deduped
        return dataframe

    def _get_redis_connection_params(self):
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

    def _execute_redis_query(self):
        self.ensure_one()
        import redis
        host, port, username, password, db = self._get_redis_connection_params()
        key_name = self.topic
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
            return pd.DataFrame([{}])
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

    name = fields.Char(string="Name", required=True)
    value = fields.Text(string="Value", required=True)
    model_id = fields.Many2one("fts.data.model", string="Data Model", required=True, ondelete="cascade")
    flow_id = fields.Many2one("fts.nr.flow", string="Flow", ondelete="set null")
