# 🌹 CRose (China Rose) - All-in-One Lightweight Data Engine

[Chinese Readme](./README_CN.md)  |  [AI Training Framework](./readme/crose_core_tech_framework.jpg)  |  [Edge Management Framework](./readme/crose_edge_management_mqtt_broker_bridge.jpg)

## Connect Datas, Deliver Values

CRose is an integrated data platform designed for manufacturing and modern agriculture. It encapsulates the full stack capabilities from underlying protocol collection (Modbus/MQTT) to upper-level statistical analysis and UI visualization.

![CRose Overview](./readme/crose.gif)

## 🌟 Why Choose CRose? Simple yet Powerful!

🚀 Out-of-the-box full‑stack platform
- A single docker-compose up launches the complete stack: Odoo, Node‑RED, IoTDB, Redis, GMqtt, etc.
- No manual selection, integration, or tuning. Industrial‑grade IoT data collection and management from day one.

📦 Scenario‑based flow template library
- Built‑in templates for 20+ common acquisition scenarios: Modbus RTU/TCP, OPC UA, MQTT, S7, …
- Covers typical equipment such as machine tools, injection molding machines, heat treatment furnaces, environmental sensors—no need to handcraft Node‑RED flows from scratch.

🧠 Natural language → auto‑generated acquisition flows
- Describe your need in everyday language (e.g., “read PLC D100 every 10s and alert if > 80”).
- The system matches templates, fills parameters, and produces executable Node‑RED flows—lowering the low‑code barrier dramatically.

📊 End‑to‑end observability of data acquisition
- Collection health board: see whether each datapoint is being collected and validation results.
- Throughput and storage statistics: total records, messages/sec, time‑series storage footprint.
- Resource monitoring: CPU/Memory/Network trends on edge nodes and servers to flag bottlenecks early.

🌐 Large‑scale edge fleet management
- Batch deployment, flow updates, version rollback, configuration drift detection.
- Designed for hundreds of Raspberry Pis/industrial PCs with fully remote ops.

✅ Native data quality governance
- Schema checks at ingestion (units, ranges, non‑null, etc.), flagging anomalies.
- Data quality reports (missing rate, latency distribution, duplication rate) to underpin trustworthy AI analytics.

![CRose Framework](./readme/crose_framework_en.jpg)

## 🚀 Quick Start

> ## Note: Versions prior to 1.0 are preview versions and are not recommended for production use.

### Deployment

```
git clone https://github.com/feitasIoT/Crose.git
cd Crose
docker-compose up -d --build
# Start AI services
docker compose -f docker-compose-ai.yml up -d
```

Optional: configure a registry prefix for images in `docker-compose.yml`.

```bash
# Example: pull from an internal registry or mirror
export REGISTRY=registry.example.com/
docker compose up -d --build
```

If `REGISTRY` is empty, Compose uses the original image addresses.

### Offline Image Import

If the deployment environment cannot pull images directly, you can first download the image packages (`*.tar`) from a networked machine and then import them into the target Docker host.

Single image package:

```bash
docker load -i your-image.tar
```

Import all image packages in the current directory:

```bash
for f in ./*.tar; do
  docker load -i "$f"
done
```

After the import completes, run:

```bash
docker compose up -d
```

You will find many containers started. The core stack comes from `docker-compose.yml`, and the AI stack comes from `docker-compose-ai.yml`:
- nginx
- frps
- nexus
- crose-web
- crose-db
- gmqtt
- iotdb
- redis
- nodered-prod
- nodered-staging
- crose-ai
- crose-ai-train
- crose-vllm

> Although many containers are started, you can complete all operations in the Crose Web interface without any concerns.

### Getting Started

- Access via browser (Chrome, Edge, etc.): http://ip:8069    Username: admin, Password: crose

> Initial password, please change it immediately!



## 📅 Key Milestones

### 2026.05
- Integrated model training framework to support users in training local proprietary models.

### 2026.04
- High-quality prompts and dataset calls to large models for generating Node-RED flow services.

### 2026.03
- Platform basic functionality framework.
