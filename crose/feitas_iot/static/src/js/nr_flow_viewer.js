/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState, useRef, onMounted, onWillUnmount, useEffect } from "@odoo/owl";

// ---------------------------------------------------------------------------
// Node-RED node-type → color mapping (mirrors Node-RED Editor palette)
// ---------------------------------------------------------------------------
const NODE_COLORS = {
    "inject":             "#a6bbcf",
    "debug":              "#87a980",
    "catch":              "#c0a89a",
    "status":             "#c0a89a",
    "complete":           "#c0a89a",
    "function":           "#f9df7c",
    "switch":             "#e7dcc7",
    "change":             "#e3e8ef",
    "range":              "#e3e8ef",
    "template":           "#d4cec5",
    "delay":              "#e6cfb4",
    "trigger":            "#e6cfb4",
    "comment":            "#ffffff",
    "link in":            "#d7d7d7",
    "link out":           "#d7d7d7",
    "link call":          "#d7d7d7",
    "mqtt in":            "#c2d6d6",
    "mqtt out":           "#c2d6d6",
    "http in":            "#d4cec5",
    "http response":      "#d4cec5",
    "http request":       "#d4cec5",
    "websocket in":       "#d4cec5",
    "websocket out":      "#d4cec5",
    "tcp in":             "#c2d6d6",
    "tcp out":            "#c2d6d6",
    "tcp request":        "#c2d6d6",
    "udp in":             "#c2d6d6",
    "udp out":            "#c2d6d6",
    "file in":            "#b4c79a",
    "file":               "#b4c79a",
    "watch":              "#b4c79a",
    "tail":               "#b4c79a",
    "join":               "#d4cec5",
    "split":              "#d4cec5",
    "sort":               "#d4cec5",
    "batch":              "#d4cec5",
    "exec":               "#d7b4b4",
    "subflow":            "#d7d7d7",
    "serial in":          "#c2d6d6",
    "serial out":         "#c2d6d6",
    "serial request":     "#c2d6d6",
    "ui_button":          "#d4cec5",
    "ui_text":            "#d4cec5",
    "ui_chart":           "#d4cec5",
    "ui_gauge":           "#d4cec5",
    "ui_slider":          "#d4cec5",
    "ui_switch":          "#d4cec5",
    "ui_dropdown":        "#d4cec5",
    "ui_form":            "#d4cec5",
    "ui_template":        "#d4cec5",
    "ui_toast":           "#d4cec5",
    "ui_notification":    "#d4cec5",
};

const DEFAULT_FILL = "#dde4ea";
const NODE_HEIGHT = 30;
const NODE_RX = 5;
const CJK_CHAR_WIDTH = 13;    // approximate px per CJK character at 12px font
const ASCII_CHAR_WIDTH = 7.5; // approximate px per ASCII character at 12px font
const MIN_NODE_WIDTH = 100;
const LABEL_PAD = 16;       // padding for label on each side
const ICON_WIDTH = 30;      // width of the left icon square

// ---------------------------------------------------------------------------
// Measure rendered text width accounting for CJK vs ASCII characters
// ---------------------------------------------------------------------------
function measureTextWidth(text) {
    let width = 0;
    for (const ch of text) {
        const code = ch.codePointAt(0);
        if ((code >= 0x4E00 && code <= 0x9FFF) ||   // CJK Unified Ideographs
            (code >= 0x3400 && code <= 0x4DBF) ||   // CJK Extension A
            (code >= 0x3000 && code <= 0x303F) ||   // CJK Symbols/Punctuation
            (code >= 0xFF00 && code <= 0xFFEF) ||   // Halfwidth/Fullwidth Forms
            (code >= 0xAC00 && code <= 0xD7AF)) {   // Korean Hangul
            width += CJK_CHAR_WIDTH;
        } else {
            width += ASCII_CHAR_WIDTH;
        }
    }
    return width;
}
const INPUT_PORT_X = 0;
const OUTPUT_PORT_X_OFFSET = 0; // will be node width

// ---------------------------------------------------------------------------
// Color helpers
// ---------------------------------------------------------------------------
function nodeFill(type) {
    if (!type) return DEFAULT_FILL;
    return NODE_COLORS[type] || DEFAULT_FILL;
}

function isDark(hex) {
    if (!hex || hex.length < 7) return false;
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return (r * 0.299 + g * 0.587 + b * 0.114) < 140;
}

function nodeBorder(type, fill) {
    if (type === "comment") return "transparent";
    if (type === "subflow") return "#555555";
    return isDark(fill) ? "#444444" : "#999999";
}

function nodeTextColor(type, fill) {
    if (type === "comment") return "#666666";
    return isDark(fill) ? "#ffffff" : "#333333";
}

// ---------------------------------------------------------------------------
// Flow parser: separate groups from regular nodes
// ---------------------------------------------------------------------------
function normalizeFlow(raw) {
    if (!raw) return [];
    if (Array.isArray(raw)) return raw;
    if (raw.nodes && Array.isArray(raw.nodes)) return raw.nodes;
    return [];
}

function parseFlow(raw) {
    const allNodes = normalizeFlow(raw);
    const groups = [];
    const regularNodes = [];
    const idToNode = {};

    for (const n of allNodes) {
        if (!n || !n.id) continue;
        idToNode[n.id] = n;
        if (n.type === "group") {
            groups.push(n);
        } else {
            regularNodes.push(n);
        }
    }

    // Build connections from wires (only regular nodes have wires)
    const connections = [];
    for (const n of regularNodes) {
        if (!Array.isArray(n.wires)) continue;
        for (let pi = 0; pi < n.wires.length; pi++) {
            const targets = n.wires[pi] || [];
            for (const tid of targets) {
                connections.push({ fromId: n.id, fromPort: pi, toId: tid });
            }
        }
    }

    return { groups, nodes: regularNodes, connections, idToNode };
}

// ---------------------------------------------------------------------------
// OWL Component
// ---------------------------------------------------------------------------
class NrFlowViewerField extends Component {
    static template = "feitas_iot.NrFlowViewer";
    static props = ["*"];

    setup() {
        this.containerRef = useRef("container");
        this.state = useState({
            groups: [],
            nodes: [],
            connections: [],
            idToNode: {},
            view: { x: 0, y: 0, scale: 1 },
            error: null,
            info: "",
            dragging: false,
            dragStart: { x: 0, y: 0 },
            dragViewStart: { x: 0, y: 0 },
        });

        this._resizeObserver = null;
        onMounted(() => {
            this._loadFlow();
            this._observeResize();
        });
        onWillUnmount(() => {
            if (this._resizeObserver) {
                this._resizeObserver.disconnect();
                this._resizeObserver = null;
            }
        });
        useEffect(
            () => { this._loadFlow(); },
            () => [this.props.record?.data?.content],
        );
    }

    // -------------------------------------------------------------------
    // Data loading
    // -------------------------------------------------------------------
    _loadFlow() {
        try {
            const content = this.props.record?.data?.content;
            if (!content) {
                this.state.groups = [];
                this.state.nodes = [];
                this.state.connections = [];
                this.state.idToNode = {};
                this.state.error = null;
                this.state.info = "No flow JSON content to display";
                return;
            }
            const parsed = typeof content === "string" ? JSON.parse(content) : content;
            const { groups, nodes, connections, idToNode } = parseFlow(parsed);
            this.state.groups = groups;
            this.state.nodes = nodes;
            this.state.connections = connections;
            this.state.idToNode = idToNode;
            this.state.error = null;
            if (nodes.length === 0 && groups.length === 0) {
                this.state.info = "Flow is empty — no nodes found";
            } else {
                this.state.info = "";
                Promise.resolve().then(() => this._fitToScreen());
            }
        } catch (e) {
            this.state.error = e.message || String(e);
            this.state.groups = [];
            this.state.nodes = [];
            this.state.connections = [];
            this.state.idToNode = {};
        }
    }

    // -------------------------------------------------------------------
    // Compute the width of a regular node based on its label
    // -------------------------------------------------------------------
    _nodeWidth(node) {
        if (node.type === "comment") return 160;
        const label = node.name || node.type || "node";
        const textW = measureTextWidth(label);
        return Math.max(MIN_NODE_WIDTH, ICON_WIDTH + textW + LABEL_PAD);
    }

    _nodeHeight(node) {
        if (node.type === "comment") return 40;
        return NODE_HEIGHT;
    }

    _nodeX(node) { return typeof node.x === "number" ? node.x : 100; }
    _nodeY(node) { return typeof node.y === "number" ? node.y : 100; }

    // -------------------------------------------------------------------
    // Output port Y position relative to node top
    // -------------------------------------------------------------------
    _portY(node, portIdx) {
        const total = (node.wires && node.wires.length) || 0;
        const h = this._nodeHeight(node);
        if (total <= 1) return h / 2;
        const spacing = (h - 8) / total;
        return 4 + spacing / 2 + portIdx * spacing;
    }

    // -------------------------------------------------------------------
    // Bounds of all content
    // -------------------------------------------------------------------
    _computeBounds() {
        const { groups, nodes } = this.state;
        if (!groups.length && !nodes.length) {
            return { minX: 0, minY: 0, maxX: 800, maxY: 600 };
        }
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;

        for (const g of groups) {
            const gx = this._nodeX(g);
            const gy = this._nodeY(g);
            const gw = typeof g.w === "number" ? g.w : 200;
            const gh = typeof g.h === "number" ? g.h : 100;
            if (gx < minX) minX = gx;
            if (gy < minY) minY = gy;
            if (gx + gw > maxX) maxX = gx + gw;
            if (gy + gh > maxY) maxY = gy + gh;
        }

        for (const n of nodes) {
            const x = this._nodeX(n);
            const y = this._nodeY(n);
            const w = this._nodeWidth(n);
            const h = this._nodeHeight(n);
            if (x < minX) minX = x;
            if (y < minY) minY = y;
            if (x + w > maxX) maxX = x + w;
            if (y + h > maxY) maxY = y + h;
        }

        if (!isFinite(minX)) {
            return { minX: 0, minY: 0, maxX: 800, maxY: 600 };
        }
        return {
            minX: minX - 60,
            minY: minY - 60,
            maxX: maxX + 60,
            maxY: maxY + 60,
        };
    }

    // -------------------------------------------------------------------
    // Fit-to-screen
    // -------------------------------------------------------------------
    _fitToScreen() {
        const el = this.containerRef?.el;
        if (!el) return;
        const b = this._computeBounds();
        const fw = b.maxX - b.minX || 800;
        const fh = b.maxY - b.minY || 600;
        const vw = el.clientWidth || 800;
        const vh = el.clientHeight || 600;
        const pad = 0.88;
        const scale = Math.min((vw * pad) / fw, (vh * pad) / fh, 2);
        const cx = b.minX + fw / 2;
        const cy = b.minY + fh / 2;
        this.state.view = {
            x: vw / 2 - cx * scale,
            y: vh / 2 - cy * scale,
            scale,
        };
    }

    _observeResize() {
        if (!window.ResizeObserver) return;
        const el = this.containerRef?.el;
        if (!el) return;
        this._resizeObserver = new ResizeObserver(() => this._fitToScreen());
        this._resizeObserver.observe(el);
    }

    // -------------------------------------------------------------------
    // Pointer & wheel handlers
    // -------------------------------------------------------------------
    _onPointerDown(ev) {
        if (ev.button !== undefined && ev.button !== 0) return;
        this.state.dragging = true;
        this.state.dragStart = { x: ev.clientX, y: ev.clientY };
        this.state.dragViewStart = { ...this.state.view };
        ev.target?.setPointerCapture?.(ev.pointerId);
    }
    _onPointerMove(ev) {
        if (!this.state.dragging) return;
        const dx = ev.clientX - this.state.dragStart.x;
        const dy = ev.clientY - this.state.dragStart.y;
        this.state.view = {
            x: this.state.dragViewStart.x + dx,
            y: this.state.dragViewStart.y + dy,
            scale: this.state.dragViewStart.scale,
        };
    }
    _onPointerUp(ev) {
        this.state.dragging = false;
        ev.target?.releasePointerCapture?.(ev.pointerId);
    }
    _onWheel(ev) {
        ev.preventDefault();
        const rect = ev.currentTarget.getBoundingClientRect();
        const mx = ev.clientX - rect.left;
        const my = ev.clientY - rect.top;
        const factor = ev.deltaY < 0 ? 1.1 : 0.9;
        const old = this.state.view.scale;
        const ns = Math.min(5, Math.max(0.1, old * factor));
        this.state.view = {
            x: mx - (mx - this.state.view.x) * (ns / old),
            y: my - (my - this.state.view.y) * (ns / old),
            scale: ns,
        };
    }

    _zoomIn() {
        const ns = Math.min(5, this.state.view.scale * 1.3);
        const el = this.containerRef?.el;
        const cx = el ? el.clientWidth / 2 : 400;
        const cy = el ? el.clientHeight / 2 : 300;
        const old = this.state.view.scale;
        this.state.view = {
            x: cx - (cx - this.state.view.x) * (ns / old),
            y: cy - (cy - this.state.view.y) * (ns / old),
            scale: ns,
        };
    }
    _zoomOut() {
        const ns = Math.max(0.1, this.state.view.scale / 1.3);
        const el = this.containerRef?.el;
        const cx = el ? el.clientWidth / 2 : 400;
        const cy = el ? el.clientHeight / 2 : 300;
        const old = this.state.view.scale;
        this.state.view = {
            x: cx - (cx - this.state.view.x) * (ns / old),
            y: cy - (cy - this.state.view.y) * (ns / old),
            scale: ns,
        };
    }

    // -------------------------------------------------------------------
    // Template getters
    // -------------------------------------------------------------------
    get viewport_transform() {
        const v = this.state.view;
        return `translate(${v.x},${v.y}) scale(${v.scale})`;
    }

    get grid_bounds() {
        const b = this._computeBounds();
        return {
            minX: b.minX - 200,
            minY: b.minY - 200,
            width: (b.maxX - b.minX) + 400,
            height: (b.maxY - b.minY) + 400,
        };
    }

    get zoom_pct_label() {
        return Math.round(this.state.view.scale * 100) + "%";
    }

    // -------------------------------------------------------------------
    // Group render data (rendered first, behind nodes)
    // -------------------------------------------------------------------
    get group_render_data() {
        return this.state.groups.map(g => {
            const x = this._nodeX(g);
            const y = this._nodeY(g);
            const w = typeof g.w === "number" ? g.w : 200;
            const h = typeof g.h === "number" ? g.h : 100;
            const style = g.style || {};
            const showLabel = style.label !== false;
            const labelPos = style["label-position"] || "nw";
            // Default group style: dashed gray border, no fill
            const stroke = style.stroke || "#999999";
            const strokeOpacity = style["stroke-opacity"] != null ? style["stroke-opacity"] : 1;
            const fill = style.fill || "none";
            const fillOpacity = style["fill-opacity"] != null ? style["fill-opacity"] : 1;
            const labelColor = style.color || "#a4a4a4";
            const name = g.name || "";

            let labelX, labelY, labelAnchor;
            if (labelPos === "nw") {
                labelX = x + 6;
                labelY = y + 14;
                labelAnchor = "start";
            } else if (labelPos === "ne") {
                labelX = x + w - 6;
                labelY = y + 14;
                labelAnchor = "end";
            } else {
                labelX = x + 6;
                labelY = y + 14;
                labelAnchor = "start";
            }

            return {
                id: g.id,
                rect: { x, y, w, h },
                stroke,
                strokeOpacity,
                fill,
                fillOpacity,
                showLabel,
                label: { x: labelX, y: labelY, text: name, color: labelColor, anchor: labelAnchor },
            };
        });
    }

    // -------------------------------------------------------------------
    // Wire paths
    // -------------------------------------------------------------------
    get wire_paths() {
        const wires = [];
        const { connections, idToNode } = this.state;
        for (const c of connections) {
            const from = idToNode[c.fromId];
            const to = idToNode[c.toId];
            if (!from || !to) continue;

            const fromW = this._nodeWidth(from);
            const sx = this._nodeX(from) + fromW;
            const sy = this._nodeY(from) + this._portY(from, c.fromPort);
            const ex = this._nodeX(to);
            const ey = this._nodeY(to) + this._nodeHeight(to) / 2;
            const dx = Math.abs(ex - sx) * 0.5;
            const path = `M ${sx},${sy} C ${sx + dx},${sy} ${ex - dx},${ey} ${ex},${ey}`;

            // Arrowhead at t=0.93
            const t = 0.93, u = 1 - t;
            const cp1x = sx + dx, cp1y = sy, cp2x = ex - dx, cp2y = ey;
            const bx = u*u*u*sx + 3*u*u*t*cp1x + 3*u*t*t*cp2x + t*t*t*ex;
            const by = u*u*u*sy + 3*u*u*t*cp1y + 3*u*t*t*cp2y + t*t*t*ey;
            const ttx = 3*u*u*(cp1x-sx) + 6*u*t*(cp2x-cp1x) + 3*t*t*(ex-cp2x);
            const tty = 3*u*u*(cp1y-sy) + 6*u*t*(cp2y-cp1y) + 3*t*t*(ey-cp2y);
            const tl = Math.sqrt(ttx*ttx + tty*tty) || 1;
            const nx = ttx / tl, ny = tty / tl;
            const as = 4;
            const arrow = `${bx},${by} ${bx - nx*as*2.5 + ny*as},${by - ny*as*2.5 - nx*as} ${bx - nx*as*2.5 - ny*as},${by - ny*as*2.5 + nx*as}`;

            wires.push({ key: `${c.fromId}-${c.fromPort}-${c.toId}`, path, arrow });
        }
        return wires;
    }

    // -------------------------------------------------------------------
    // Node render data
    // -------------------------------------------------------------------
    get node_render_data() {
        return this.state.nodes.map(n => {
            const x = this._nodeX(n);
            const y = this._nodeY(n);
            const w = this._nodeWidth(n);
            const h = this._nodeHeight(n);
            const fill = nodeFill(n.type);
            const border = nodeBorder(n.type, fill);
            const textColor = nodeTextColor(n.type, fill);
            const isComment = n.type === "comment";

            // Badge (left icon square)
            let badge = null;
            if (!isComment) {
                const badgeFill = n.type === "subflow" ? "#555555" : border;
                badge = {
                    x, y, w: h, h,
                    rx: NODE_RX - 1, ry: NODE_RX - 1,
                    fill: badgeFill,
                    textX: x + h / 2,
                    textY: y + h / 2 + 4,
                    letter: this._iconLetter(n),
                };
            }

            // Label
            let labelText = n.name || n.type || "node";
            const maxLen = isComment ? 55 : Math.max(3, Math.floor((w - ICON_WIDTH - LABEL_PAD) / ASCII_CHAR_WIDTH));
            if (labelText.length > maxLen) labelText = labelText.slice(0, maxLen - 3) + "...";

            const label = {
                x: isComment ? x + 8 : x + h + 8,
                y: isComment ? y + 14 : y + h / 2 + 4,
                fontSize: isComment ? 11 : 12,
                color: textColor,
                text: labelText,
            };

            // Output ports
            const ports = [];
            const totalOuts = (n.wires && n.wires.length) || 0;
            if (totalOuts > 1) {
                for (let i = 0; i < totalOuts; i++) {
                    ports.push({
                        key: `p-${n.id}-${i}`,
                        cx: x + w,
                        cy: y + this._portY(n, i),
                    });
                }
            }

            return {
                id: n.id,
                rect: {
                    x, y, w, h,
                    rx: isComment ? 0 : NODE_RX,
                    ry: isComment ? 0 : NODE_RX,
                    fill, stroke: border,
                    cssClass: isComment ? "feitas_nr_comment_node" : "feitas_nr_normal_node",
                },
                badge,
                label,
                ports,
            };
        });
    }

    _iconLetter(n) {
        const type = (n.type || "?").replace(/^subflow:/, "").replace(/^ui_/, "");
        return type.charAt(0).toUpperCase();
    }
}

// ---------------------------------------------------------------------------
// Registry — available as widget="nr_flow_viewer"
// ---------------------------------------------------------------------------
export const nrFlowViewer = {
    component: NrFlowViewerField,
    supportedTypes: ["text", "char"],
    extractProps({ attrs, field }) {
        return {
            readonly: attrs.readonly,
            name: field?.name,
        };
    },
};

registry.category("fields").add("nr_flow_viewer", nrFlowViewer);
registry.category("fields").add("nr_flowviewer", nrFlowViewer);
