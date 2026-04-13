# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class FtsKnowledge(models.Model):
    _name = 'fts.knowledge'
    _description = 'Knowledge Base for Node-RED'

    name = fields.Char(string='Name', required=True)
    description = fields.Text(string='Details')
    json_source = fields.Text(string='JSON Source')

    def action_vectorize(self):
        """Call the AI model to generate and store vectors."""
        from .utils import EmbeddingManager
        for record in self:
            if not record.json_source:
                continue
            text_to_vector = f"Name: {record.name}\nDescription: {record.description or ''}\nJSON: {record.json_source}"
            try:
                vector = EmbeddingManager.encode(self.env, text_to_vector)
                if vector:
                    record.save_vector(vector)
            except Exception as e:
                raise ValidationError(_("Vectorization failed: %(error)s", error=e))

    def _register_hook(self):
        """Ensure the database supports the vector extension and column."""
        self.env.cr.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        self.env.cr.execute("SELECT to_regclass(%s)", ("fts_knowledge",))
        row = self.env.cr.fetchone()
        if row and row[0]:
            self.env.cr.execute(
                """
                ALTER TABLE fts_knowledge
                ADD COLUMN IF NOT EXISTS vector_data vector(384);
                """
            )
        return super(FtsKnowledge, self)._register_hook()

    @api.model
    def search_similar_flows(self, query_vector, limit=3):
        """Search for similar flows using the pgvector distance operator."""
        vector_str = str(query_vector)

        sql = """
            SELECT id, name, json_source,
                   vector_data <-> %s AS distance
            FROM fts_knowledge
            WHERE vector_data IS NOT NULL
            ORDER BY distance ASC
            LIMIT %s
        """
        self.env.cr.execute(sql, (vector_str, limit))
        results = self.env.cr.dictfetchall()
        return results

    def save_vector(self, vector_list):
        """
        Update the vector value for the current record.
        """
        self.ensure_one()
        sql = "UPDATE fts_knowledge SET vector_data = %s WHERE id = %s"
        self.env.cr.execute(sql, (str(vector_list), self.id))
