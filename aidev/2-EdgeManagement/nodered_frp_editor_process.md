# Node-RED 编辑器通过 FRP 打开的技术要点

## 适用场景

本文只描述 `Remote Instance + use_frp=True` 时，从 Odoo 中点击打开 Node-RED 编辑器的实际链路。

## 一句话结论

打开远程 Node-RED 编辑器时，浏览器并不是直接访问边缘节点的 `1880`，而是走下面这条链路：

`Odoo 按钮 -> 前端 iframe -> Nginx /edge-proxy -> frps vhostHTTPPort -> Gateway 上的 frpc http 代理 -> 边缘节点 Node-RED:1880`

只要这条链路里有任意一个端口、Host 头、代理配置没有同步，FRP 打开编辑器就会失效。

## 业务操作流程

1. 用户在 Odoo 的 Node-RED Instance 上点击“Open/Editor”。
2. 后端 `action_open_editor()` 生成编辑器地址。
3. 如果实例是远程实例并且边缘节点启用了 `use_frp`，前端不会直连 `ip:port`，而是改写成 `/edge-proxy/<host>/...`。
4. Nginx 把这个请求转发给 `frps:7080`，并把 `Host` 头改成目标实例域名。
5. frps 根据 `Host` 匹配到对应的 http 代理。
6. Gateway 上的 frpc 再把请求转发到边缘节点的 Node-RED 服务。
7. Node-RED 返回编辑器页面、静态资源、API 和 WebSocket 连接。

## 关键实现点

### 1. 远程实例地址不是边缘节点 IP，而是 FRP 域名

边缘节点初始化时，如果 `use_frp=True`，系统会把远程实例的 `ip_address` 写成：

`nr<edge_node_id>.edge.local`

同时会在 Gateway 的 frpc store 中写入一个 http 代理：

- `name`: `nr<edge_node_id>`
- `type`: `http`
- `localIP`: 边缘节点 IP
- `localPort`: 实例端口，当前代码默认是 `1880`
- `customDomains`: `["nr<edge_node_id>.edge.local"]`

这意味着：

- Odoo 里的远程实例地址，本质上是 FRP 的路由标识，不是浏览器真的去解析的 DNS。
- frps 能不能把请求转发到正确节点，取决于 `Host=nr<edge_node_id>.edge.local` 是否能匹配到 frpc 中的 `customDomains`。

### 2. 前端 iframe 会把地址改写成 `/edge-proxy/...`

前端嵌入编辑器时，FRP 模式下会把原始地址：

`http://nr<id>.edge.local:1880`

改写成：

`http://<当前浏览器主机名>/edge-proxy/nr<id>.edge.local/`

这里有两个重要特点：

- 它使用的是“当前浏览器主机名”。
- 它没有自动带上当前浏览器访问 Odoo 时的端口。

这就是端口重新规划后最容易出问题的地方之一。

## 代理链路与端口分工

### 1. 浏览器 -> Nginx

浏览器访问：

`http://<浏览器主机>:<Nginx系统组件.mapped_port>/edge-proxy/nr<id>.edge.local/`

Nginx 负责：

- 匹配 `/edge-proxy/<host>/<path>`
- 转发到 `http://frps:7080/<path>`
- 设置 `Host: nr<id>.edge.local`
- 透传 `Authorization`
- 透传 `Upgrade/Connection`，保证 WebSocket 正常

### 2. Nginx -> frps

当前容器内配置：

- `frps.bindPort = 7000`
- `frps.vhostHTTPPort = 7080`

注意：

- `7000` 是 frpc 连接 frps 的控制端口。
- `7080` 是 frps 处理 http vhost 的容器内端口。
- Nginx 代理远程 Node-RED 时，打的是 `frps:7080`，不是 `17000`。

### 3. frpc -> 边缘节点 Node-RED

frpc 中登记的 http 代理最终会把请求转发到：

`http://<边缘节点IP>:1880`

所以远程 Node-RED 真实监听端口如果已经不是 `1880`，但 Odoo 里实例 `port`、frpc store 里的 `localPort` 仍然还是 `1880`，打开编辑器就一定失败。

## 当前代码里的固定端口和映射关系

### 容器内固定值

- Node-RED 容器内监听端口：`1880`
- frps 控制端口：`7000`
- frps http vhost 端口：`7080`
- Nginx 容器内端口：`80`

### 当前 docker-compose 的宿主机映射

- Nginx: `10080 -> 80`
- Odoo: `18069 -> 8069`
- frps 控制端口: `17000 -> 7000`
- frps dashboard: `17500 -> 7500`
- 本地 Node-RED prod: `11880 -> 1880`
- 本地 Node-RED staging: `11881 -> 1880`

### 哪些端口对 FRP 打开编辑器真正关键

- 关键的是容器内链路：`Nginx 80 -> frps 7080 -> frpc -> Node-RED 1880`
- `17000` 只影响 frpc 连 frps
- `11880/11881` 只影响本地直接暴露的 Node-RED，不影响远程实例通过 FRP 打开编辑器

## 端口重规划后最容易失效的点

### 1. 浏览器入口端口变了，但 iframe 仍然访问默认 80

前端 FRP 地址是：

`<window.location.protocol>//<window.location.hostname>:<Nginx系统组件.mapped_port>/edge-proxy/...`

其中端口应来自系统组件中的 `Nginx.mapped_port`，而不是浏览器当前访问 Odoo 的端口。

例如用户从下面地址进入 Odoo：

`http://ip:18069`

但 iframe 会去访问：

`http://ip/edge-proxy/...`

而不是：

`http://ip:18069/edge-proxy/...`

如果这里没有使用 Nginx 的映射端口，例如实际应该走 `10080`，但前端仍然走默认 `80/443` 或 Odoo 自己的端口，FRP 编辑器就会直接失败。

### 2. frps 的 vhostHTTPPort 改了，但 Nginx 仍然代理到 7080

Nginx 当前写死转发到：

`http://frps:7080`

如果你改了 frps 的 `vhostHTTPPort`，但没有同步修改 Nginx 配置，FRP http 代理一定不可用。

### 3. 宿主机 frps 端口改了，但 Gateway 上的 frpc 没同步

frpc 连 frps 用的是服务端控制端口，对应当前的：

- frps 容器内：`7000`
- 宿主机暴露：`17000`

如果宿主机侧端口变了，Gateway 上 frpc 的 `serverPort` 没更新，frpc 会掉线，后续所有远程编辑器访问都会失败。

### 4. 边缘节点 Node-RED 端口改了，但实例端口和 frpc localPort 没同步

当前代码创建远程实例和同步 frpc 代理时，默认使用：

- `instance.port = 1880`
- `http.localPort = 1880`

如果边缘节点 Node-RED 实际已经不是 `1880`，但业务数据和 frpc store 还是旧值，frps 能匹配到域名，最终也会转发到错误端口。

### 5. Host 头不对，frps 无法按域名匹配代理

frps 的 http 代理依赖 `Host` 头识别目标代理。

因此下面两项必须同时成立：

- Nginx 必须把 `Host` 改成 `nr<id>.edge.local`
- frpc 里必须存在对应 `customDomains=["nr<id>.edge.local"]`

只要任意一侧不一致，请求就找不到正确代理。

## 最小排查清单

按这个顺序排查最快：

1. 确认边缘节点 `use_frp=True`。
2. 确认远程实例 `ip_address=nr<id>.edge.local`，`port=1880` 或实际 Node-RED 端口。
3. 确认 Gateway 启用了 `is_frpc=True`，且 `frpc_webserver_port`、账号密码正确。
4. 确认 frpc store 中存在 `nr<id>` 对应的 http 代理，`customDomains` 与实例域名一致。
5. 确认 Gateway 上 frpc 仍然能连上 frps 控制端口。
6. 确认 Nginx 仍然把 `/edge-proxy/...` 转发到 `frps:7080`。
7. 确认浏览器实际访问的入口可以命中 Nginx，而不是只命中 Odoo 的 `18069`。
8. 确认边缘节点上的 Node-RED 真实监听端口与 frpc `localPort` 一致。

## 建议的端口规划原则

为避免再次出问题，建议明确区分三层端口：

1. 浏览器入口端口
   由用户访问的平台入口决定，必须能落到 Nginx，并能访问 `/edge-proxy/...`。
2. FRP 服务端端口
   包括 frps 控制端口和 http vhost 端口，Gateway 上 frpc 必须同步。
3. 边缘服务真实端口
   例如远程 Node-RED 实际监听的 `1880`，必须与 frpc `localPort` 和 Odoo 实例端口保持一致。

## 结论

这套 FRP 打开 Node-RED 编辑器的能力，本质依赖 4 个条件同时成立：

- Odoo 中实例域名正确
- 前端能访问 `/edge-proxy/...`
- Nginx 到 frps 的 vhostHTTPPort 正确
- frpc 到边缘节点 Node-RED 的 `localIP/localPort` 正确

这次“容器端口重新规划后 FRP 不可用”，优先怀疑下面 3 个点：

1. 浏览器实际入口没有落到 Nginx，导致 `/edge-proxy/...` 走错端口。
2. frps 的 `7000/7080` 或宿主机映射调整后，Gateway frpc 没同步。
3. 边缘节点 Node-RED 真实端口变了，但 Odoo 实例端口和 frpc `localPort` 还保留 `1880`。
