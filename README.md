# 🌹 CRose (China Rose) - All-in-One Lightweight Data Engine

[中文版](./README_CN.md)

## Connect Devices, Deliver Value

CRose is an integrated data platform designed for manufacturing and modern agriculture. It encapsulates the full stack capabilities from underlying protocol collection (Modbus/MQTT) to upper-level statistical analysis and UI visualization.

## 🌟 Why Choose CRose? Simple yet Powerful!

1. One-click deployment with docker-compose.
2. AI intelligently generates Node-RED flows.
3. Easily manage thousands of edge nodes from a single platform.

## 🚀 Quick Start

> ## Note: Versions prior to 1.0 are preview versions and are not recommended for production use.

### Deployment

```
git clone https://github.com/feitasIoT/Crose.git
cd Crose
docker-compose up -d --build
```

You will find 10 containers started:
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

> Although many containers are started, you can complete all operations in the Crose Web interface without any concerns.

### Getting Started

- Access via browser (Chrome, Edge, etc.): http://ip:8069

## 📅 Key Milestones

### 2026.05
- Integrated model training framework to support users in training local proprietary models.

### 2026.04
- High-quality prompts and dataset calls to large models for generating Node-RED flow services.

### 2026.03
- Platform basic functionality framework.
