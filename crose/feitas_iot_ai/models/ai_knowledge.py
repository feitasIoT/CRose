# -*- coding: utf-8 -*-

import re

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class FtsAiKnowledgeDocument(models.Model):
    _name = "fts.ai.knowledge.document"
    _description = "AI Knowledge Document"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(string="Name", required=True, tracking=True)
    source_type = fields.Selection(
        [
            ("text", "Text"),
        ],
        string="Source Type",
        default="text",
        required=True,
        tracking=True,
    )
    raw_text = fields.Text(string="Content", tracking=True)
    chunk_size = fields.Integer(string="Chunk Size", default=800, required=True)
    chunk_overlap = fields.Integer(string="Chunk Overlap", default=120, required=True)
    chunk_ids = fields.One2many("fts.ai.knowledge.chunk", "document_id", string="Chunks")
    chunk_count = fields.Integer(string="Chunk Count", compute="_compute_chunk_counts", store=True)
    vectorized_chunk_count = fields.Integer(string="Vectorized Chunks", compute="_compute_chunk_counts", store=True)

    @api.depends("chunk_ids", "chunk_ids.is_vectorized")
    def _compute_chunk_counts(self):
        for record in self:
            record.chunk_count = len(record.chunk_ids)
            record.vectorized_chunk_count = len(record.chunk_ids.filtered("is_vectorized"))

    def _split_text(self, text, chunk_size, chunk_overlap):
        text = (text or "").strip()
        if not text:
            return []

        normalized = re.sub(r"\r\n?", "\n", text)
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", normalized) if p.strip()]

        chunks = []
        buf = ""
        for para in paragraphs:
            candidate = (buf + "\n\n" + para).strip() if buf else para
            if len(candidate) <= chunk_size:
                buf = candidate
                continue
            if buf:
                chunks.append(buf)
                buf = ""
            if len(para) <= chunk_size:
                buf = para
                continue
            start = 0
            while start < len(para):
                end = min(start + chunk_size, len(para))
                chunks.append(para[start:end].strip())
                if end >= len(para):
                    break
                start = max(0, end - chunk_overlap)

        if buf:
            chunks.append(buf)

        merged = []
        prev = ""
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            if prev and chunk_overlap and len(prev) >= chunk_overlap:
                overlap_text = prev[-chunk_overlap:]
                if not chunk.startswith(overlap_text):
                    chunk = (overlap_text + chunk).strip()
            merged.append(chunk)
            prev = chunk
        return merged

    def action_split(self):
        for record in self:
            if record.chunk_size <= 0:
                raise ValidationError(_("Chunk Size must be greater than 0."))
            if record.chunk_overlap < 0:
                raise ValidationError(_("Chunk Overlap cannot be negative."))
            if record.chunk_overlap >= record.chunk_size:
                raise ValidationError(_("Chunk Overlap must be smaller than Chunk Size."))
            if not record.raw_text:
                raise ValidationError(_("Please provide Content before splitting."))

            record.chunk_ids.unlink()
            parts = record._split_text(record.raw_text, record.chunk_size, record.chunk_overlap)
            Chunk = self.env["fts.ai.knowledge.chunk"]
            vals_list = []
            for i, content in enumerate(parts, start=1):
                vals_list.append(
                    {
                        "document_id": record.id,
                        "sequence": i,
                        "name": f"{record.name} #{i}",
                        "content": content,
                        "is_vectorized": False,
                    }
                )
            if vals_list:
                Chunk.create(vals_list)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Split Complete"),
                "message": _("Chunks were generated."),
                "type": "success",
                "sticky": False,
            },
        }

    def action_vectorize_chunks(self):
        for record in self:
            record.chunk_ids.filtered(lambda r: not r.is_vectorized).action_vectorize()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Vectorization Complete"),
                "message": _("Chunks were vectorized."),
                "type": "success",
                "sticky": False,
            },
        }

    def action_split_and_vectorize(self):
        self.action_split()
        return self.action_vectorize_chunks()

    @api.model
    def rag_search(self, query_text, document_ids=None, top_k=5):
        query_text = (query_text or "").strip()
        if not query_text:
            return []
        doc_ids = [int(x) for x in (document_ids or []) if int(x) > 0]
        return self.env["fts.ai.knowledge.chunk"].rag_search(query_text=query_text, document_ids=doc_ids, top_k=top_k)

    @api.model
    def rag_context(self, query_text, document_ids=None, top_k=5, separator="\n\n---\n\n"):
        results = self.rag_search(query_text=query_text, document_ids=document_ids, top_k=top_k)
        texts = []
        for item in results:
            title = item.get("document_name") or ""
            seq = item.get("sequence") or ""
            header = f"[{title} #{seq}]".strip()
            body = (item.get("content") or "").strip()
            if header and body:
                texts.append(f"{header}\n{body}")
            elif body:
                texts.append(body)
        return separator.join(texts)


class FtsAiKnowledgeChunk(models.Model):
    _name = "fts.ai.knowledge.chunk"
    _description = "AI Knowledge Chunk"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "document_id, sequence"

    name = fields.Char(string="Name", required=True, tracking=True)
    document_id = fields.Many2one("fts.ai.knowledge.document", string="Document", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(string="Sequence", required=True, index=True)
    content = fields.Text(string="Content", required=True)
    is_vectorized = fields.Boolean(string="Vectorized", default=False, tracking=True, index=True)

    def _ensure_vector_column(self):
        self.env.cr.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        self.env.cr.execute("SELECT to_regclass(%s)", ("fts_ai_knowledge_chunk",))
        row = self.env.cr.fetchone()
        exists = bool(row and row[0])
        if exists:
            self.env.cr.execute(
                """
                ALTER TABLE fts_ai_knowledge_chunk
                ADD COLUMN IF NOT EXISTS vector_data vector(384);
                """
            )

    def _register_hook(self):
        self._ensure_vector_column()
        return super()._register_hook()

    def action_vectorize(self):
        from .utils import EmbeddingManager

        for record in self:
            if not record.content:
                continue
            if record.is_vectorized:
                continue
            vector = EmbeddingManager.encode(self.env, record.content)
            if vector:
                record.save_vector(vector)

    def save_vector(self, vector_list):
        self.ensure_one()
        self._ensure_vector_column()
        sql = "UPDATE fts_ai_knowledge_chunk SET vector_data = %s WHERE id = %s"
        self.env.cr.execute(sql, (str(vector_list), self.id))
        self.is_vectorized = True

    @api.model
    def rag_search(self, query_text, document_ids=None, top_k=5):
        from .utils import EmbeddingManager

        query_text = (query_text or "").strip()
        if not query_text:
            return []
        top_k = int(top_k or 0)
        if top_k <= 0:
            top_k = 5

        vector = EmbeddingManager.encode(self.env, query_text)
        if not vector:
            return []
        self._ensure_vector_column()
        vector_str = str(vector)

        doc_ids = [int(x) for x in (document_ids or []) if int(x) > 0]
        params = [vector_str]
        where = "WHERE c.is_vectorized IS TRUE AND c.vector_data IS NOT NULL"
        if doc_ids:
            where += " AND c.document_id = ANY(%s)"
            params.append(doc_ids)
        params.append(top_k)

        sql = f"""
            SELECT
                c.id,
                c.document_id,
                d.name AS document_name,
                c.sequence,
                c.content,
                c.vector_data <-> %s AS distance
            FROM fts_ai_knowledge_chunk c
            JOIN fts_ai_knowledge_document d ON d.id = c.document_id
            {where}
            ORDER BY distance ASC
            LIMIT %s
        """
        self.env.cr.execute(sql, tuple(params))
        return self.env.cr.dictfetchall()
