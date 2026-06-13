# -*- coding: utf-8 -*-

from odoo import _, models
from odoo.exceptions import ValidationError
from odoo.tools import html2plaintext


class FtsNrFlow(models.Model):
    _inherit = "fts.nr.flow"

    def action_sync_to_knowledge(self):
        Document = self.env["fts.ai.knowledge.document"]
        vals_list = []
        for record in self:
            description = html2plaintext(record.description or "").strip()
            lines = [
                f"Flow Name: {record.name or ''}",
                f"Flow ID: {record.nr_id or ''}",
                f"Flow Type: {record.type or ''}",
                "",
                "Flow Prompt:",
                (record.prompt or "").strip(),
                "",
                "Flow Description:",
                description,
                "",
                "Flow JSON:",
                (record.content or "").strip(),
            ]
            raw_text = "\n".join([line for line in lines if line is not None]).strip()
            if not raw_text:
                continue
            vals_list.append(
                {
                    "name": f"Flow: {record.name}",
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
                "message": _("Synchronized %(count)s flows to knowledge documents and completed vectorization.", count=len(created_records)),
                "sticky": False,
            },
        }

    def _build_atomic_user_text(self):
        self.ensure_one()
        lines = [
            f"Flow Name: {self.name or ''}",
            f"Flow ID: {self.nr_id or ''}",
            f"Flow Type: {self.type or ''}",
            "",
            "Flow Prompt:",
            self.prompt or "",
            "",
            "Flow Description:",
            self.description or "",
        ]
        return "\n".join(lines).strip()

    def _build_atomic_assistant_text(self):
        self.ensure_one()
        return self.content or ""

    def action_convert_to_atomic_messages(self):
        Prompt = self.env["fts.ai.prompt"]
        Message = self.env["fts.ai.dataset.message"]
        templates = Prompt.search([("is_template", "=", True)])
        if not templates:
            raise ValidationError(_("No template prompts were found. Please create at least one prompt with Template Prompt enabled."))
        created = Message.browse()
        for flow in self:
            user_text = flow._build_atomic_user_text()
            assistant_text = flow._build_atomic_assistant_text()
            for prompt in templates:
                values = {
                    "format": "ChatML",
                    "system": prompt.content or "",
                    "user": user_text,
                    "assistant": assistant_text,
                    "category_ids": [(6, 0, prompt.category_ids.ids)],
                }
                created |= Message.create(values)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Atomic Messages Created"),
                "message": _("%(count)s atomic messages were created from %(flow_count)s flows.", count=len(created), flow_count=len(self)),
                "type": "success",
                "sticky": False,
            },
        }
