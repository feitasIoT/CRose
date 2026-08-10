from odoo import models, fields, api, _
from odoo.exceptions import UserError


class EdgeNodeRunFlowWizard(models.TransientModel):
    _name = "fts.edge.node.run.flow.wizard"
    _description = "Run Node-RED Flows"

    node_id = fields.Many2one(
        "fts.edge.node",
        string="Edge Node",
        required=True,
        default=lambda self: self.env.context.get("active_id"),
    )
    line_ids = fields.One2many(
        "fts.edge.node.run.flow.wizard.line",
        "wizard_id",
        string="Flows",
    )
    allowed_instance_ids = fields.Many2many(
        "fts.nr.instance",
        string="Allowed Instances",
        compute="_compute_allowed_instance_ids",
    )

    @api.depends("node_id")
    def _compute_allowed_instance_ids(self):
        for wizard in self:
            node = wizard.node_id
            if not node:
                wizard.allowed_instance_ids = False
                continue
            # 边缘节点本身可能是网关；否则取其所处网关下的实例。
            gateway = node if node.is_gateway else node.gateway_id
            wizard.allowed_instance_ids = gateway.instance_ids if gateway else False

    def _get_gateway_instances(self):
        self.ensure_one()
        node = self.node_id
        gateway = node if node.is_gateway else node.gateway_id
        return gateway.instance_ids if gateway else self.env["fts.nr.instance"]

    def action_confirm(self):
        self.ensure_one()
        if not self.node_id:
            raise UserError(_("No edge node selected."))
        if not self.line_ids:
            raise UserError(_("Please add at least one flow to run."))

        # 服务器端兜底校验：只能选择网关下 NR 实例的流程，且流程须有 http in 节点。
        allowed_flows = self._get_gateway_instances().mapped("flow_ids")
        run_lines = []
        for line in self.line_ids:
            if line.flow_id not in allowed_flows:
                raise UserError(
                    _("Flow '%(flow)s' does not belong to any Node-RED instance under the gateway of node '%(node)s'.",
                      flow=line.flow_id.display_name, node=self.node_id.display_name)
                )
            http_in_node = line.flow_id.node_ids.filtered(
                lambda n: (n.node_type or "") == "http in"
            )[:1]
            if not http_in_node:
                raise UserError(
                    _("Flow '%(flow)s' has no 'http in' node and cannot be run.",
                      flow=line.flow_id.display_name)
                )
            run_lines.append((line, http_in_node))

        # 把每条明细转成一个 queue job task，按 sequence 顺序排队执行。
        enqueued = 0
        for line, http_in_node in sorted(run_lines, key=lambda item: item[0].sequence):
            http_in_node.with_delay(
                description=_(
                    "Run flow '%(flow)s' on edge node '%(node)s'",
                    flow=line.flow_id.display_name, node=self.node_id.display_name,
                ),
            ).action_run_flow(self.node_id.id)
            enqueued += 1

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Run Submitted"),
                "message": _("%(count)d flow(s) have been queued for execution.", count=enqueued),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }


class EdgeNodeRunFlowWizardLine(models.TransientModel):
    _name = "fts.edge.node.run.flow.wizard.line"
    _description = "Run Flow Line"

    wizard_id = fields.Many2one(
        "fts.edge.node.run.flow.wizard",
        string="Wizard",
        ondelete="cascade",
        required=True,
    )
    sequence = fields.Integer(string="Sequence", default=10)
    flow_id = fields.Many2one("fts.nr.flow", string="Flow", required=True)
