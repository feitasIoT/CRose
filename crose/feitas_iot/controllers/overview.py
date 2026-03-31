from odoo import http
from odoo.http import request

class OverviewController(http.Controller):

    @http.route('/feitas_iot/get_component_status', type='jsonrpc', auth='user')
    def get_component_status(self):
        components = request.env['crose.component'].search_read(
            [],
            ['name', 'component_type', 'status']
        )
        return {
            'components': components
        }
