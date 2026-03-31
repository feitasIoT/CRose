import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { Component, onWillStart, useState } from "@odoo/owl";
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
            fileName: "查询结果",
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
            this.state.fileName = record?.name || "查询结果";
            let data = {
                sheets: [{ id: "sheet1", name: "Sheet1" }],
                settings: {},
                revisionId: "START_REVISION",
            };
            if (record?.spreadsheet_binary_data) {
                try {
                    data = decodeBase64Json(record.spreadsheet_binary_data);
                } catch (error) {
                    this.notification.add(`电子表格数据解析失败：${error?.message || error}`, {
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
                this.notification.add("电子表格已打开，但当前没有可显示的单元格数据。", {
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
            this.notification.add("电子表格尚未完成初始化。", {
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
