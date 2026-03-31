# -*- coding: utf-8 -*-
import os
import logging

from odoo import exceptions, _
import base64

_logger = logging.getLogger(__name__)

import requests
import json

_logger = logging.getLogger(__name__)

class EmbeddingManager:
    @classmethod
    def clear_cache(cls):
        """No local cache needs to be cleared because a remote AI service is used."""
        pass

    @classmethod
    def encode(cls, env, text):
        """Call the remote AI service to generate embeddings."""
        ai_endpoint = env['ir.config_parameter'].sudo().get_param('crose_iot.ai_endpoint', 'http://crose-ai:8000/embed')
        
        try:
            response = requests.post(ai_endpoint, json={'text': text}, timeout=10)
            response.raise_for_status()
            result = response.json()
            return result.get('vector', [])
        except Exception as e:
            _logger.error(f"Failed to get embedding from AI service: {e}")
            raise exceptions.UserError(_("AI embedding service call failed: %(error)s", error=e))
