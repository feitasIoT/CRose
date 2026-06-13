# -*- coding: utf-8 -*-

import json

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class FtsAiChatWizard(models.TransientModel):
    _name = "fts.ai.chat.wizard"
    _description = "AI Chat"

    model_id = fields.Many2one(
        "fts.ai.model",
        string="Model",
        required=True,
        domain="[('model_type', 'in', ('vllm_base', 'vllm_adapter', 'provider'))]",
    )
    use_knowledge = fields.Boolean(string="Use Knowledge Base", default=True)
    document_ids = fields.Many2many("fts.ai.knowledge.document", string="Knowledge Documents")
    top_k = fields.Integer(string="Top K", default=5, required=True)
    user_text = fields.Text(string="Message", required=True)
    reply_text = fields.Text(string="Reply", readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if "model_id" in fields_list and not res.get("model_id"):
            default_model = self.env["fts.ai.model"].search(
                [("model_type", "in", ("vllm_base", "vllm_adapter", "provider")), ("is_default", "=", True)],
                limit=1,
            )
            if default_model:
                res["model_id"] = default_model.id
        return res

    def action_ask(self):
        self.ensure_one()
        query = (self.user_text or "").strip()
        if not query:
            raise ValidationError(_("Message is empty."))
        if not self.model_id:
            raise ValidationError(_("Model is required."))
        top_k = int(self.top_k or 0) or 5
        doc_ids = self.document_ids.ids
        if self.use_knowledge:
            data = self.model_id.rag_chat(query_text=query, document_ids=doc_ids, top_k=top_k)
        else:
            data = self.model_id.chat(messages=[{"role": "user", "content": query}])

        content = ""
        if isinstance(data, dict):
            choices = data.get("choices")
            if isinstance(choices, list) and choices:
                choice = choices[0] if isinstance(choices[0], dict) else {}
                message = choice.get("message") if isinstance(choice, dict) else {}
                if isinstance(message, dict):
                    content = message.get("content") or ""
        self.reply_text = content or (json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else str(data))
        return self._reload()

    def _reload(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("Ask AI"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

