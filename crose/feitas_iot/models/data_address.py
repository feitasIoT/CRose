# -*- coding: utf-8 -*-

from odoo import models, fields, _


class FtsDataAddress(models.Model):
    _name = 'fts.data.address'
    _description = 'Data Model Address'

    model_id = fields.Many2one('fts.data.model', string='Data Model', required=True, ondelete='cascade')
    unitid = fields.Char(string=_('Unit ID'))
    address = fields.Char(string=_('Address'))
    length = fields.Integer(string=_('Length'))
