from odoo import models, fields, api, _


class FtsMqttUser(models.Model):
    _name = "fts.mqtt.user"
    _description = "MQTT User"

    name = fields.Char(string="Name", required=True)
    password = fields.Char(string="Password", required=True)
    broker_id = fields.Many2one("crose.component", string="MQTT Broker", required=True, domain=[('component_type', '=', 'mqtt')])
    partner_id = fields.Many2one("res.partner", string=_("Contact"))

    status = fields.Selection(
        [
            ("active", "Active"),
            ("pause", "Paused")
        ],
        string=_("Status"),
        default="active",
    )

    _name_partner_unique = models.Constraint(
        'unique(name, partner_id)',
        _('The combination of username and contact must be unique!')
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super(FtsMqttUser, self).create(vals_list)
        if not self.env.context.get('skip_broker_sync'):
            for record in records:
                if record.broker_id and record.name and record.password:
                    record.broker_id.api_create_users(record.name, record.password)
        return records
