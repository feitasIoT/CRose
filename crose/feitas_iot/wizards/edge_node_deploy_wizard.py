from odoo import models, fields, api, _
from odoo.exceptions import UserError


class EdgeNodeDeployWizard(models.TransientModel):
    _name = "edge.node.deploy.wizard"
    _description = "Edge Node Deploy Wizard"

    # -- User-selectable deploy targets --
    docker = fields.Boolean(
        string="Docker Containers",
        default=False,
        help="Deploy Docker containers (docker compose up -d).",
    )
    nodered = fields.Boolean(
        string="Node-RED",
        default=False,
        help="Deploy a Node-RED instance.",
    )
    another_nodered = fields.Boolean(
        string="Additional Node-RED",
        default=False,
        help="Deploy an additional Node-RED instance.",
    )
    mqtt_broker = fields.Boolean(
        string="MQTT Broker",
        default=False,
        help="Deploy the MQTT Broker.",
    )
    redis = fields.Boolean(
        string="Redis",
        default=False,
        help="Deploy Redis.",
    )

    # -- Context state fields (read-only, auto-populated, drive view logic) --
    node_has_docker = fields.Boolean("Docker Already Deployed", readonly=True)
    node_has_nodered = fields.Boolean("Node-RED Already Deployed", readonly=True)
    node_has_mqtt_broker = fields.Boolean("MQTT Broker Already Deployed", readonly=True)
    node_uses_redis = fields.Boolean("Redis Already Deployed", readonly=True)

    # -- Computed visibility fields (Odoo 17+ invisible attribute) --
    docker_invisible = fields.Boolean(compute="_compute_visibility")
    nodered_invisible = fields.Boolean(compute="_compute_visibility")
    another_nodered_invisible = fields.Boolean(compute="_compute_visibility")
    mqtt_broker_invisible = fields.Boolean(compute="_compute_visibility")
    redis_invisible = fields.Boolean(compute="_compute_visibility")

    @api.depends("node_has_docker", "node_has_nodered", "node_has_mqtt_broker", "node_uses_redis")
    def _compute_visibility(self):
        for rec in self:
            rec.docker_invisible = rec.node_has_docker
            rec.nodered_invisible = rec.node_has_nodered
            rec.another_nodered_invisible = not rec.node_has_nodered
            rec.mqtt_broker_invisible = rec.node_has_mqtt_broker
            rec.redis_invisible = rec.node_uses_redis

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        active_id = self.env.context.get("active_id")
        if active_id:
            node = self.env["fts.edge.node"].browse(active_id)
            if node.exists():
                defaults["node_has_docker"] = node.has_docker
                defaults["node_has_nodered"] = node.has_nodered
                defaults["node_has_mqtt_broker"] = node.has_mqtt_broker
                defaults["node_uses_redis"] = node.use_redis
        return defaults

    def action_confirm(self):
        self.ensure_one()
        active_id = self.env.context.get("active_id")
        if not active_id:
            raise UserError(_("No edge node selected."))
        node = self.env["fts.edge.node"].browse(active_id)
        if not node.exists():
            raise UserError(_("Edge node not found."))
        if node.status != "draft":
            raise UserError(_("Deploy is only available for nodes in 'Draft' status."))

        # Step 1: Run deploy prechecks and generate deployment package
        node.action_deploy()

        # Step 2: Run initialization steps controlled by user's checkboxes
        node.action_initialize(
            deploy_nodered=self.nodered or self.another_nodered,
            deploy_mqtt_broker=self.mqtt_broker,
            deploy_redis=self.redis,
        )

        # Close wizard
        return {"type": "ir.actions.act_window_close"}
