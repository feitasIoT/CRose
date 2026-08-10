import json
import logging

import requests

from odoo import models, fields, api, _
from odoo.exceptions import UserError


_logger = logging.getLogger(__name__)


class FtsNrNode(models.Model):
    _name = "fts.nr.node"
    _description = "Node-RED Node"

    name = fields.Char(string="Name", required=True)
    nr_id = fields.Char(string="Node ID", required=True)
    node_type = fields.Char(string="Type")
    content = fields.Text(string="Content")

    flow_id = fields.Many2one("fts.nr.flow", string="Flow", required=True, ondelete="cascade")
    instance_id = fields.Many2one(
        "fts.nr.instance",
        string="Instance",
        related="flow_id.instance_id",
        store=True,
        readonly=True,
    )
    config_node_ids = fields.Many2many(
        "fts.nr.node",
        "fts_nr_node_config_rel",
        "node_id",
        "config_node_id",
        string="Config Nodes",
    )
    item_ids = fields.One2many("fts.node.item", "node_id", string="Configuration Items")

    def _format_json_text(self, value):
        if value is None:
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, indent=2)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped or stripped[0] not in ("{", "["):
                return value
            try:
                parsed = json.loads(value)
            except Exception:
                return value
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        return value

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if "content" in vals:
                vals["content"] = self._format_json_text(vals.get("content"))
        return super().create(vals_list)

    def write(self, vals):
        if "content" in vals:
            vals["content"] = self._format_json_text(vals.get("content"))
        return super().write(vals)

    # ========================================================================
    # Run flow (edge node triggers Node-RED flow execution)
    # ========================================================================

    def action_run_flow(self, edge_node_id=None):
        """根据节点 type 触发其所属流程（目前仅支持 http in）。

        self 为流程中 type=http in 的节点（job 挂在该节点上排队执行）。
        http in 节点有 url 属性，用 requests.post 触发；
        POST body 为流程 param_ids 组装出的参数。
        """
        self.ensure_one()
        node_type = self.node_type or ""
        if node_type != "http in":
            raise UserError(
                _("Trigger type '%(type)s' is not supported yet (only 'http in').",
                  type=node_type)
            )

        flow = self.flow_id
        edge_node = (
            self.env["fts.edge.node"].browse(edge_node_id)
            if edge_node_id
            else self.env["fts.edge.node"]
        )

        instance = flow.instance_id
        if not instance:
            raise UserError(
                _("Flow '%(flow)s' is not linked to any Node-RED instance.",
                  flow=flow.display_name)
            )
        if not instance.ip_address or not instance.port:
            raise UserError(
                _("Instance '%(instance)s' has no IP address or port configured.",
                  instance=instance.display_name)
            )

        content = {}
        try:
            parsed = json.loads(self.content or "{}") if isinstance(self.content, str) else (self.content or {})
            if isinstance(parsed, dict):
                content = parsed
        except Exception:
            pass
        url_path = content.get("url") or ""
        if not url_path:
            raise UserError(
                _("The 'http in' node of flow '%(flow)s' has no url.",
                  flow=flow.display_name)
            )

        url = "http://%s:%d%s" % (instance.ip_address, instance.port, url_path)
        params = self._build_trigger_params(flow, edge_node)
        _logger.info(
            "Triggering flow '%s' via %s, params=%s",
            flow.display_name, url, json.dumps(params, ensure_ascii=False),
        )
        try:
            response = requests.post(url, json=params, timeout=15)
            response.raise_for_status()
        except Exception as exc:
            raise UserError(
                _("Failed to trigger flow '%(flow)s': %(error)s",
                  flow=flow.display_name, error=str(exc))
            )
        return True

    def _build_trigger_params(self, flow, edge_node):
        """根据流程 param_ids 组装触发参数。

        param name 用点号表示嵌套，例如 target.host -> {"target": {"host": ...}}；
        param value 若以 record. 开头，解析为触发向导的边缘节点字段值。
        """
        params = {}
        for param in flow.param_ids:
            name = (param.name or "").strip()
            if not name:
                continue
            value = self._resolve_param_value(param.value or "", edge_node)
            self._set_nested_value(params, name, value)
        return params

    def _resolve_param_value(self, raw_value, edge_node):
        if not isinstance(raw_value, str):
            return raw_value
        text = raw_value.strip()
        if text.startswith("record."):
            return self._resolve_record_path(edge_node, text[len("record."):])
        return raw_value

    def _resolve_record_path(self, record, path):
        """在记录上解析点号路径，例如 ip_address 或 gateway_id.name。"""
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
            current = current[:1]
            return current.display_name if current else ""
        return current

    def _set_nested_value(self, container, dotted_name, value):
        """把值写入点号路径，例如 target.host 写入 {"target": {"host": value}}。"""
        keys = [part for part in dotted_name.split(".") if part]
        if not keys:
            return
        current = container
        for part in keys[:-1]:
            child = current.get(part)
            if not isinstance(child, dict):
                child = {}
                current[part] = child
            current = child
        current[keys[-1]] = value
