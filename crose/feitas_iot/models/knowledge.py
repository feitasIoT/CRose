# -*- coding: utf-8 -*-

from odoo import models, fields, api, _


class FtsKnowledge(models.Model):
    _name = 'fts.knowledge'
    _description = 'Knowledge Base for Node-RED'

    name = fields.Char(string=_('Name'), required=True)
    description = fields.Text(string=_('Details'))
    json_source = fields.Text(string=_('JSON Source'))
    
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
                raise models.ValidationError(_("Vectorization failed: %(error)s", error=e))

    def _register_hook(self):
        """Ensure the database supports the vector extension and column."""
        query = """
            CREATE EXTENSION IF NOT EXISTS vector;
            ALTER TABLE fts_knowledge 
            ADD COLUMN IF NOT EXISTS vector_data vector(384);
        """
        self.env.cr.execute(query)
        return super(FtsKnowledge, self)._register_hook()

    @api.model
    def search_similar_flows(self, query_vector, limit=3):
        """Search for similar flows using the pgvector distance operator."""
        vector_str = str(query_vector)
        
        sql = """
            SELECT id, name, flow_json, 
                   vector_data <-> %s AS distance
            FROM fts_knowledge
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
