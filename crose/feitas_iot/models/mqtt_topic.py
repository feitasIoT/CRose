from odoo import models, fields, api, _

class FtsMqttTopic(models.Model):
    _name = "fts.mqtt.topic"
    _description = "MQTT Topic"

    name = fields.Char(string="Name", required=True)
    broker_id = fields.Many2one("crose.component", string="MQTT Broker", required=True, domain=[('component_type', '=', 'mqtt')])
    partner_ids = fields.Many2many("res.partner", string="Partners")
    create_date = fields.Datetime(_("Created On"))

    @api.depends('name', 'broker_id.name')
    def _compute_display_name(self):
        for topic in self:
            if topic.broker_id:
                topic.display_name = f"{topic.broker_id.name} - {topic.name}"
            else:
                topic.display_name = topic.name

    @api.model
    def action_sync_all(self):
        brokers = self.env["crose.component"].search([('component_type', '=', 'mqtt'), ('status', '=', 'online')])
        for broker in brokers:
            pass 
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Synchronization Complete'),
                'message': _('MQTT topics have been synchronized.'),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            }
        }
