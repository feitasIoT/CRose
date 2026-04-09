FROM nodered/node-red-dev:5.0.0-beta.4

WORKDIR /usr/src/node-red

# 安装插件并清理缓存
RUN npm install --no-update-notifier --omit=dev \
    node-red-contrib-modbus \
    node-red-contrib-buffer-parser \
    node-red-contrib-s7 \
    node-red-contrib-redis \
    node-red-contrib-smb2.5 \
    node-red-contrib-spreadsheet-in \
    @feitas/node-red-odoo \
    @flowfuse/node-red-dashboard \
    && npm cache clean --force