import threading
import time
import base64
import re
import requests
import json
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)



class FtsEdgeAgent(models.Model):
    _name = "fts.edge.agent"
    _description = "Edge Agent"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string=_("Name"), required=True)
    version = fields.Char(string=_("Version"))
    is_template = fields.Boolean(string=_("Is Template"))
    template_id = fields.Many2one("fts.edge.agent", string=_("Template"), domain="[('is_template', '=', True)]")

    ip_address = fields.Char(string=_("IP Address"))
    port = fields.Integer(string=_("Port"), default=6080)
    agent_port = fields.Integer(string=_("Agent Port"), default=18080)
    os_version = fields.Selection([('rasp', 'Raspberry'), ('ubuntu', 'Ubuntu')], string=_("OS Distribution"))
    npm_registry_id = fields.Many2one("crose.component", string=_("NPM Registry"), domain=[('component_type', '=', 'npm')])
    # MQTT Config
    mqtt_broker_id = fields.Many2one("crose.component", string="MQTT Broker", domain=[('component_type', '=', 'mqtt')])
    username = fields.Char(string=_("Username"))
    password = fields.Char(string=_("Password"))

    # Instance Config
    instance_id = fields.Many2one(
        "fts.nr.instance", 
        string=_("Related Instance"), 
        domain=[('instance_type', '=', 'local')],
        help=_("Only local instances can be selected.")
    )
    nr_node = fields.Text(string=_("NR Node"))

    config = fields.Text(_("Configuration File"))
    agent_cmd = fields.Text(_("Command"))
    status = fields.Selection(
        [
            ("online", "Online"),
            ("offline", "Offline"),
            ("error", "Error"),
        ],
        string=_("Status"),
        default="offline",
        required=True,
    )
    flow_ids = fields.One2many("agent.flow.line", "agent_id", string=_("Flows"))

#-------------onchange--------------

    @api.onchange('template_id')
    def _onchange_template_id(self):
        if self.template_id:
            self.version = self.template_id.version


#-------------actions--------------

    def message_post(self, **kwargs):
        message = super().message_post(**kwargs)
        
        # Check if we should trigger AI response
        # 1. Message is a comment
        # 2. Skip if explicitly asked to skip
        if kwargs.get('message_type') == 'comment' and not self.env.context.get('skip_ai_reply'):
            # Try to find AI partner via XML ID first, then name
            ai_partner = self.env.ref('feitas_iot.partner_ai_assistant', raise_if_not_found=False)
            if not ai_partner:
                ai_partner = self.env['res.partner'].sudo().search([('name', '=', 'AI Assistant')], limit=1)
            
            # Check if AI is mentioned (partner_ids OR text body)
            is_mentioned = False
            if ai_partner and ai_partner.id in message.partner_ids.ids:
                is_mentioned = True
            elif '@AI Assistant' in (message.body or ''):
                is_mentioned = True
            
            if is_mentioned:
                _logger.info(f"AI Assistant triggered for message {message.id}")
                
                # Check API Key immediately to give feedback
                api_key = self.env['ir.config_parameter'].sudo().get_param('feitas_iot.deepseek_api_key')
                if not api_key:
                    _logger.warning("DeepSeek API Key missing")
                    # Post warning to user
                    self.with_context(skip_ai_reply=True).message_post(
                        body=_("⚠️ System notice: the AI API key is not configured. Please set `feitas_iot.deepseek_api_key` in system parameters."),
                        message_type='comment',
                        subtype_xmlid='mail.mt_note'
                    )
                    return message
                
                if ai_partner:
                    # Use threading to avoid blocking the UI
                    # Register callback to run after commit to ensure message exists in DB for the new thread
                    def trigger_ai():
                        thread = threading.Thread(target=self._chat_with_ai_threaded, args=(message.id, ai_partner.id))
                        thread.start()
                    self.env.cr.postcommit.add(trigger_ai)
            
        return message

    def _chat_with_ai_threaded(self, message_id, ai_partner_id):
        """
        Threaded wrapper for AI chat
        """
        with self.pool.cursor() as new_cr:
            self = self.with_env(self.env(cr=new_cr))
            message = self.env['mail.message'].browse(message_id)
            ai_partner = self.env['res.partner'].browse(ai_partner_id)
            self._chat_with_ai(message, ai_partner)

    def _chat_with_ai(self, message, ai_partner):
        """
        Send message to LLM and post response (Streaming simulation)
        """
        _logger.info(f"Starting AI chat for message {message.id}")
        api_key = self.env['ir.config_parameter'].sudo().get_param('feitas_iot.deepseek_api_key')
        if not api_key:
            return

        base_url = self.env['ir.config_parameter'].sudo().get_param('feitas_iot.deepseek_base_url', 'https://api.deepseek.com')
        model = self.env['ir.config_parameter'].sudo().get_param('feitas_iot.deepseek_model', 'deepseek-chat')
        
        # Avoid replying to itself (double check)
        if message.author_id == ai_partner:
            return

        # 1. Post a placeholder "Thinking..." message immediately
        placeholder_content = "AI is thinking... <i class='fa fa-spinner fa-spin'></i>"
        reply_message = self.with_context(skip_ai_reply=True).message_post(
            body=placeholder_content,
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
            author_id=ai_partner.id,
            partner_ids=[] # Don't notify anyone for placeholder? Or maybe yes.
        )
        self.env.cr.commit() # Commit immediately to ensure "Thinking..." is visible via Bus

        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            }
            
            # Prepare conversation history
            # Ideally we should fetch previous messages in the thread
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant for IoT Edge Agent management."},
                    {"role": "user", "content": html2plaintext(message.body or "")} 
                ],
                "stream": True # Enable streaming
            }
            
            response = requests.post(f"{base_url}/chat/completions", headers=headers, json=payload, stream=True, timeout=60)
            
            if response.status_code != 200:
                 reply_message.write({'body': f"AI API Error: {response.status_code} - {response.text}"})
                 return

            full_content = ""
            last_update_time = time.time()
            
            # 2. Process stream
            for line in response.iter_lines():
                if not line:
                    continue
                
                line_text = line.decode('utf-8')
                if line_text.startswith("data: "):
                    data_str = line_text[6:]
                    if data_str == "[DONE]":
                        break
                    
                    try:
                        data = json.loads(data_str)
                        delta = data.get('choices', [{}])[0].get('delta', {}).get('content', '')
                        if delta:
                            full_content += delta
                            
                            # Update DB every 0.5 seconds to simulate streaming without killing DB
                            if time.time() - last_update_time > 0.5:
                                reply_message.write({'body': full_content + " <i class='fa fa-spinner fa-spin'></i>"})
                                self.env.cr.commit() # Commit to make visible to other transactions/UI
                                last_update_time = time.time()
                                
                    except json.JSONDecodeError:
                        continue

            # 3. Final update
            reply_message.write({'body': full_content})
            self.env.cr.commit()
                
        except Exception as e:
            _logger.error(f"Failed to call AI API: {str(e)}")
            reply_message.write({'body': f"AI Error: {str(e)}"})
            self.env.cr.commit()

    def action_view_logs(self):
        self.ensure_one()
        action = self.env.ref("feitas_iot.action_node_red_logs_client", raise_if_not_found=False)
        if action:
            res = action.read()[0]
            res["params"] = {"agent_id": self.id, "title": _("Logs - %(name)s", name=self.name)}
            return res

    def action_generate_config(self):
        self.ensure_one()
        Attachment = self.env["ir.attachment"].sudo()
        attachment = Attachment.search([("name", "=", "config.yaml.template")], order="id desc", limit=1)
        if not attachment:
            raise UserError(_("Attachment not found: config.yaml.template"))

        data = b""
        if attachment.store_fname:
            try:
                data = attachment._file_read(attachment.store_fname)
            except Exception:
                data = b""
        elif attachment.datas:
            try:
                data = base64.b64decode(attachment.datas)
            except Exception:
                data = b""

        try:
            template_text = data.decode("utf-8")
        except Exception:
            template_text = data.decode("utf-8", errors="ignore")

        if not template_text.strip():
            raise UserError(_("The template content is empty: config.yaml.template"))

        def _placeholder_value(field_name):
            if not hasattr(self, field_name):
                return ""
            value = getattr(self, field_name)
            if value is None or value is False:
                return ""
            if isinstance(value, models.BaseModel):
                return value.display_name or ""
            return str(value)

        pattern = re.compile(r"\{\{\s*(?:record\.)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
        rendered = pattern.sub(lambda m: _placeholder_value(m.group(1)), template_text)
        self.config = rendered

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Generation Complete"),
                "message": _("The configuration has been generated from config.yaml.template."),
                "type": "success",
                "sticky": False,
            },
        }

    def action_open_vnc(self):
        self.ensure_one()
        action = self.env.ref("feitas_iot.action_node_red_editor_client", raise_if_not_found=False)
        if action:
            res = action.read()[0]
            res["display_name"] = _("Remote Desktop")
            res["name"] = _("Remote Desktop")
            res["params"] = {
                "node_red_url": f"http://{self.ip_address}:{self.port}/vnc.html",
            }
            return res
        return {}

    def action_view_instances(self):
        self.ensure_one()
        action = self.env.ref("feitas_iot.action_fts_nr_instance", raise_if_not_found=False)
        if action:
            res = action.read()[0]
            res["display_name"] = _("Instances")
            res["name"] = _("Instances")
            res["domain"] = [("edge_agent_id", "=", self.id)]
            res["context"] = {
                "default_edge_agent_id": self.id,
            }
            return res
        return {}
