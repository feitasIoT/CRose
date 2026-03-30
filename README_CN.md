
# 🌹 CRose (China Rose，月季)一站式轻量化数据引擎
## 连接设备，交付价值

CRose 是一个专为制造业与现代农业打造的集成化数据底座。它封装了从底层的协议采集（Modbus/MQTT）到上层的统计分析、UI展示的全链路能力。

## 🌟 为什么选择 CRose？简单但强大！

1. 平台使用docker-compose一键部署。
2. AI智能生成Node-RED流程。
3. 一个平台轻松维护上千边缘节点。

## 🚀 快速开始

> ## 提示：1.0之前的版本为预览版，不建议生产使用。

### 部署

```
git clone https://github.com/feitasIoT/Crose.git
cd Crose
docker-compose up -d --build
```

你会发现启动了10个容器：
- crose-web
- crose-ai
- crose-db
- gmqtt
- iotdb
- redis
- nodered-prod
- nodered-staging
- verdaccio-prod
- verdaccio-staging

> 虽然启动了不少容器，但你可以在Crose Web中完成所有操作，无需多虑。

### 体验

- 用谷歌、Edge等浏览器访问：http://ip:8069

## 📅 里程碑

### 2026.05
- 集成模型训练框架，支撑用户训练本地专属模型。

### 2026.04
- 高质量提示词与数据集调用大模型生成Node-RED流程服务。

### 2026.03
- 平台基础功能框架。


