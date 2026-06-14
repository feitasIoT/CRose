import re

from odoo import models, fields, api
from odoo.exceptions import UserError


PLACEHOLDER_PATTERN = re.compile(r"\{\{.*?\}\}")


class FtsNrInstanceWizard(models.TransientModel):
    _name = "fts.nr.instance.wizard"
    _description = "Manage Instance Flows"

    operation = fields.Selection(
        [
            ("add", "Add Flows from Template"),
            ("disable", "Disable Flows"),
            ("delete", "Delete Flows"),
        ],
        string="Operation",
        required=True,
        default="add",
    )
    instance_id = fields.Many2one(
        "fts.nr.instance",
        string="Instance",
        required=True,
        ondelete="cascade",
        default=lambda self: self.env.context.get("active_id"),
    )
    template_flow_ids = fields.Many2many(
        "fts.nr.flow",
        "wizard_instance_template_rel",
        "wizard_id",
        "flow_id",
        string="Template Flows",
        domain=[("is_template", "=", True)],
    )
    target_flow_ids = fields.Many2many(
        "fts.nr.flow",
        "wizard_instance_target_rel",
        "wizard_id",
        "flow_id",
        string="Target Flows",
        domain="[('instance_id', '=', instance_id), ('is_template', '=', False)]",
    )

    # -------- Parameter preview fields --------
    preview_param_ids = fields.One2many(
        "fts.nr.instance.wizard.param",
        "wizard_id",
        string="Parameters Preview",
    )

    @api.onchange("template_flow_ids")
    def _onchange_template_flow_ids(self):
        """Auto-refresh parameter preview when templates change."""
        instance = self.instance_id
        if not instance or not self.template_flow_ids:
            self.preview_param_ids.unlink()
            return

        results = []
        for tmpl in self.template_flow_ids:
            preview = instance._nr_preview_flow_params(tmpl, instance)
            for item in preview:
                results.append((0, 0, {
                    "template_flow_id": tmpl.id,
                    "flow_name": tmpl.display_name,
                    "param_name": item["name"],
                    "template_value": item["value"],
                    "resolved_value": str(item["resolved_value"]) if item["resolved_value"] is not None else "",
                    "param_type": item["type"],
                }))

        self.preview_param_ids = False  # clear through the ORM
        self.preview_param_ids = results

    def _check_unresolved_placeholders(self):
        """Raise UserError if any template_value contains {{ }} but resolved_value is empty."""
        unresolved = []
        for p in self.preview_param_ids:
            # If template has placeholder but resolved value is empty → unresolved
            if PLACEHOLDER_PATTERN.search(p.template_value or "") and not (p.resolved_value or "").strip():
                unresolved.append(
                    f"  [{p.flow_name}] {p.param_name}: template={p.template_value}, resolved=empty"
                )
        if unresolved:
            raise UserError(
                "The following parameters still contain unresolved placeholders:\n"
                + "\n".join(unresolved)
            )

    def action_confirm(self):
        self.ensure_one()
        instance = self.instance_id

        if self.operation == "add":
            if not self.template_flow_ids:
                raise UserError("Please select at least one template flow to add.")

            # Block deployment if any placeholders are unresolved
            self._check_unresolved_placeholders()

            # Delegate to instance deployment
            result = instance.action_deploy_flows(
                flow_ids=self.template_flow_ids,
                record=instance,
            )
            result["params"]["next"] = {"type": "ir.actions.act_window_close"}
            return result

        elif self.operation == "disable":
            if not self.target_flow_ids:
                raise UserError("Please select at least one flow to disable.")
            for flow in self.target_flow_ids:
                if flow.nr_id:
                    try:
                        instance._nr_post_json("/flow", {"id": flow.nr_id, "disabled": True})
                    except Exception:
                        pass
                flow.write({"state": "disabled"})
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Disable Complete",
                    "message": f"{len(self.target_flow_ids)} flow(s) disabled.",
                    "type": "success",
                    "sticky": False,
                    "next": {"type": "ir.actions.act_window_close"},
                },
            }

        elif self.operation == "delete":
            if not self.target_flow_ids:
                raise UserError("Please select at least one flow to delete.")
            deleted = []
            for flow in self.target_flow_ids:
                if flow.nr_id:
                    try:
                        instance._nr_get_json(f"/flow/{flow.nr_id}", timeout=15)
                        instance._nr_delete_json(f"/flow/{flow.nr_id}")
                    except Exception:
                        pass
                deleted.append(flow.display_name)
                flow.unlink()
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Delete Complete",
                    "message": f"Deleted {len(deleted)} flow(s).",
                    "type": "success",
                    "sticky": False,
                    "next": {"type": "ir.actions.act_window_close"},
                },
            }

    def _nr_delete_json(self, path, timeout=15):
        """Thin wrapper to delegate delete to instance."""
        instance = self.instance_id
        return instance._nr_delete_json(path, timeout=timeout)


class FtsNrInstanceWizardParam(models.TransientModel):
    _name = "fts.nr.instance.wizard.param"
    _description = "Wizard Parameter Preview"

    wizard_id = fields.Many2one("fts.nr.instance.wizard", string="Wizard", ondelete="cascade")
    template_flow_id = fields.Many2one("fts.nr.flow", string="Template Flow")
    flow_name = fields.Char(string="Flow")
    param_name = fields.Char(string="Parameter Name")
    template_value = fields.Char(string="Template Value")
    resolved_value = fields.Char(string="Resolved Value")
    param_type = fields.Selection([
        ("str", "String"),
        ("num", "Number"),
        ("bool", "Boolean"),
        ("json", "JSON"),
        ("env", "Environment Variable"),
    ], string="Type", default="str")