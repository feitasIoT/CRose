
# Edge Management

## Edge Nodes

### 什么是边缘节点？
> 边缘节点是部署在网络边缘、靠近用户或数据源的计算和存储单元，用于实现低延迟、高效率的数据处理和服务交付。边缘节点是边缘计算体系中的核心组成部分，是对边缘网关、边缘控制器、边缘服务器等多种边缘设备的逻辑抽象。它具备实时数据分析、本地存储、网络联接等共性能力，通过将计算资源从传统云数据中心迁移到靠近用户的边缘位置，实现数据处理的低延时和高稳定性。

> 边缘网关是部署在网络边缘的智能设备，负责终端设备与云端的连接，同时具备数据采集、处理、协议转换和本地计算能力。

当前，CRose中边缘节点分为：网关、边缘节点。


### 网关
- 名称
- 系统
- Repository
- IP地址1
- IP地址2
- Docker
- FRPC
- SSH
- VNC
- Node-RED
- MQTT Broker

#### 部署

- CRose能否ping通IP地址1。
- 能否访问Repository。（CRose会检查Repository的状态，所以CRose如果能ping通IP地址1就表示网关可以访问Repository。）
- 如果勾选Docker，确保能够访问Docker接口。
- 如果勾选FRPC，确保有FRPC容器并运行。
- 如果勾选SSH，确保能够使用SSH登录网关。
- 如果勾选VNC，确保有novnc并运行。
- 如果勾选Node-RED，确保有Node-RED容器并运行。
- 如果勾选MQTT Broker，确保有Gmqtt容器并运行。



- 动作-初始化，可以执行多次？
  - 创建Node-red实例记录，类型=Remote、名称={节点名称}-NR、节点来自于所属边缘节点、IP地址自动生成域名，例如：nr{record id}.edge.local
  - 调用/api/proxies接口动态新增一个代理，其中name取domain的第一节，type=http，localIP=，localPort=，customDomains使用前面生成的域名。frpc部署在边缘节点所选网关。
  - 调用gmqtt接口新增一个mqtt client账号，

### 边缘节点
- 名称
- 系统
- Repository
- IP地址
- Docker
- FRPC
- SSH
- VNC
- Node-RED
- MQTT Broker

#### 确认



#### 更新


更新所属gateway的检测流程，检测instance是否包含node-red、docker等服务。如果有node-red，自动创建

### 增加旧节点时
- 旧节点可能需要通过ssh登录

### 增加新节点时

### 状态
边缘节点的状态分多个指标，包括：ip ping结果，docker状态及运行的容器、vnc等。但是系统要做的简单，也就是说正常情况下用户只需要知道正常状态即可，不需要知道ping的数据，docker或vnc的状态，所以，还是要通过节点中的Node-RED实例来获取状态，变化时发送mqtt消息。

## App Store

### 参数
- 参数名称使用/分隔：节点类型/节点属性/节点属性，例如：mqtt out/broker/clientid表示type为mqtt out的节点中broker属性中的clientid属性。
- 参数值支持引用。例如：%record.ip_address% 表示流程下发到的实例的ip_address字段的值。

### mqtt client user
- 使用边缘节点初始化时为Node-RED实例创建的mqtt user。（local instance使用crose component中的user，但是remote instance要使用所属网关的mqtt user）

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

### 管理流程
#### Add Flows from Template

重构流程下发到Node-red实例
要重构的重点文件有三个：nr_instance.py 、data_model.py、 nr_instance_wizard.py。目前nr_instance_wizard是可以下发流程的，包括包含子流程的流程。data modeling也需要下发流程，所以需要重构，重构目标如下：
1. 所有Node-RED接口都写在nr_instance.py中。
2. 下发流程最关键的是parameter的生成。应用商店中的流程已经定义好了要替换的参数，例如：{{record.topic}}。
如果从instance下发流程，record就是instance记录，如果从data modeling下发流程，record就是data modeling记录。
topic就是相应记录上的字段。
3. 从instance下发流程，也就是通过向导下发，用户选择应用之后，系统要将需要替换的参数显示出来，供用户确认。
4. 从data modeling下发流程，用户点击“生成参数”按钮，系统将需要替换的参数的替换结果显示到Parameters列表供用户确认。因为data modeling要填写的数据比较多，而下发向导只是选择应用。
5. 最后，调用接口下发流程。


## Flows

### 来源
边缘实例动作 Synchronize Flow List

### 发布到应用商店（Publish App）
以发布的流程为基础，将 `content`、`configs`、`subflows` 以及 `global` 中被流程引用的节点一起放到 `content` 中，便于下发流程。
例如 `content` 中的节点如果引用了 `global` 中的配置节点，也要一并带上；例如 `redis-command` 节点的 `server` 属性指向 `global` 中的 redis server 节点，这种被引用的 `global` 节点必须包含在发布结果中。
