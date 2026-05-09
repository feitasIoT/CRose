
# Edge Management

## Edge Nodes
### 增加gateway节点

### 增加边缘节点
- 创建，填写名称、选择网关、填写IP地址。
- 动作-初始化，
  - 创建Node-red实例记录，类型=Remote、名称={节点名称}-NR、节点来自于所属边缘节点、IP地址自动生成域名，例如：nr{record id}.edge.local
  - 调用/api/proxies接口动态新增一个代理，其中name取domain的第一节，type=http，localIP=，localPort=，customDomains使用前面生成的域名。frpc部署在边缘节点所选网关。

`
curl -u admin:admin -X POST http://localhost:7400/api/proxies \
-H "Content-Type: application/json" \
-d '{
  "name": "test-proxy",
  "type": "tcp",
  "localIP": "127.0.0.1",
  "localPort": 8080,
  "remotePort": 6000
}'
`


更新所属gateway的检测流程，检测instance是否包含node-red、docker等服务。如果有node-red，自动创建

### 增加旧节点时
- 旧节点可能需要通过ssh登录

### 增加新节点时

### 状态
边缘节点的状态分多个指标，包括：ip ping结果，docker状态及运行的容器、vnc等。但是系统要做的简单，也就是说正常情况下用户只需要知道正常状态即可，不需要知道ping的数据，docker或vnc的状态，所以，还是要通过节点中的Node-RED实例来获取状态，变化时发送mqtt消息。

## App Store

## Deployments

### local和remote之分
CRose平台有的component包括Node-RED Prod和Node-RED Stage，它们被称为local instance，接入CRose平台的Gateway和Edge node中部署的Node-RED被称为Remote Instance。

### instance status
实例的状态分根据它们所在的环境和网络，分为：
- local instance，通过gmqtt broker
- gateway，同local instance
- remote instance，mqtt桥接，通过gateway来传递状态

### 创建instance
- 类型默认Remote Instance，用户选择Edge Node，系统自动填写Name和Ip Address，用户可以修改
- node-red上增加流程，通过gateway向CRose发布状态。


## Flows