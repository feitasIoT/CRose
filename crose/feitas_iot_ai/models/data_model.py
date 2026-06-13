# -*- coding: utf-8 -*-

import contextlib
import json
import re
import uuid

from odoo import _, fields, models
from odoo.exceptions import ValidationError


class DataModel(models.Model):
    _inherit = "fts.data.model"

    ai_model_name = fields.Char(
        string="AI Model",
        help="Model alias used by AI Flow inference, usually a loaded LoRA alias in vLLM.",
    )

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

        system_prompt = str(metadata.get("system_prompt") or "你是一个 Node-RED 专家，只输出 JSON 流程。").strip()
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
