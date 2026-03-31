# -*- coding: utf-8 -*-

from odoo import models, fields, _


class ResPartner(models.Model):
    _inherit = 'res.partner'

    mqtt_username = fields.Char(string=_('MQTT Username'), prefetch=False)
