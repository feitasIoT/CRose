from odoo import models, api, tools
import threading
import requests
import json
import time
import logging

_logger = logging.getLogger(__name__)

class MailThread(models.AbstractModel):
    _inherit = 'mail.thread'

    def message_post(self, **kwargs):
        message = super().message_post(**kwargs)
        # Check if we need to trigger AI
        if not self.env.context.get('skip_ai_reply'):
             self._check_ai_reply(message)
        return message

    def _check_ai_reply(self, message):
        # Logic:
        # 1. Message is a comment (not notification)
        # 2. Deepseek user is involved (Channel member OR Mentioned/Recipient)
        # 3. Message is NOT from Deepseek
        
        if message.message_type != 'comment':
            return

        # Find Deepseek partner by email
        deepseek_email = 'deepseek@123.com'
        deepseek_partner = self.env['res.partner'].sudo().search([('email', '=', deepseek_email)], limit=1)
        
        if not deepseek_partner:
            # Try searching by name if email not found, or just log
            # _logger.warning("DeepSeek partner with email %s not found", deepseek_email)
            return

        # Check author
        if message.author_id.id == deepseek_partner.id:
            return

        should_reply = False
        
        # Scenario 1: Discuss Channel (Private/Group Chat)
        if self._name == 'discuss.channel':
             if deepseek_partner.id in self.channel_partner_ids.ids:
                 should_reply = True
        
        # Scenario 2: Chatter (Any other model, or even channel if mentioned explicitly)
        # Check if Deepseek is mentioned or notified
        if not should_reply and deepseek_partner.id in message.partner_ids.ids:
            should_reply = True

        if not should_reply:
            return

        # Prepare content before threading
        user_content = tools.html2plaintext(message.body or "")
        record_id = self.id
        model_name = self._name
        partner_id = deepseek_partner.id
        
        # Define callback to run after commit
        def trigger_ai_thread():
            _logger.info(f"DeepSeek: Triggering thread for model {model_name} record {record_id}")
            thread = threading.Thread(target=self._chat_with_ai_worker, args=(user_content, model_name, record_id, partner_id))
            thread.start()
            
        self.env.cr.postcommit.add(trigger_ai_thread)

    def _chat_with_ai_worker(self, user_content, model_name, record_id, deepseek_partner_id):
        _logger.info(f"DeepSeek: Thread started for {model_name} {record_id}")
        with self.pool.cursor() as new_cr:
            # Use sudo to ensure AI can reply
            self = self.with_env(self.env(cr=new_cr)).sudo()
            record = self.env[model_name].browse(record_id)
            deepseek_partner = self.env['res.partner'].browse(deepseek_partner_id)
            
            # Typing status only for channels
            deepseek_member = False
            if model_name == 'discuss.channel':
                deepseek_member = self.env['discuss.channel.member'].search([
                    ('channel_id', '=', record.id),
                    ('partner_id', '=', deepseek_partner.id)
                ], limit=1)
            
            # Config
            base_url = self.env['ir.config_parameter'].sudo().get_param('feitas_iot.deepseek_base_url', 'https://api.deepseek.com/v1')
            api_key = self.env['ir.config_parameter'].sudo().get_param('feitas_iot.deepseek_api_key')
            model = self.env['ir.config_parameter'].sudo().get_param('feitas_iot.deepseek_model', 'deepseek-chat')
            
            if not api_key:
                _logger.warning("DeepSeek: API Key not configured")
                record.with_context(skip_ai_reply=True).message_post(
                    body="System notice: DeepSeek API Key is not configured. Please contact the administrator.",
                    author_id=deepseek_partner.id,
                    message_type='comment',
                    subtype_xmlid='mail.mt_note'
                )
                self.env.cr.commit()
                return
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": user_content}
                ],
                "stream": True
            }

            # Notify typing start (if applicable)
            if deepseek_member:
                _logger.info("DeepSeek: Sending typing notification")
                deepseek_member._notify_typing(True)
                self.env.cr.commit()

            try:
                _logger.info("DeepSeek: Sending API request")
                response = requests.post(f"{base_url}/chat/completions", headers=headers, json=payload, stream=True, timeout=60)
                
                if response.status_code != 200:
                     error_msg = f"API Error: {response.status_code} - {response.text}"
                     _logger.error(f"DeepSeek: {error_msg}")
                     record.with_context(skip_ai_reply=True).message_post(
                        body=error_msg,
                        author_id=deepseek_partner.id,
                        message_type='comment',
                        subtype_xmlid='mail.mt_note'
                     )
                     self.env.cr.commit()
                     return

                full_content = ""
                last_typing_time = time.time()
                
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
                                
                                # Keep typing alive (every 3 seconds)
                                if deepseek_member and (time.time() - last_typing_time > 3.0):
                                    deepseek_member._notify_typing(True)
                                    self.env.cr.commit()
                                    last_typing_time = time.time()
                        except json.JSONDecodeError:
                            continue

                # Post final message
                _logger.info(f"DeepSeek: Posting reply (len={len(full_content)})")
                record.with_context(skip_ai_reply=True).message_post(
                    body=full_content,
                    author_id=deepseek_partner.id,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment'
                )
                self.env.cr.commit()
                
            except Exception as e:
                _logger.error(f"DeepSeek: Exception during chat: {e}")
                record.with_context(skip_ai_reply=True).message_post(
                    body=f"AI Error: {str(e)}",
                    author_id=deepseek_partner.id
                )
                self.env.cr.commit()
            finally:
                # Stop typing status
                if deepseek_member:
                    _logger.info("DeepSeek: Stopping typing notification")
                    deepseek_member._notify_typing(False)
                    self.env.cr.commit()
