import base64
import io
import json
import zipfile

import requests

from odoo import http
from odoo.http import request, content_disposition
from odoo.exceptions import UserError
from odoo.tools.translate import _


class FeitasIotDownloadController(http.Controller):

    def _has_attachment_access(self, attachment, token):
        return bool(attachment.public) or bool(token and attachment.access_token and token == attachment.access_token)

    @http.route("/crose/agent/<string:filename>", type="http", auth="public", methods=["GET"], csrf=False)
    def download_attachment_by_filename(self, filename, **kwargs):
        if not filename or "/" in filename or "\\" in filename or ".." in filename:
            return request.not_found()

        Attachment = request.env["ir.attachment"].sudo()
        attachment = Attachment.search([("name", "=", filename)], order="id desc", limit=1)
        if not attachment:
            return request.not_found()

        token = kwargs.get("access_token") or kwargs.get("token")
        if not self._has_attachment_access(attachment, token):
            return request.not_found()

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

        headers = [
            ("Content-Type", attachment.mimetype or "application/octet-stream"),
            ("Content-Disposition", content_disposition(filename)),
        ]
        return request.make_response(data, headers=headers)

    @http.route(["/crose/agent/<int:agentid>", "/crose/agent/<int:agentid>/crose_agent.zip"], type="http", auth="public", methods=["GET"], csrf=False)
    def download_bundle(self, agentid, **kwargs):
        """Download multiple files and package them into crose_agent.zip."""
        Agent = request.env["fts.edge.agent"].sudo()
        agent = Agent.browse(agentid)
        if not agent.exists():
            return request.not_found()

        # Files to include in the bundle
        # 1. config.yaml (generated from agent.config)
        # 2. installer.sh
        # 3. crose_agent
        # 4. croseagent.service

        Attachment = request.env["ir.attachment"].sudo()
        
        def _read_data(attachment):
            if attachment.store_fname:
                try:
                    return attachment._file_read(attachment.store_fname)
                except Exception:
                    return b""
            if attachment.datas:
                try:
                    return base64.b64decode(attachment.datas)
                except Exception:
                    return b""
            return b""

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # 1. Add config.yaml
            config_content = (agent.config or "").strip()
            if config_content:
                zf.writestr("config.yaml", config_content.encode("utf-8"))

            # 2. Add other files from attachments
            # Priority: Attachments on the specific agent record > Global attachments (for installer.sh)
            # We search for files attached to this agent first.
            
            target_files = ["installer.sh", "crose_agent", "croseagent.service"]
            # Also check for 'agent' as an alias for 'crose_agent' as user mentioned uploading 'agent'
            
            found_files = set()

            # Search attachments linked to this agent
            agent_attachments = Attachment.search([
                ("res_model", "=", "fts.edge.agent"),
                ("res_id", "=", agent.id),
                ("name", "in", target_files + ["agent"])
            ])
            
            for att in agent_attachments:
                filename = att.name
                if filename == "agent": # Alias mapping
                    filename = "crose_agent"
                
                if filename not in found_files:
                    zf.writestr(filename, _read_data(att))
                    found_files.add(filename)

        data = zip_buffer.getvalue()
        headers = [
            ("Content-Type", "application/zip"),
            ("Content-Disposition", content_disposition("crose_agent.zip")),
        ]
        return request.make_response(data, headers=headers)

    @http.route("/spreadsheet/xlsx", type="http", auth="user", methods=["POST"], csrf=False)
    def download_spreadsheet_xlsx(self, zip_name=None, files=None, **kwargs):
        upload = files or request.httprequest.files.get("files")
        if not upload:
            return request.not_found()
        spreadsheet_files = json.loads(upload.read().decode())
        content = request.env["spreadsheet.mixin"]._zip_xslx_files(spreadsheet_files)
        headers = [
            ("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("Content-Disposition", content_disposition(zip_name or "spreadsheet.xlsx")),
        ]
        return request.make_response(content, headers=headers)

    @http.route("/feitas_iot/nodered/logs", type="jsonrpc", auth="user", methods=["POST"], csrf=False)
    def nodered_logs(self, instance_id=None, agent_id=None, cursor=None, limit=200, **kwargs):
        if not instance_id and not agent_id:
            raise UserError(_("Missing parameter: instance_id or agent_id"))

        if instance_id:
            try:
                instance_id = int(instance_id)
            except Exception:
                raise UserError(_("Invalid parameter: instance_id"))

            Instance = request.env["fts.nr.instance"]
            rec = Instance.browse(instance_id).exists()
            if not rec:
                raise UserError(_("Instance does not exist"))
            rec.check_access_rights("read")
            rec.check_access_rule("read")

            agent = rec.edge_agent_id
            if not agent:
                raise UserError(_("This instance has no edge agent configured, so runtime logs cannot be read."))
            host = (agent.ip_address or "").strip()
            port = int(agent.agent_port or 18080)
            identifier = (rec.name or "").strip() or str(rec.id)
        else:
            try:
                agent_id = int(agent_id)
            except Exception:
                raise UserError(_("Invalid parameter: agent_id"))

            Agent = request.env["fts.edge.agent"]
            agent = Agent.browse(agent_id).exists()
            if not agent:
                raise UserError(_("Agent does not exist"))
            agent.check_access_rights("read")
            agent.check_access_rule("read")

            host = (agent.ip_address or "").strip()
            port = int(agent.agent_port or 18080)
            identifier = "agent"

        if not host:
            raise UserError(_("Agent address is not configured"))

        url = f"http://{host}:{port}/v1/nodered/logs"
        params = {
            "identifier": identifier,
            "cursor": cursor or "",
            "limit": int(limit or 200),
        }
        token = (request.env["ir.config_parameter"].sudo().get_param("feitas_iot.agent_http_token") or "").strip()
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
        except Exception as e:
            raise UserError(_("Failed to read logs: %(error)s", error=str(e)))

        lines = data.get("lines") if isinstance(data, dict) else None
        if not isinstance(lines, list):
            lines = []
        next_cursor = ""
        if isinstance(data, dict):
            next_cursor = data.get("next_cursor") or ""
        return {
            "lines": lines,
            "next_cursor": next_cursor,
        }
