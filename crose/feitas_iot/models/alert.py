# -*- coding: utf-8 -*-

from odoo import models, fields, api


class FtsAlert(models.Model):
    _name = "fts.alert"
    _description = "Alert"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "occurred_at desc, id desc"

    name = fields.Char(string="Title", required=True, tracking=True)
    source = fields.Selection(
        [
            ("collection", "Data Collection"),
            ("processing", "Data Processing"),
            ("device", "Device"),
            ("system", "System"),
        ],
        string="Source",
        required=True,
        default="collection",
        tracking=True,
    )
    severity = fields.Selection(
        [
            ("info", "Info"),
            ("warning", "Warning"),
            ("critical", "Critical"),
        ],
        string="Severity",
        required=True,
        default="warning",
        tracking=True,
    )
    state = fields.Selection(
        [
            ("open", "Open"),
            ("ack", "Acknowledged"),
            ("resolved", "Resolved"),
        ],
        string="Status",
        required=True,
        default="open",
        tracking=True,
    )
    message = fields.Text(string="Message", tracking=True)
    details = fields.Text(string="Details")
    occurred_at = fields.Datetime(string="Occurred At", default=fields.Datetime.now, required=True, index=True)
    resolved_at = fields.Datetime(string="Resolved At", readonly=True)
    write_date = fields.Datetime(string="Last Updated on")
    count = fields.Integer(string="Count", default=1)
    active = fields.Boolean(default=True)


    @api.model_create_multi
    def create(self, vals_list):
        if isinstance(vals_list, dict):
            vals_list = [vals_list]

        result_ids = []
        for vals in vals_list:
            vals = vals or {}
            name = vals.get("name")
            state = vals.get("state", "open")

            if name and state == "open":
                existing = self.search(
                    [
                        ("name", "=", name),
                        ("state", "=", "open"),
                    ],
                    limit=1,
                )
                if existing:
                    existing.count += 1
                    result_ids.append(existing.id)
                    continue

            created = super(FtsAlert, self).create(vals)
            result_ids.append(created.id)

        return self.browse(result_ids)

    def _get_similar_alerts_domain(self):
        self.ensure_one()
        return [
            ("source", "=", self.source),
            ("state", "=", self.state),
            ("message", "=", self.message or False),
        ]

    def action_acknowledge(self):
        for record in self:
            alerts = self.search(record._get_similar_alerts_domain())
            alerts.write({"state": "ack"})
        return True

    def action_close(self):
        now = fields.Datetime.now()
        for record in self:
            alerts = self.search(record._get_similar_alerts_domain())
            alerts.write(
                {
                    "state": "resolved",
                    "resolved_at": now,
                    "active": False,
                }
            )
        return True
