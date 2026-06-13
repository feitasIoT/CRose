import json
import logging
import threading
import time

import requests

from odoo import _, models
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)


class FtsEdgeNode(models.Model):
    _inherit = "fts.edge.node"

    def message_post(self, **kwargs):
        message = super().message_post(**kwargs)

        if kwargs.get("message_type") == "comment" and not self.env.context.get("skip_ai_reply"):
            ai_partner = self.env.ref("feitas_iot_ai.partner_ai_assistant", raise_if_not_found=False)
            if not ai_partner:
                ai_partner = self.env["res.partner"].sudo().search([("name", "=", "AI Assistant")], limit=1)

            is_mentioned = False
            if ai_partner and ai_partner.id in message.partner_ids.ids:
                is_mentioned = True
            elif "@AI Assistant" in (message.body or ""):
                is_mentioned = True

            if is_mentioned:
                _logger.info("AI Assistant triggered for message %s", message.id)
                api_key = self.env["ir.config_parameter"].sudo().get_param("feitas_iot.deepseek_api_key")
                if not api_key:
                    _logger.warning("DeepSeek API Key missing")
                    self.with_context(skip_ai_reply=True).message_post(
                        body=_("System notice: the AI API key is not configured. Please set `feitas_iot.deepseek_api_key` in system parameters."),
                        message_type="comment",
                        subtype_xmlid="mail.mt_note",
                    )
                    return message

                if ai_partner:

                    def trigger_ai():
                        thread = threading.Thread(target=self._chat_with_ai_threaded, args=(message.id, ai_partner.id))
                        thread.start()

                    self.env.cr.postcommit.add(trigger_ai)

        return message

    def _chat_with_ai_threaded(self, message_id, ai_partner_id):
        with self.pool.cursor() as new_cr:
            self = self.with_env(self.env(cr=new_cr))
            message = self.env["mail.message"].browse(message_id)
            ai_partner = self.env["res.partner"].browse(ai_partner_id)
            self._chat_with_ai(message, ai_partner)

    def _chat_with_ai(self, message, ai_partner):
        _logger.info("Starting AI chat for message %s", message.id)
        api_key = self.env["ir.config_parameter"].sudo().get_param("feitas_iot.deepseek_api_key")
        if not api_key:
            return

        base_url = self.env["ir.config_parameter"].sudo().get_param("feitas_iot.deepseek_base_url", "https://api.deepseek.com")
        model = self.env["ir.config_parameter"].sudo().get_param("feitas_iot.deepseek_model", "deepseek-chat")

        if message.author_id == ai_partner:
            return

        placeholder_content = "AI is thinking... <i class='fa fa-spinner fa-spin'></i>"
        reply_message = self.with_context(skip_ai_reply=True).message_post(
            body=placeholder_content,
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
            author_id=ai_partner.id,
            partner_ids=[],
        )
        self.env.cr.commit()

        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant for IoT Edge Agent management."},
                    {"role": "user", "content": html2plaintext(message.body or "")},
                ],
                "stream": True,
            }

            response = requests.post(f"{base_url}/chat/completions", headers=headers, json=payload, stream=True, timeout=60)

            if response.status_code != 200:
                reply_message.write({"body": f"AI API Error: {response.status_code} - {response.text}"})
                return

            full_content = ""
            last_update_time = time.time()
            for line in response.iter_lines():
                if not line:
                    continue
                line_text = line.decode("utf-8")
                if line_text.startswith("data: "):
                    data_str = line_text[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        delta = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if delta:
                            full_content += delta
                            if time.time() - last_update_time > 0.5:
                                reply_message.write({"body": full_content + " <i class='fa fa-spinner fa-spin'></i>"})
                                self.env.cr.commit()
                                last_update_time = time.time()
                    except json.JSONDecodeError:
                        continue

            reply_message.write({"body": full_content})
            self.env.cr.commit()
        except Exception as error:
            _logger.error("Failed to call AI API: %s", error)
            reply_message.write({"body": f"AI Error: {str(error)}"})
            self.env.cr.commit()
