# -*- coding: utf-8 -*-

from odoo import _, models


class FtsNrNode(models.Model):
    _inherit = "fts.nr.node"

    def action_sync_to_knowledge(self):
        Document = self.env["fts.ai.knowledge.document"]
        vals_list = []
        for record in self:
            flow_name = record.flow_id.name if record.flow_id else ""
            lines = [
                f"Node Name: {record.name or ''}",
                f"Node ID: {record.nr_id or ''}",
                f"Node Type: {record.node_type or ''}",
                f"Flow: {flow_name or ''}",
                "",
                "Node JSON:",
                (record.content or "").strip(),
            ]
            raw_text = "\n".join([line for line in lines if line is not None]).strip()
            if not raw_text:
                continue
            vals_list.append(
                {
                    "name": f"Node: {record.name}",
                    "source_type": "text",
                    "raw_text": raw_text,
                }
            )
        created_records = Document.create(vals_list) if vals_list else Document.browse()
        if created_records:
            created_records.action_split_and_vectorize()

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Synchronization Successful"),
                "message": _("Synchronized %(count)s nodes to knowledge documents and completed vectorization.", count=len(created_records)),
                "sticky": False,
            },
        }
