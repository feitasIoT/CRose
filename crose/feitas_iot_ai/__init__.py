from . import models
from . import wizards

# Monkey patch Odoo's DEFAULT_MAX_CONTENT_LENGTH to allow larger AI model uploads.
import logging
from odoo import http

_logger = logging.getLogger(__name__)

http.DEFAULT_MAX_CONTENT_LENGTH = 1024 * 1024 * 1024
_logger.info("CRose AI: Monkey patched odoo.http.DEFAULT_MAX_CONTENT_LENGTH to 1GB")

try:
    original_init = http.HTTPRequest.__init__

    def new_http_request_init(self, environ):
        original_init(self, environ)
        self.max_content_length = http.DEFAULT_MAX_CONTENT_LENGTH

    http.HTTPRequest.__init__ = new_http_request_init
    _logger.info("CRose AI: Monkey patched odoo.http.HTTPRequest.__init__ to use 1GB limit")
except Exception as error:
    _logger.error("CRose AI: Failed to monkey patch HTTPRequest: %s", error)
