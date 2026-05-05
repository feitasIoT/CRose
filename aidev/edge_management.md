
# Edge Management

## Edge Nodes
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

## Flows