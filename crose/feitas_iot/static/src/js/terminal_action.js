/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onWillStart, onWillUnmount, onMounted, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";

class TerminalAction extends Component {
    static template = "feitas_iot.TerminalAction";

    setup() {
        this.terminalRef = useRef("terminal");
        this.state = useState({
            connected: false,
            error: "",
            connecting: true,
        });
        this._terminal = null;
        this._ws = null;
        this._fitAddon = null;
        this._resizeObserver = null;
        this._sshCredentials = null;

        onWillStart(async () => {
            const params = this.props.action.params || {};
            this._sshCredentials = {
                host: params.ssh_host,
                port: params.ssh_port || 22,
                username: params.ssh_username || "",
                password: params.ssh_password || "",
                ws_url: params.ws_url || "",
            };
            try {
                await this._loadXtermAssets();
            } catch (e) {
                this.state.error = _t(
                    "Failed to load terminal library: %(error)s",
                    { error: String(e) }
                );
            }
        });

        onMounted(() => {
            if (!this.state.error) {
                this._initTerminal();
                this._connectWebSocket();
            }
        });

        onWillUnmount(() => {
            this._cleanup();
        });
    }

    // --- Xterm.js dynamic loading ---

    async _loadXtermAssets() {
        // Load CSS once
        if (!document.querySelector("link[data-feitas-xterm-css]")) {
            await new Promise((resolve, reject) => {
                const link = document.createElement("link");
                link.rel = "stylesheet";
                link.href = "/feitas_iot/static/src/lib/xterm/xterm.css";
                link.dataset.feitasXtermCss = "1";
                link.onload = resolve;
                link.onerror = () => reject(new Error("Failed to load xterm.css"));
                document.head.appendChild(link);
            });
        }

        // Load JS libraries sequentially (addons depend on core)
        if (!window.Terminal) {
            await this._injectScript(
                "/feitas_iot/static/src/lib/xterm/xterm.js"
            );
        }
        if (!window.FitAddon) {
            await this._injectScript(
                "/feitas_iot/static/src/lib/xterm/xterm-addon-fit.js"
            );
        }
        if (!window.WebLinksAddon) {
            await this._injectScript(
                "/feitas_iot/static/src/lib/xterm/xterm-addon-web-links.js"
            );
        }
    }

    _injectScript(src) {
        return new Promise((resolve, reject) => {
            const script = document.createElement("script");
            script.src = src;
            script.onload = resolve;
            script.onerror = () =>
                reject(new Error(`Failed to load: ${src}`));
            document.head.appendChild(script);
        });
    }

    // --- Terminal initialization ---

    _initTerminal() {
        const container = this.terminalRef.el;
        if (!container) {
            return;
        }

        this._terminal = new window.Terminal({
            cursorBlink: true,
            fontSize: 14,
            fontFamily:
                "'Cascadia Code', 'Fira Code', 'Consolas', 'Courier New', monospace",
            theme: {
                background: "#1e1e1e",
                foreground: "#d4d4d4",
                cursor: "#ffffff",
                selectionBackground: "#264f78",
                black: "#000000",
                red: "#cd3131",
                green: "#0dbc79",
                yellow: "#e5e510",
                blue: "#2472c8",
                magenta: "#bc3fbc",
                cyan: "#11a8cd",
                white: "#e5e5e5",
                brightBlack: "#666666",
                brightRed: "#f14c4c",
                brightGreen: "#23d18b",
                brightYellow: "#f5f543",
                brightBlue: "#3b8eea",
                brightMagenta: "#d670d6",
                brightCyan: "#29b8db",
                brightWhite: "#ffffff",
            },
            allowProposedApi: true,
            scrollback: 5000,
            tabStopWidth: 4,
        });

        this._terminal.open(container);

        // Fit addon — auto-resize terminal to container
        if (window.FitAddon) {
            this._fitAddon = new window.FitAddon.FitAddon();
            this._terminal.loadAddon(this._fitAddon);
            this._fitAddon.fit();
        }

        // Web links addon — make URLs clickable
        if (window.WebLinksAddon) {
            const webLinksAddon = new window.WebLinksAddon.WebLinksAddon();
            this._terminal.loadAddon(webLinksAddon);
        }

        // ResizeObserver for container size changes
        if (window.ResizeObserver) {
            this._resizeObserver = new ResizeObserver(() => {
                if (this._fitAddon) {
                    try {
                        this._fitAddon.fit();
                    } catch (_) {
                        // ignore fit errors during resize
                    }
                }
            });
            this._resizeObserver.observe(container);
        }

        // Forward keystrokes to WebSocket
        this._terminal.onData((data) => {
            if (this._ws && this._ws.readyState === WebSocket.OPEN) {
                this._ws.send(data);
            }
        });

        // Handle terminal resize from PTY
        this._terminal.onResize(({ cols, rows }) => {
            if (this._ws && this._ws.readyState === WebSocket.OPEN) {
                this._ws.send(
                    JSON.stringify({ type: "resize", cols, rows })
                );
            }
        });
    }

    // --- WebSocket connection ---

    _connectWebSocket() {
        const wsUrl = this._sshCredentials.ws_url;
        if (!wsUrl) {
            this.state.connecting = false;
            this.state.error = _t("No WebSocket URL configured.");
            return;
        }

        try {
            this._ws = new WebSocket(wsUrl);
            this._ws.binaryType = "arraybuffer";
        } catch (e) {
            this.state.connecting = false;
            this.state.error = _t(
                "Failed to create WebSocket: %(error)s",
                { error: String(e) }
            );
            return;
        }

        this._ws.onopen = () => {
            this.state.connected = true;
            this.state.connecting = false;

            // Send SSH credentials as first message
            const connectMsg = JSON.stringify({
                type: "connect",
                host: this._sshCredentials.host,
                port: this._sshCredentials.port,
                username: this._sshCredentials.username,
                password: this._sshCredentials.password,
            });
            this._ws.send(connectMsg);

            if (this._terminal) {
                this._terminal.focus();
            }
        };

        this._ws.onmessage = (event) => {
            if (!this._terminal) {
                return;
            }
            if (event.data instanceof ArrayBuffer) {
                this._terminal.write(new Uint8Array(event.data));
            } else if (typeof event.data === "string") {
                // Try to detect JSON status messages from the bridge
                try {
                    const msg = JSON.parse(event.data);
                    if (msg.type === "error") {
                        this._terminal.writeln(
                            `\r\n\x1b[31m[ERROR] ${msg.message}\x1b[0m`
                        );
                    } else if (msg.type === "status") {
                        this._terminal.writeln(
                            `\r\n\x1b[33m[${msg.message}]\x1b[0m`
                        );
                    }
                } catch (_) {
                    // Plain text output, write directly to terminal
                    this._terminal.write(event.data);
                }
            }
        };

        this._ws.onerror = () => {
            this.state.connecting = false;
            this.state.error = _t(
                "WebSocket connection error. Please check that the " +
                    "gateway is online and Node-RED is running."
            );
        };

        this._ws.onclose = (event) => {
            this.state.connected = false;
            if (this._terminal) {
                this._terminal.writeln(
                    `\r\n\x1b[33m*** Connection closed (code ${event.code}) ***\x1b[0m`
                );
            }
        };
    }

    // --- Cleanup ---

    _cleanup() {
        if (this._ws) {
            try {
                this._ws.close();
            } catch (_) {
                // ignore
            }
            this._ws = null;
        }
        if (this._resizeObserver) {
            this._resizeObserver.disconnect();
            this._resizeObserver = null;
        }
        if (this._terminal) {
            try {
                this._terminal.dispose();
            } catch (_) {
                // ignore
            }
            this._terminal = null;
        }
    }
}

registry.category("actions").add("feitas_iot.terminal", TerminalAction);
