
# CRose web menus
- 下面列出的是CRose平台的菜单结构。

## Discuss
- 这是odoo官方的功能菜单，CRose平台依然使用，场景：用户在此与模型进行对话，例如：我今天要干什么？

## Workbench
### Dashboard
- 对应当前的overview页面，改名dashboard
### Alerts
- 平台要帮助用户解决各种采集是不是正常，都有什么问题，这个菜单一目了然，也是用户处理问题的重要入口。

## Edge Management
### Edge Nodes
- 之前的Agents改为Edge Nodes，用户在此管理边缘节点，例如：某产线的树莓派等。
### App Store
- 流程库，预置的场景模板都在这里。
### Deployments
- 之前的Instances改为Deployments用户在此管理边缘节点中部署的Node-RED实例。
### Flows
- 用户在此管理边缘节点中部署的Node-RED流程。

## Data Factory
### Assets
- 用户在此管理设备、传感器、数据源等。

### Modeling
- 用户在此管理数据模型，例如：某某产品质检文件数据模型、某某设备温控数据模型等。

## AI Lab
### Knowledge
- 就是目前的knowledge documents，管理知识库文档。  目前的knowledge chunks菜单删除
### Datasets
- 管理数据集
### Model Training
- 管理模型训练任务。
### Models
- 用户在此管理AI模型，例如：Llama-Factory模型。
### Prompts
- 用户在此管理提示词。

## Infrastructure
### System Components
- 用于管理系统组件，例如：数据库、缓存、Node-RED、MQTT Broker等。
### Agent Package
- 这里用于存放管理边缘节点的golang程序。边缘节点部署后，从这里下载程序。

### MQTT
- 用于管理Gmqtt Broker，或者说是为gmqtt提供一个图形化界面，主要是查看数据，例如：用户、主题等。
#### MQTT Users
#### Topics
#### Synchronize Topics Now

## Apps
- odoo原有的菜单

## Settings
- odoo原有的菜单，CRose平台的一些设置要从Infrastructure转移到此。
### Tags
- 用于Node-RED流程的标签？
### Prompt Categories
- 用于管理提示词分类，例如：生成、解释等。
