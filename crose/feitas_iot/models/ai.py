import zipfile
import io
import os
import base64
from .utils import EmbeddingManager

from odoo import models, fields, api, exceptions, _


class FtsAiModel(models.Model):
    _name = "fts.ai.model"
    _description = "AI Model"

    name = fields.Char(string=_("Name"), required=True)
    model_file = fields.Binary(_('Model Archive (.zip)'), required=True, help=_('Upload a zip archive created from the folder downloaded from HuggingFace.'))
    is_active = fields.Boolean(_('Active'), default=False)
    local_path = fields.Char(_('Local Extract Path'), compute='_compute_local_path')

    @api.depends('is_active')
    def _compute_local_path(self):
        for record in self:
            if record.id:
                record.local_path = os.path.join(self.env['ir.attachment']._storage(), 'ai_models', str(record.id))
            else:
                record.local_path = False

    @api.constrains('is_active')
    def _check_single_active(self):
        if self.search_count([('is_active', '=', True)]) > 1:
            raise exceptions.ValidationError(_("Only one model can be active at a time."))

    def action_deploy_model(self):
        """Extract the model archive to persistent storage."""
        self.ensure_one()
        base_path = self.local_path
        
        if not os.path.exists(base_path):
            os.makedirs(base_path)

        try:
            zip_data = base64.b64decode(self.model_file)
            with zipfile.ZipFile(io.BytesIO(zip_data), 'r') as zip_ref:
                zip_ref.extractall(base_path)
        except Exception as e:
            raise exceptions.UserError(_("Failed to extract the model archive: %(error)s", error=e))
        
        self.env['fts.ai.model'].search([('id', '!=', self.id)]).write({'is_active': False})
        self.write({'is_active': True})
        EmbeddingManager.clear_cache()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Deployment Successful'),
                'message': _('The model has been deployed to %(path)s and activated.', path=base_path),
                'sticky': False,
            }
        }


class FtsAiDataset(models.Model):
    _name = "fts.ai.dataset"
    _description = "Dataset"

    name = fields.Char(string=_("Name"), required=True)


class FtsAiTraining(models.Model):
    _name = "fts.ai.training"
    _description = "Model Training"

    name = fields.Char(string=_("Name"), required=True)
