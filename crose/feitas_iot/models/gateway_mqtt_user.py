import base64
import hashlib
from urllib.parse import quote

import requests

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError

HASH_SNAPSHOT_PREFIXES = ("hash$", "sha256$", "md5$", "bcrypt$")
ENCRYPTION_SECRET_PARAM = "feitas_iot.encryption_secret"
LEGACY_ENCRYPTION_SECRET_PARAM = "database.secret"


class FtsGatewayMqttUser(models.Model):
    _name = "fts.gateway.mqtt.user"
    _description = "Gateway MQTT User"
    _order = "write_date desc, id desc"

    gateway_id = fields.Many2one(
        "fts.edge.node",
        string="Gateway",
        required=True,
        ondelete="cascade",
        domain=[("is_gateway", "=", True)],
    )
    edge_node_id = fields.Many2one(
        "fts.edge.node",
        string="Edge Node",
        ondelete="set null",
        domain=[("is_gateway", "=", False)],
    )
    instance_id = fields.Many2one("fts.nr.instance", string="Node-RED Instance", ondelete="set null")
    username = fields.Char(string="Username", required=True)
    password_encrypted = fields.Char(string="Encrypted Password", required=True)
    modified_by = fields.Many2one("res.users", string="Modified By", related="write_uid", readonly=True)
    modified_at = fields.Datetime(string="Modified At", related="write_date", readonly=True)

    _gateway_username_unique = models.Constraint(
        "unique(gateway_id, username)",
        message="The username already exists in this gateway.",
    )

    def _get_cipher(self):
        try:
            from cryptography.fernet import Fernet
        except Exception as e:
            raise UserError(_("Missing dependency cryptography: %(error)s", error=str(e)))
        config = self.env["ir.config_parameter"].sudo()
        secret = (config.get_param(ENCRYPTION_SECRET_PARAM) or "").strip()
        if not secret:
            secret = (config.get_param(LEGACY_ENCRYPTION_SECRET_PARAM) or "").strip()
        if not secret:
            raise UserError(
                _(
                    "Missing encryption secret in system parameter %(param)s.",
                    param=ENCRYPTION_SECRET_PARAM,
                )
            )
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        return Fernet(key)

    def _decrypt_password(self, value):
        if not value:
            return ""
        text = str(value).strip()
        if not text:
            return ""
        if text.startswith("enc$"):
            token = text[4:]
            try:
                return self._get_cipher().decrypt(token.encode("utf-8")).decode("utf-8")
            except Exception:
                return ""
        if text.startswith(HASH_SNAPSHOT_PREFIXES):
            return ""
        return text

    def _encrypt_password(self, value):
        if not value:
            return value
        text = str(value)
        if text.startswith("enc$") or text.startswith(HASH_SNAPSHOT_PREFIXES):
            return text
        token = self._get_cipher().encrypt(text.encode("utf-8")).decode("utf-8")
        return f"enc${token}"

    def _get_plain_password(self):
        self.ensure_one()
        return self._decrypt_password(self.password_encrypted)

    def _gmqtt_account_endpoint(self):
        self.ensure_one()
        gateway = self.gateway_id
        if not gateway:
            raise UserError(_("Gateway is required."))
        username = (self.username or "").strip()
        if not username:
            raise UserError(_("Username is required."))
        return f"{gateway._build_gateway_gmqtt_api_base_url()}/v1/accounts/{quote(username)}"

    def _sync_create_to_gmqtt(self):
        self.ensure_one()
        password = self._get_plain_password()
        if not password:
            raise UserError(_("Plain password is required to create a gateway MQTT user in GMQTT."))
        response = requests.post(
            self._gmqtt_account_endpoint(),
            json={"password": password},
            timeout=15,
        )
        response.raise_for_status()

    def _sync_delete_to_gmqtt(self):
        self.ensure_one()
        response = requests.delete(
            self._gmqtt_account_endpoint(),
            timeout=15,
        )
        if response.status_code == 404:
            return True
        response.raise_for_status()
        return True

    def action_show_password(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only system administrators can view passwords."))
        plain_password = self._get_plain_password() or _("(empty)")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Password"),
                "message": _("%(username)s: %(password)s", username=self.username, password=plain_password),
                "type": "warning",
                "sticky": False,
            },
        }

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if "password_encrypted" in vals:
                vals["password_encrypted"] = self._encrypt_password(vals.get("password_encrypted"))
        records = super().create(vals_list)
        for record in records:
            record._sync_create_to_gmqtt()
        return records

    def write(self, vals):
        if "password_encrypted" in vals:
            vals["password_encrypted"] = self._encrypt_password(vals.get("password_encrypted"))
        return super().write(vals)

    def unlink(self):
        for record in self:
            record._sync_delete_to_gmqtt()
        return super().unlink()
