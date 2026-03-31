/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Dialog } from "@web/core/dialog/dialog";
import { rpc } from "@web/core/network/rpc";
import { Component, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

class NodeREDEmbed extends Component {
    static template = "feitas_iot.NodeREDEmbed";
    
    get iframeSrc() {
        const params = this.props.action.params || {};
        let url = params.node_red_url;
        if (url) {
            const browserHost = window.location.hostname;
            if (url.includes('//nodered-staging:')) {
                url = url.replace('//nodered-staging:', `//${browserHost}:`);
            } else if (url.includes('//nodered:')) {
                url = url.replace('//nodered:', `//${browserHost}:`);
            } else if (url.includes('//gmqtt:')) {
                url = url.replace('//gmqtt:', `//${browserHost}:`);
            }
        }
        if (url) {
            return url;
        }
        const instanceId = params.instance_id;
        return instanceId ? `/node-red/editor/${instanceId}` : '/node-red/editor';
    }
}

class NodeREDLogsDialog extends Component {
    static template = "feitas_iot.NodeREDLogsDialog";
    static components = { Dialog };

    setup() {
        this.logRef = useRef("log");
        this.state = useState({
            lines: [],
            cursor: "",
            error: "",
            loading: true,
        });
        this._pollHandle = null;

        const fetchOnce = async (initial) => {
            try {
                const payload = await rpc("/feitas_iot/nodered/logs", {
                    instance_id: this.props.instanceId,
                    cursor: initial ? "" : this.state.cursor,
                    limit: 200,
                });
                const lines = Array.isArray(payload?.lines) ? payload.lines : [];
                const nextCursor = payload?.next_cursor ? String(payload.next_cursor) : "";
                if (initial) {
                    this.state.lines = lines;
                } else if (lines.length) {
                    this.state.lines = [...this.state.lines, ...lines];
                    if (this.state.lines.length > 5000) {
                        this.state.lines = this.state.lines.slice(this.state.lines.length - 5000);
                    }
                }
                if (nextCursor) {
                    this.state.cursor = nextCursor;
                }
                this.state.error = "";
            } catch (e) {
                const msg = e?.message ? String(e.message) : String(e);
                this.state.error = msg;
            } finally {
                this.state.loading = false;
                setTimeout(() => {
                    const el = this.logRef.el;
                    if (el) {
                        el.scrollTop = el.scrollHeight;
                    }
                }, 0);
            }
        };

        const schedule = async () => {
            await fetchOnce(false);
            this._pollHandle = setTimeout(schedule, 1500);
        };

        onWillStart(async () => {
            await fetchOnce(true);
            this._pollHandle = setTimeout(schedule, 1500);
        });
        onWillUnmount(() => {
            if (this._pollHandle) {
                clearTimeout(this._pollHandle);
                this._pollHandle = null;
            }
        });
    }

    get title() {
        return this.props.title || _t("Logs");
    }

    get logText() {
        return this.state.lines.join("\n");
    }

    onClear() {
        this.state.lines = [];
        this.state.cursor = "";
        this.state.error = "";
        this.state.loading = true;
    }
}

class NodeREDLogsAction extends Component {
    static template = "feitas_iot.NodeREDLogsAction";

    setup() {
        this.logRef = useRef("log");
        this.state = useState({
            lines: [],
            cursor: "",
            error: "",
            loading: true,
        });
        this._pollHandle = null;

        const fetchOnce = async (initial) => {
            try {
                const payload = await rpc("/feitas_iot/nodered/logs", {
                    instance_id: this.instanceId,
                    agent_id: this.agentId,
                    cursor: initial ? "" : this.state.cursor,
                    limit: 200,
                });
                const lines = Array.isArray(payload?.lines) ? payload.lines : [];
                const nextCursor = payload?.next_cursor ? String(payload.next_cursor) : "";
                if (initial) {
                    this.state.lines = lines;
                } else if (lines.length) {
                    this.state.lines = [...this.state.lines, ...lines];
                    if (this.state.lines.length > 5000) {
                        this.state.lines = this.state.lines.slice(this.state.lines.length - 5000);
                    }
                }
                if (nextCursor) {
                    this.state.cursor = nextCursor;
                }
                this.state.error = "";
            } catch (e) {
                const msg = e?.message ? String(e.message) : String(e);
                this.state.error = msg;
            } finally {
                this.state.loading = false;
                setTimeout(() => {
                    const el = this.logRef.el;
                    if (el) {
                        el.scrollTop = el.scrollHeight;
                    }
                }, 0);
            }
        };

        const schedule = async () => {
            await fetchOnce(false);
            this._pollHandle = setTimeout(schedule, 1500);
        };

        onWillStart(async () => {
            const params = this.props.action.params || {};
            const instanceId = params.instance_id;
            const agentId = params.agent_id;
            if (!instanceId && !agentId) {
                return;
            }
            this.instanceId = instanceId;
            this.agentId = agentId;
            await fetchOnce(true);
            this._pollHandle = setTimeout(schedule, 1500);
        });
        onWillUnmount(() => {
            if (this._pollHandle) {
                clearTimeout(this._pollHandle);
                this._pollHandle = null;
            }
        });
    }

    get logText() {
        return this.state.lines.join("\n");
    }

    onClear() {
        this.state.lines = [];
        this.state.cursor = "";
        this.state.error = "";
        this.state.loading = true;
    }
}

registry.category("actions").add("node_red.editor", NodeREDEmbed);
registry.category("actions").add("feitas_iot.nodered_logs", NodeREDLogsAction);

const feitasIotBrokerStatusToastService = {
    dependencies: ["bus_service", "notification"],
    start(env, { bus_service: busService, notification }) {
        const typeByStatus = {
            online: "success",
            offline: "danger",
            error: "warning",
        };
        const onBrokerStatus = (payload) => {
            notification.add(payload.message || "", {
                title: payload.title || _t("Broker Status Changed"),
                type: typeByStatus[payload.new_status] || "info",
            });
        };
        busService.subscribe("feitas_iot.broker_status", onBrokerStatus);
        busService.start();
        return {
            destroy() {
                busService.unsubscribe("feitas_iot.broker_status", onBrokerStatus);
            },
        };
    },
};

registry.category("services").add("feitas_iot.broker_status_toast", feitasIotBrokerStatusToastService);
