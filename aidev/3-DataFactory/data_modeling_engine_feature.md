# 功能定义文件
## 名称
- 数据建模引擎（Data Modeling Engine）

## 用户输入
- 需求方
- 提供方
- 描述
- 设备名称
- 协议
  - modbus tcp：需要host、tcp_port、slave_id
  - modbus rtu：需要host、rtu_port、slave_id
  - mqtt：需要host、mqtt_port、mqtt_username、mqtt_password、mqtt_topic
  - smb：需要主机、端口、共享目录、用户名、密码
  - http：需要host、http_port、username、password
  - coap：需要host、coap_port、username、password
- IP地址
- 端口号
- 串口

## 系统默认值输入

## 功能逻辑
- 保存后，系统根据规则创建MQTT Topic
  - 关于使用哪个MQTT Broker？
    - CRose支持配置多个Broker（TODO：评审这个功能），所以产生了使用哪个Broker的问题。
      - 目的是为了分测试环境和生产环境。
      - 目的是支持部署mqtt broker集群，用户可以根据实际情况选择使用哪个broker。（这是不对的，集群应该对外暴露一个地址给CRose）
    - CRose默认使用第一个在线的Broker。
  - 规则：`/{需求方}/{提供方}/{数据建模name}`
  - 示例：`/客户A/供应商B/DM00003`
- 保存后，系统检查需求方和提供方是否存在MQTT用户（通过联系人字段 mqtt_username）
  - 存在用户则忽略
  - 不存在用户，则调用broker的方法来创建用户
- 保存后，系统将broker的连接参数创建到单据的chatter message，以便提供方去设置自己的MQTT client，内容包括：
  - 服务端IP地址
  - 端口
  - 协议，v3.1.1 还是 v5


## smb协议


增加一个query type字段，可以选择data或log，用户选择data，系统去查询iotdb，用户选择log，系统去查询redis。

增加一个query history字段，