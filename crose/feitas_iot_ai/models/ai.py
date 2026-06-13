import os
import json
import re
import tempfile
from collections import defaultdict
import requests

from odoo import models, fields, api, exceptions, _


class FtsAiModel(models.Model):
    _name = "fts.ai.model"
    _description = "AI Model"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(string="Name", required=True, tracking=True)
    model_type = fields.Selection(
        [
            ("hf", "HuggingFace/Base (Training)"),
            ("vllm_base", "vLLM Base (Local Inference)"),
            ("vllm_adapter", "vLLM Adapter (LoRA)"),
            ("provider", "LLM Provider (External)"),
        ],
        string="Type",
        required=True,
        default="vllm_base",
        tracking=True,
    )
    model_ref = fields.Char(string="Model Reference", required=True, tracking=True)
    component_id = fields.Many2one("crose.component", string="Component", tracking=True)
    account_id = fields.Many2one(
        "crose.component.account",
        string="Account",
        domain="[('component_id', '=', component_id)]",
        tracking=True,
    )
    adapter_path = fields.Char(string="Adapter Path", tracking=True)
    base_model_id = fields.Many2one(
        "fts.ai.model",
        string="Base Model",
        domain="[('model_type', '=', 'vllm_base')]",
        tracking=True,
    )
    is_default = fields.Boolean(string="Default", default=False, tracking=True)

    @api.constrains("model_type", "is_default")
    def _check_single_default_per_type(self):
        for record in self:
            if not record.is_default:
                continue
            domain = [("id", "!=", record.id), ("model_type", "=", record.model_type), ("is_default", "=", True)]
            if self.search_count(domain):
                raise exceptions.ValidationError(_("Only one default model is allowed per type."))

    def _get_component(self):
        self.ensure_one()
        if self.component_id:
            return self.component_id
        component_type = False
        if self.model_type in ("vllm_base", "vllm_adapter"):
            component_type = "ai"
        elif self.model_type == "provider":
            component_type = "llm_provider"
        if not component_type:
            return self.env["crose.component"]
        component = self.env["crose.component"].search([("component_type", "=", component_type), ("status", "=", "online")], limit=1)
        if not component:
            component = self.env["crose.component"].search([("component_type", "=", component_type)], limit=1)
        return component

    def _resolve_endpoint(self, component, metadata_key):
        self.ensure_one()
        if not component:
            raise exceptions.ValidationError(_("No component is configured for this model."))
        try:
            return component._resolve_metadata_endpoint(metadata_key)
        except Exception as error:
            raise exceptions.ValidationError(_("Failed to resolve endpoint (%(key)s): %(error)s", key=metadata_key, error=str(error)))

    def _provider_headers(self):
        self.ensure_one()
        headers = {"Content-Type": "application/json"}
        if self.account_id:
            token = self.account_id._get_plain_password()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def ensure_remote_ready(self):
        self.ensure_one()
        if self.model_type != "vllm_adapter":
            return
        component = self._get_component()
        endpoint = self._resolve_endpoint(component, "load_adapter_api_path")
        adapter_name = (self.model_ref or "").strip()
        adapter_path = (self.adapter_path or "").strip()
        if not adapter_name:
            raise exceptions.ValidationError(_("Model Reference is required for vLLM adapter models."))
        if not adapter_path:
            raise exceptions.ValidationError(_("Adapter Path is required for vLLM adapter models."))
        payload = {"adapter_name": adapter_name, "adapter_path": adapter_path}
        try:
            response = requests.post(endpoint, json=payload, timeout=60)
            response.raise_for_status()
        except Exception as error:
            raise exceptions.ValidationError(_("Failed to load vLLM adapter: %(error)s", error=str(error)))

    def chat(self, messages, temperature=0.1, max_tokens=512):
        self.ensure_one()
        model_ref = (self.model_ref or "").strip()
        if not model_ref:
            raise exceptions.ValidationError(_("Model Reference is required."))
        if not isinstance(messages, list) or not messages:
            raise exceptions.ValidationError(_("Messages are required."))

        if self.model_type in ("vllm_base", "vllm_adapter"):
            if self.model_type == "vllm_adapter":
                self.ensure_remote_ready()
            component = self._get_component()
            endpoint = self._resolve_endpoint(component, "infer_api_path")
            payload = {"model": model_ref, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
            try:
                response = requests.post(endpoint, json=payload, timeout=120)
                response.raise_for_status()
                return response.json() if response.text else {}
            except Exception as error:
                raise exceptions.ValidationError(_("Inference failed: %(error)s", error=str(error)))

        if self.model_type == "provider":
            component = self._get_component()
            endpoint = self._resolve_endpoint(component, "chat_completions_path")
            payload = {"model": model_ref, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
            try:
                response = requests.post(endpoint, headers=self._provider_headers(), json=payload, timeout=120)
                response.raise_for_status()
                return response.json() if response.text else {}
            except Exception as error:
                raise exceptions.ValidationError(_("Provider call failed: %(error)s", error=str(error)))

        raise exceptions.ValidationError(_("Unsupported model type: %(type)s", type=self.model_type))

    def rag_chat(self, query_text, document_ids=None, top_k=5, temperature=0.1, max_tokens=512):
        self.ensure_one()
        query_text = (query_text or "").strip()
        if not query_text:
            raise exceptions.ValidationError(_("Query is empty."))
        context = self.env["fts.ai.knowledge.document"].rag_context(
            query_text=query_text,
            document_ids=document_ids or [],
            top_k=top_k,
        )
        system_prompt = "你是 CRose 平台的 AI 助手。请严格基于提供的知识库片段回答；若片段中没有依据，请直接说明无法从知识库中找到答案。"
        messages = [{"role": "system", "content": system_prompt}]
        if context:
            messages.append({"role": "system", "content": f"知识库片段：\n{context}"})
        messages.append({"role": "user", "content": query_text})
        return self.chat(messages=messages, temperature=temperature, max_tokens=max_tokens)


class FtsAiDataset(models.Model):
    _name = "fts.ai.dataset"
    _description = "Dataset"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(string="Name", required=True)
    dataset_message_ids = fields.Many2many(
        "fts.ai.dataset.message",
        relation="fts_ai_dataset_message_relation",
        string="Atomic Messages",
        tracking=True,
    )
    message_count = fields.Integer(string="Message Count", compute="_compute_message_metrics", store=True)
    category_ratio = fields.Char(string="Category Ratio", compute="_compute_message_metrics", store=True)

    @api.depends("dataset_message_ids", "dataset_message_ids.category_ids", "dataset_message_ids.primary_category_id")
    def _compute_message_metrics(self):
        for record in self:
            record.message_count = len(record.dataset_message_ids)
            if not record.dataset_message_ids:
                record.category_ratio = "0:0"
                continue
            counts = defaultdict(int)
            for message in record.dataset_message_ids:
                primary = message.primary_category_id
                if primary:
                    counts[primary.name] += 1
            if not counts:
                record.category_ratio = "0:0"
                continue
            ordered_counts = sorted(counts.items(), key=lambda item: item[1], reverse=True)
            if len(ordered_counts) == 1:
                record.category_ratio = f"{ordered_counts[0][0]} 100%"
                continue
            first_name, first_count = ordered_counts[0]
            second_name, second_count = ordered_counts[1]
            ratio = f"{first_count}:{second_count}"
            record.category_ratio = f"{first_name}/{second_name} {ratio}"


class FtsAiCategory(models.Model):
    _name = "fts.ai.category"
    _description = "AI Category"

    name = fields.Char(string="Name", required=True)
    active = fields.Boolean(string="Active", default=True)
    color = fields.Integer(string="Color")


class FtsAiPrompt(models.Model):
    _name = "fts.ai.prompt"
    _description = "AI Prompt"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(string="Name", required=True, tracking=True)
    content = fields.Text(string="Content", required=True)
    category_ids = fields.Many2many(
        "fts.ai.category",
        "fts_ai_prompt_category_rel",
        "prompt_id",
        "category_id",
        string="Categories",
        tracking=True,
    )
    is_template = fields.Boolean(string="Template Prompt", default=False, tracking=True)


class FtsAiDatasetMessage(models.Model):
    _name = "fts.ai.dataset.message"
    _description = "Dataset Atomic Message"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(string="Number", required=True, copy=False, readonly=True, default="New")
    format = fields.Char(string="Format", default="ChatML")
    system = fields.Text(string="System", required=True)
    user = fields.Text(string="User", required=True)
    assistant = fields.Text(string="Assistant", required=True)
    category_ids = fields.Many2many(
        "fts.ai.category",
        "fts_ai_dataset_message_category_rel",
        "message_id",
        "category_id",
        string="Categories",
        tracking=True,
    )
    primary_category_id = fields.Many2one("fts.ai.category", string="Primary Category", compute="_compute_primary_category", store=True)

    @api.depends("category_ids")
    def _compute_primary_category(self):
        for record in self:
            record.primary_category_id = record.category_ids[:1].id if record.category_ids else False

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = sequence.next_by_code("fts.ai.dataset.message") or "New"
        return super().create(vals_list)


class FtsAiTraining(models.Model):
    _name = "fts.ai.training"
    _description = "Model Training"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(string="Model Name/Alias", required=True)
    dataset_ids = fields.Many2many(
        "fts.ai.dataset",
        "fts_ai_training_dataset_rel",
        "training_id",
        "dataset_id",
        string="Datasets",
        required=True,
        tracking=True,
    )
    base_model_id = fields.Many2one(
        "fts.ai.model",
        string="Base Model",
        required=True,
        domain="[('model_type', '=', 'hf')]",
    )
    epochs = fields.Integer(string="Epochs", default=10)
    quantization = fields.Selection([("no", "No"), ("4bit", "4-bit"), ("8bit", "8-bit")], string="Quantization", default="no")
    batch_size = fields.Integer(string="Batch Size", default=1)
    gradient_accumulation_steps = fields.Integer(string="Gradient Accumulation", default=4)
    learning_rate = fields.Float(string="Learning Rate", default=1e-4)
    adapter_type = fields.Selection([("LoRA", "LoRA")], string="Adapter Type", default="LoRA")
    lora_rank = fields.Integer(string="LoRA Rank", default=8)
    lora_alpha = fields.Float(string="LoRA Alpha", default=32)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("pending", "Pending"),
            ("queued", "Queued"),
            ("preparing", "Preparing"),
            ("training", "Training"),
            ("running", "Running"),
            ("completed", "Completed"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
        string="State",
        default="draft",
        tracking=True,
    )
    progress_pct = fields.Float(string="Progress (%)", digits=(5, 2), default=0.0, tracking=True)
    current_epoch = fields.Integer(string="Current Epoch", default=0)
    total_epoch = fields.Integer(string="Total Epoch", compute="_compute_total_epoch", store=True)
    current_step = fields.Integer(string="Current Step", default=0)
    total_step = fields.Integer(string="Total Step", default=0)
    eta_seconds = fields.Integer(string="ETA (s)", default=0)
    external_job_id = fields.Char(string="External Job ID", tracking=True)
    dataset_file_path = fields.Char(string="Dataset File Path", readonly=True, copy=False)
    dataset_key = fields.Char(string="Dataset Key", readonly=True, copy=False)
    output_path = fields.Char(string="Output Path", readonly=True, copy=False)
    loaded_model_name = fields.Char(string="Loaded Model Alias", readonly=True, copy=False, tracking=True)
    loaded_model_path = fields.Char(string="Loaded Model Path", readonly=True, copy=False)
    is_model_loaded = fields.Boolean(string="Model Loaded", default=False, readonly=True)
    last_inference_at = fields.Datetime(string="Last Inference At", readonly=True, copy=False)
    last_inference_reply = fields.Text(string="Last Inference Reply", readonly=True, copy=False)
    last_heartbeat = fields.Datetime(string="Last Heartbeat", readonly=True, copy=False)
    log_tail = fields.Text(string="Latest Logs", readonly=True, copy=False)
    error_message = fields.Text(string="Error Message", readonly=True, copy=False)

    @api.depends("epochs")
    def _compute_total_epoch(self):
        for record in self:
            record.total_epoch = int(record.epochs or 0)

    def _get_training_gateway_component(self):
        component = self.env["crose.component"].search(
            [("component_type", "=", "ai"), ("status", "=", "online")],
            limit=1,
        )
        if not component:
            component = self.env["crose.component"].search([("component_type", "=", "ai")], limit=1)
        if not component:
            raise exceptions.ValidationError(_("No AI gateway component was found. Please configure component type AI in System Components."))
        return component

    def _get_ai_dataset_root(self):
        env_path = os.getenv("CROSE_AI_DATASET_ROOT")
        root_path = self.env["ir.config_parameter"].sudo().get_param("crose_iot.ai_dataset_root", env_path or "/mnt/ai-datasets")
        return root_path.rstrip("/\\")

    def _get_default_output_path(self):
        return f"/app/output/{self.id}_{self.name}"

    def _sanitize_dataset_key(self):
        base = re.sub(r"[^a-zA-Z0-9_]+", "_", self.name or "").strip("_")
        if not base:
            base = "training"
        return f"{base}_{self.id}"

    def _ensure_dataset_root_writable(self, dataset_root):
        try:
            os.makedirs(dataset_root, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=dataset_root, prefix=".crose_probe_", delete=True):
                pass
        except PermissionError as error:
            raise exceptions.ValidationError(
                _(
                    "Dataset path is not writable: %(path)s. "
                    "Please fix volume permission for the Odoo container user, then retry. "
                    "Recommended command: docker compose run --rm --user root web sh -lc "
                    "\"mkdir -p %(path)s && chown -R odoo:odoo %(path)s && chmod -R u+rwX,g+rwX %(path)s\". "
                    "Original error: %(error)s",
                    path=dataset_root,
                    error=str(error),
                )
            )

    def _to_chatml_item(self, message):
        messages = []
        if message.system:
            messages.append({"role": "system", "content": message.system})
        messages.append({"role": "user", "content": message.user or ""})
        messages.append({"role": "assistant", "content": message.assistant or ""})
        return {
            "messages": messages,
        }

    def _collect_dataset_messages(self):
        self.ensure_one()
        return self.dataset_ids.mapped("dataset_message_ids")

    def _prepare_dataset_files(self):
        self.ensure_one()
        messages = self._collect_dataset_messages()
        if not messages:
            raise exceptions.ValidationError(_("At least one atomic message is required to start training."))
        dataset_root = self._get_ai_dataset_root()
        self._ensure_dataset_root_writable(dataset_root)
        dataset_key = self._sanitize_dataset_key()
        dataset_filename = f"{dataset_key}.jsonl"
        dataset_path = os.path.join(dataset_root, dataset_filename)
        temp_path = f"{dataset_path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as dataset_fp:
            for message in messages:
                item = self._to_chatml_item(message)
                dataset_fp.write(json.dumps(item, ensure_ascii=False))
                dataset_fp.write("\n")
        os.replace(temp_path, dataset_path)
        info_path = os.path.join(dataset_root, "dataset_info.json")
        info_data = {}
        if os.path.exists(info_path):
            try:
                with open(info_path, "r", encoding="utf-8") as info_fp:
                    info_data = json.load(info_fp) or {}
            except Exception:
                info_data = {}
        info_data[dataset_key] = {
            "file_name": dataset_filename,
            "formatting": "openai",
            "columns": {"messages": "messages"},
            "tags": {
                "role_tag": "role",
                "content_tag": "content",
                "user_tag": "user",
                "assistant_tag": "assistant",
                "system_tag": "system",
            },
        }
        tmp_info_path = f"{info_path}.tmp"
        with open(tmp_info_path, "w", encoding="utf-8") as info_fp:
            json.dump(info_data, info_fp, ensure_ascii=False, indent=2)
        os.replace(tmp_info_path, info_path)
        return dataset_key, dataset_path

    def _get_training_api_endpoint(self, component):
        try:
            return component._resolve_metadata_endpoint("train_api_path")
        except Exception as error:
            raise exceptions.ValidationError(_("Failed to resolve training API endpoint: %(error)s", error=str(error)))

    def _get_training_status_endpoint(self, component):
        try:
            endpoint = component._resolve_metadata_endpoint("train_status_api_path")
        except Exception as error:
            raise exceptions.ValidationError(_("Failed to resolve training status API endpoint: %(error)s", error=str(error)))
        if "{job_id}" in endpoint:
            return endpoint.format(job_id=self.external_job_id)
        return f"{endpoint.rstrip('/')}/{self.external_job_id}"

    def _get_gateway_endpoint(self, component, metadata_key):
        try:
            return component._resolve_metadata_endpoint(metadata_key)
        except Exception as error:
            raise exceptions.ValidationError(_("Failed to resolve gateway endpoint (%(key)s): %(error)s", key=metadata_key, error=str(error)))

    def _build_model_alias(self):
        self.ensure_one()
        alias = re.sub(r"[^a-zA-Z0-9_]+", "_", self.name or "").strip("_")
        if not alias:
            alias = "training_model"
        return f"{alias}_{self.id}"

    def _to_vllm_adapter_path(self):
        self.ensure_one()
        source = (self.output_path or "").strip()
        if not source:
            raise exceptions.ValidationError(_("Output path is empty. Please run training first."))
        if source.startswith("/app/output/"):
            return source.replace("/app/output/", "/app/lora_adapters/", 1)
        return source

    def _build_training_yaml(self):
        model_ref = self._resolve_training_model_reference()
        quantization_bit = ""
        if self.quantization == "4bit":
            quantization_bit = "4"
        elif self.quantization == "8bit":
            quantization_bit = "8"
        lines = [
            "stage: sft",
            "do_train: true",
            f"model_name_or_path: {model_ref}",
            f"dataset: {self.dataset_key}",
            "dataset_dir: /app/data",
            "template: qwen",
            f"finetuning_type: {str(self.adapter_type or '').lower()}",
            "lora_target: all",
        ]
        if quantization_bit:
            lines.append(f"quantization_bit: {quantization_bit}")
        lines.extend(
            [
                f"per_device_train_batch_size: {int(self.batch_size or 1)}",
                f"gradient_accumulation_steps: {int(self.gradient_accumulation_steps or 4)}",
                f"learning_rate: {float(self.learning_rate or 1e-4)}",
                f"num_train_epochs: {float(self.epochs or 10)}",
                "lr_scheduler_type: cosine",
                "max_samples: 1000",
                f"output_dir: {self.output_path or self._get_default_output_path()}",
                "overwrite_output_dir: true",
                "fp16: true",
                f"lora_rank: {int(self.lora_rank or 8)}",
                f"lora_alpha: {float(self.lora_alpha or 32)}",
            ]
        )
        return "\n".join(lines)

    def _resolve_training_model_reference(self):
        self.ensure_one()
        model_ref = (self.base_model_id.model_ref or "").strip()
        if not model_ref:
            raise exceptions.ValidationError(_("Base model identifier is required."))
        normalized_model_ref = model_ref.lower()
        if model_ref.isdigit():
            raise exceptions.ValidationError(
                _(
                    "Base model '%(model)s' is invalid. "
                    "Please use a valid Hugging Face model id (for example: Qwen/Qwen2-1.5B-Instruct) "
                    "or a local path that exists inside crose_ai_train container.",
                    model=model_ref,
                )
            )
        if "awq" in normalized_model_ref:
            raise exceptions.ValidationError(
                _(
                    "Base model '%(model)s' appears to be an AWQ quantized model. "
                    "Current training pipeline requires a non-AWQ base model "
                    "(for example: Qwen/Qwen2-1.5B-Instruct).",
                    model=model_ref,
                )
            )
        return model_ref

    def _apply_remote_status(self, payload):
        self.ensure_one()
        if not isinstance(payload, dict):
            return
        updates = {"last_heartbeat": fields.Datetime.now()}
        remote_state = str(payload.get("status") or payload.get("state") or "").lower()
        mapping = {
            "queued": "queued",
            "preparing": "preparing",
            "pending": "queued",
            "running": "running",
            "training": "running",
            "completed": "completed",
            "done": "completed",
            "failed": "failed",
            "error": "failed",
            "cancelled": "cancelled",
        }
        if remote_state in mapping:
            updates["state"] = mapping[remote_state]
        progress = payload.get("progress")
        if progress is not None:
            try:
                updates["progress_pct"] = float(progress)
            except Exception:
                pass
        for src, dst in [
            ("current_epoch", "current_epoch"),
            ("epoch", "current_epoch"),
            ("total_epoch", "total_epoch"),
            ("current_step", "current_step"),
            ("step", "current_step"),
            ("total_step", "total_step"),
            ("eta_seconds", "eta_seconds"),
            ("eta", "eta_seconds"),
        ]:
            if src in payload and payload.get(src) is not None:
                try:
                    updates[dst] = int(payload.get(src))
                except Exception:
                    continue
        if payload.get("logs"):
            updates["log_tail"] = str(payload.get("logs"))
        if payload.get("error"):
            updates["error_message"] = str(payload.get("error"))
        self.write(updates)

    def action_start_training(self):
        self.ensure_one()
        dataset_key, dataset_file_path = self._prepare_dataset_files()
        self.write(
            {
                "dataset_key": dataset_key,
                "dataset_file_path": dataset_file_path,
                "output_path": self._get_default_output_path(),
                "state": "preparing",
                "progress_pct": 0.0,
                "current_epoch": 0,
                "current_step": 0,
                "error_message": False,
            }
        )
        component = self._get_training_gateway_component()
        endpoint = self._get_training_api_endpoint(component)
        yaml_text = self._build_training_yaml()
        try:
            response = requests.post(
                endpoint,
                data=yaml_text.encode("utf-8"),
                headers={"Content-Type": "text/yaml; charset=utf-8"},
                timeout=60,
            )
            response.raise_for_status()
            try:
                body = response.json() if response.text else {}
            except Exception:
                body = {}
        except Exception as e:
            self.write({"state": "failed", "error_message": str(e)})
            raise exceptions.ValidationError(_("Failed to start training: %(error)s", error=str(e)))
        external_job_id = body.get("job_id") or body.get("id") if isinstance(body, dict) else False
        updates = {"state": "queued", "last_heartbeat": fields.Datetime.now()}
        if external_job_id:
            updates["external_job_id"] = str(external_job_id)
        self.write(updates)
        self.message_post(body=_("Training started. Dataset file: %(path)s", path=dataset_file_path))

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Training Started"),
                "message": _("Training request has been sent to AI gateway, and dataset has been prepared."),
                "type": "success",
                "sticky": False,
            },
        }

    def action_refresh_training_status(self):
        for record in self:
            if not record.external_job_id:
                continue
            component = record._get_training_gateway_component()
            endpoint = record._get_training_status_endpoint(component)
            try:
                response = requests.get(endpoint, timeout=15)
                response.raise_for_status()
                try:
                    payload = response.json() if response.text else {}
                except Exception:
                    payload = {}
                record._apply_remote_status(payload)
            except Exception as error:
                record.write({"last_heartbeat": fields.Datetime.now(), "error_message": str(error)})

    def action_load_model(self):
        for record in self:
            if record.state != "completed":
                raise exceptions.ValidationError(_("Training must be completed before loading the model."))
            component = record._get_training_gateway_component()
            endpoint = record._get_gateway_endpoint(component, "load_adapter_api_path")
            alias = record.loaded_model_name or record._build_model_alias()
            adapter_path = record._to_vllm_adapter_path()
            payload = {
                "model": record._resolve_training_model_reference(),
                "adapter_name": alias,
                "adapter_path": adapter_path,
            }
            try:
                response = requests.post(endpoint, json=payload, timeout=30)
                response.raise_for_status()
                body = response.json() if response.text else {}
            except Exception as error:
                raise exceptions.ValidationError(_("Failed to load model adapter: %(error)s", error=str(error)))
            record.write(
                {
                    "loaded_model_name": alias,
                    "loaded_model_path": adapter_path,
                    "is_model_loaded": True,
                    "error_message": False,
                }
            )
            record.message_post(
                body=_(
                    "Model adapter loaded.\nAlias: %(alias)s\nPath: %(path)s\nGateway response: %(response)s",
                    alias=alias,
                    path=adapter_path,
                    response=json.dumps(body, ensure_ascii=False) if isinstance(body, dict) else str(body),
                )
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Model Loaded"),
                "message": _("The trained model adapter has been loaded into vLLM through AI gateway."),
                "type": "success",
                "sticky": False,
            },
        }

    def action_unload_model(self):
        for record in self:
            if not record.loaded_model_name:
                raise exceptions.ValidationError(_("No loaded model alias was found for this training task."))
            component = record._get_training_gateway_component()
            endpoint = record._get_gateway_endpoint(component, "unload_adapter_api_path")
            payload = {
                "adapter_name": record.loaded_model_name,
            }
            try:
                response = requests.post(endpoint, json=payload, timeout=30)
                response.raise_for_status()
                body = response.json() if response.text else {}
            except Exception as error:
                raise exceptions.ValidationError(_("Failed to unload model adapter: %(error)s", error=str(error)))
            record.write(
                {
                    "is_model_loaded": False,
                    "error_message": False,
                }
            )
            record.message_post(
                body=_(
                    "Model adapter unloaded.\nAlias: %(alias)s\nGateway response: %(response)s",
                    alias=record.loaded_model_name,
                    response=json.dumps(body, ensure_ascii=False) if isinstance(body, dict) else str(body),
                )
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Model Unloaded"),
                "message": _("The model adapter has been unloaded from vLLM through AI gateway."),
                "type": "success",
                "sticky": False,
            },
        }

    def action_infer(self):
        fixed_system_prompt = "你是 CRose 平台的 AI 助手，请基于当前训练模型给出简洁、可执行的回复。"
        fixed_user_prompt = "请介绍你最擅长的任务类型，并给出一个用于数据模型转流程的示例回答。"
        for record in self:
            if not record.is_model_loaded or not record.loaded_model_name:
                raise exceptions.ValidationError(_("Please load the model first."))
            component = record._get_training_gateway_component()
            endpoint = record._get_gateway_endpoint(component, "infer_api_path")
            payload = {
                "model": record.loaded_model_name,
                "messages": [
                    {"role": "system", "content": fixed_system_prompt},
                    {"role": "user", "content": fixed_user_prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 512,
            }
            try:
                response = requests.post(endpoint, json=payload, timeout=60)
                response.raise_for_status()
                data = response.json() if response.text else {}
            except Exception as error:
                raise exceptions.ValidationError(_("Inference failed: %(error)s", error=str(error)))
            content_text = ""
            if isinstance(data, dict):
                choices = data.get("choices")
                if isinstance(choices, list) and choices:
                    first_choice = choices[0] if isinstance(choices[0], dict) else {}
                    message = first_choice.get("message") if isinstance(first_choice, dict) else {}
                    if isinstance(message, dict):
                        content_text = message.get("content") or ""
            if not content_text:
                content_text = json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else str(data)
            record.write(
                {
                    "last_inference_at": fields.Datetime.now(),
                    "last_inference_reply": content_text,
                }
            )
            record.message_post(
                body=_(
                    "Inference with model %(model)s\nPrompt: %(prompt)s\nReply: %(reply)s",
                    model=record.loaded_model_name,
                    prompt=fixed_user_prompt,
                    reply=content_text,
                )
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Inference Complete"),
                "message": _("Inference result has been posted to chatter."),
                "type": "success",
                "sticky": False,
            },
        }
