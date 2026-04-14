/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onWillStart, useState } from "@odoo/owl";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";

class OverviewDashboard extends Component {
    static template = "feitas_iot.OverviewDashboard";

    setup() {
        this.action = useService("action");
        this.state = useState({
            loading: true,
            error: null,
            components: [],
            timeRange: "today",
            overview: {
                stats: { agents: 0, instances: 0, topics: 0 },
                metrics: { cpu: "-", memory: "-", disk: "-", network: "-" },
                dashboard: {
                    connectivity: { protocol: {} },
                    throughput: {},
                    metrics_trend: { time_range: "today", points: [] },
                    value_delivery: { kpis: [], trend_points: [] },
                    asset_insight: {},
                },
            },
        });

        onWillStart(async () => {
            await this.fetchData();
        });
    }

    async fetchData() {
        this.state.loading = true;
        try {
            const componentData = await rpc("/feitas_iot/get_component_status", {
                time_range: this.state.timeRange,
            });
            this.state.components = componentData.components || [];
            if (componentData.overview) {
                this.state.overview = componentData.overview;
            }
            this.state.error = null;
        } catch (e) {
            this.state.error = e.message;
        } finally {
            this.state.loading = false;
        }
    }

    async changeTimeRange(range) {
        if (this.state.timeRange === range) {
            return;
        }
        this.state.timeRange = range;
        await this.fetchData();
    }

    getComponentStats(type) {
        const filtered = this.state.components.filter(c => c.component_type === type);
        const online = filtered.filter(c => c.status === 'online').length;
        const total = filtered.length;
        const pct = total > 0 ? Math.round((online * 100) / total) : 0;
        return { online, total, pct };
    }

    getTrendPath() {
        const points = (this.state.overview?.dashboard?.value_delivery?.trend_points) || [];
        if (!points.length) {
            return "";
        }
        const width = 340;
        const height = 110;
        const min = Math.min(...points);
        const max = Math.max(...points);
        const range = max - min || 1;
        return points.map((v, i) => {
            const x = points.length === 1 ? 0 : (i * width) / (points.length - 1);
            const y = height - ((v - min) / range) * height;
            return `${x},${y}`;
        }).join(" ");
    }

    getDeviceOnlinePct() {
        const asset = this.state.overview?.dashboard?.asset_insight || {};
        const total = Number(asset.devices_total || 0);
        const online = Number(asset.online_devices || 0);
        if (!total) {
            return 0;
        }
        return Math.round((online * 100) / total);
    }

    getMetricTrendPath(metricName) {
        const points = (this.state.overview?.dashboard?.metrics_trend?.points) || [];
        if (!points.length) {
            return "";
        }
        const values = points
            .map(p => Number(p?.[metricName]))
            .filter(v => Number.isFinite(v));
        if (!values.length) {
            return "";
        }
        const width = 260;
        const height = 70;
        const min = Math.min(...values);
        const max = Math.max(...values);
        const range = max - min || 1;
        const normalized = points.map(p => {
            const v = Number(p?.[metricName]);
            return Number.isFinite(v) ? v : min;
        });
        return normalized.map((v, i) => {
            const x = normalized.length === 1 ? 0 : (i * width) / (normalized.length - 1);
            const y = height - ((v - min) / range) * height;
            return `${x},${y}`;
        }).join(" ");
    }

    openAskAi() {
        return this.action.doAction({
            type: "ir.actions.act_window",
            name: "Ask AI",
            res_model: "fts.ai.chat.wizard",
            views: [[false, "form"]],
            target: "new",
        });
    }
}

registry.category("actions").add("feitas_iot.overview", OverviewDashboard);
