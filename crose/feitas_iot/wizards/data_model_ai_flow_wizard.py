# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class FtsDataModelAiFlowWizard(models.TransientModel):
    _name = "fts.data.model.ai.flow.wizard"
    _description = "Data Model AI Flow Wizard"

    data_model_id = fields.Many2one("fts.data.model", string="Data Model", required=True, readonly=True)
    model_id = fields.Many2one(
        "fts.ai.model",
        string="Model",
        required=True,
        domain="[('model_type', 'in', ('vllm_base', 'vllm_adapter', 'provider'))]",
    )
    temperature = fields.Float(string="Temperature", default=0.1)
    max_tokens = fields.Integer(string="Max Tokens", default=4096)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_id = self.env.context.get("active_id")
        if active_id and "data_model_id" in fields_list:
            res["data_model_id"] = int(active_id)
        if "model_id" in fields_list and not res.get("model_id"):
            default_model = self.env["fts.ai.model"].search(
                [("model_type", "in", ("vllm_base", "vllm_adapter", "provider")), ("is_default", "=", True)],
                limit=1,
            )
            if default_model:
                res["model_id"] = default_model.id
        return res

    def action_generate(self):
        self.ensure_one()
        if not self.data_model_id:
            raise ValidationError(_("Data Model is required."))
        if not self.model_id:
            raise ValidationError(_("Model is required."))
        return self.data_model_id._action_generate_flow_ai_with_model(
            model=self.model_id,
            temperature=float(self.temperature or 0.1),
            max_tokens=int(self.max_tokens or 4096),
        )

