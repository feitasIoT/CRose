import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { Component, onWillStart, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { SpreadsheetComponent } from "@spreadsheet/actions/spreadsheet_component";
import { Model } from "@odoo/o-spreadsheet";

function decodeBase64Json(base64Value) {
    const binary = atob(base64Value);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
    }
    return JSON.parse(new TextDecoder("utf-8").decode(bytes));
}

export class DataModelSpreadsheetAction extends Component {
    static template = "feitas_iot.DataModelSpreadsheetAction";
    static components = { SpreadsheetComponent };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            fileName: _t("Query Result"),
        });
        onWillStart(async () => {
            const resId = this.props.action?.params?.resId;
            const readonly = this.props.action?.params?.readonly === true;
            const [record] = await this.orm.call(
                "fts.data.model",
                "read",
                [[resId], ["name", "spreadsheet_binary_data"]],
                { context: { bin_size: false } }
            );
            this.state.fileName = record?.name || _t("Query Result");
            let data = {
                sheets: [{ id: "sheet1", name: "Sheet1" }],
                settings: {},
                revisionId: "START_REVISION",
            };
            if (record?.spreadsheet_binary_data) {
                try {
                    data = decodeBase64Json(record.spreadsheet_binary_data);
                } catch (error) {
                    this.notification.add(`${_t("Failed to parse spreadsheet data")}: ${error?.message || error}`, {
                        type: "danger",
                        sticky: true,
                    });
                    data = {
                        sheets: [{ id: "sheet1", name: "Sheet1" }],
                        settings: {},
                        revisionId: "START_REVISION",
                    };
                }
            }
            data.settings = data.settings || {};
            if (typeof data.settings.locale === "string") {
                delete data.settings.locale;
            }
            const cellCount = Object.keys(data?.sheets?.[0]?.cells || {}).length;
            if (!cellCount) {
                this.notification.add(_t("The spreadsheet is open, but there are no visible cell values."), {
                    type: "warning",
                    sticky: true,
                });
            }
            this.model = new Model(data, {
                mode: readonly ? "readonly" : "normal",
            });
        });
    }

    async downloadSpreadsheet() {
        if (!this.model) {
            this.notification.add(_t("The spreadsheet has not finished initializing."), {
                type: "warning",
            });
            return;
        }
        await this.actionService.doAction({
            type: "ir.actions.client",
            tag: "action_download_spreadsheet",
            params: {
                name: this.state.fileName,
                xlsxData: this.model.exportXLSX(),
            },
        });
    }
}

registry.category("actions").add("feitas_iot.action_open_spreadsheet", DataModelSpreadsheetAction, { force: true });
