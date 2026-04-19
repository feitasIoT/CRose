from odoo import models, fields, api


class DataAsset(models.Model):
    _name = 'fts.data.asset'
    _description = 'Data Asset'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="Name", required=True)
    partner_id = fields.Many2one("res.partner", string="Provider", required=True)
    position = fields.Char(string="Position")
    model = fields.Char(string="Model")
    data_model_ids = fields.One2many("fts.data.model", "data_asset_id", string="Data Models")
    data_modeling_ids = fields.Many2many("fts.data.model", string="Modelings", relation="rel_data_asset_modeling")
    model_count = fields.Integer(string="Model Count", compute="_compute_health", store=True)
    health_status = fields.Selection(
        [
            ("normal", "Normal"),
            ("abnormal", "Exceptional"),
            ("empty", "No Model"),
        ],
        string="Health Status",
        compute="_compute_health",
        store=True,
    )

    @api.depends("data_model_ids", "data_model_ids.data_status")
    def _compute_health(self):
        for record in self:
            models = record.data_model_ids
            record.model_count = len(models)
            if not models:
                record.health_status = "empty"
            elif any(m.data_status == "abnormal" for m in models):
                record.health_status = "abnormal"
            else:
                record.health_status = "normal"
