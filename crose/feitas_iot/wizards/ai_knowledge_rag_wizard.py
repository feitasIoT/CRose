# -*- coding: utf-8 -*-

import json

from odoo import api, fields, models, _


class FtsAiKnowledgeRagWizard(models.TransientModel):
    _name = "fts.ai.knowledge.rag.wizard"
    _description = "Knowledge RAG Test"

    query_text = fields.Text(string="Query", required=True)
    top_k = fields.Integer(string="Top K", default=5, required=True)
    document_ids = fields.Many2many("fts.ai.knowledge.document", string="Documents")
    model_id = fields.Many2one(
        "fts.ai.model",
        string="Model",
        domain="[('model_type', 'in', ('vllm_base', 'vllm_adapter', 'provider'))]",
    )
    result_context = fields.Text(string="Context", readonly=True)
    result_hits = fields.Text(string="Hits (JSON)", readonly=True)
    result_answer = fields.Text(string="Answer", readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        default_document_ids = self.env.context.get("default_document_ids") or []
        if default_document_ids and "document_ids" in fields_list:
            res["document_ids"] = [(6, 0, list(default_document_ids))]
        if "model_id" in fields_list and not res.get("model_id"):
            default_model = self.env["fts.ai.model"].search(
                [("model_type", "in", ("vllm_base", "vllm_adapter", "provider")), ("is_default", "=", True)],
                limit=1,
            )
            if default_model:
                res["model_id"] = default_model.id
        return res

    def action_search(self):
        self.ensure_one()
        query = (self.query_text or "").strip()
        if not query:
            return self._reload()
        top_k = int(self.top_k or 0) or 5
        doc_ids = self.document_ids.ids
        hits = self.env["fts.ai.knowledge.document"].rag_search(query_text=query, document_ids=doc_ids, top_k=top_k)
        context = self.env["fts.ai.knowledge.document"].rag_context(query_text=query, document_ids=doc_ids, top_k=top_k)
        self.result_context = context
        self.result_hits = json.dumps(hits or [], ensure_ascii=False, indent=2)
        return self._reload()

    def action_ask(self):
        self.ensure_one()
        query = (self.query_text or "").strip()
        if not query:
            return self._reload()
        if not self.model_id:
            return self._reload()
        top_k = int(self.top_k or 0) or 5
        doc_ids = self.document_ids.ids
        data = self.model_id.rag_chat(query_text=query, document_ids=doc_ids, top_k=top_k)
        answer = ""
        if isinstance(data, dict):
            choices = data.get("choices")
            if isinstance(choices, list) and choices:
                choice = choices[0] if isinstance(choices[0], dict) else {}
                message = choice.get("message") if isinstance(choice, dict) else {}
                if isinstance(message, dict):
                    answer = message.get("content") or ""
        self.result_answer = answer or (json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else str(data))
        return self._reload()

    def _reload(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("RAG Test"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

