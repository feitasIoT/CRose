# 功能定义文件
## 名称：CRose管理gmqtt
- Gmqtt是用Go语言实现的一个具备灵活灵活扩展能力，高性能的MQTT broker，其完整实现了MQTT V3.x和V5协议。 具体的说明见本项目gmqtt/README.md
- Gmqtt没有图形化管理界面（通过插件实现了 ip:8082/metrics），所以CRose通过其提供的HTTP接口来实现管理界面。

## 接口清单
通过对 `e:\workspace\odoo-19.0\gmqtt` 源码及 Swagger 定义文件的分析，gmqtt 提供的 HTTP 接口主要由 `auth`、`admin` 和 `federation` 三个插件提供。

默认情况下，HTTP 服务监听在 **`127.0.0.1:8083`**。以下是完整的接口列表：

### **1. 账户管理 (Auth 插件)**
用于管理 MQTT 客户端的登录凭据。

| 方法 | 路径 | 功能描述 |
| :--- | :--- | :--- |
| `GET` | `/v1/accounts` | 列出所有账户（支持 `page_size` 和 `page` 分页） |
| `GET` | `/v1/accounts/{username}` | 获取指定用户名的账户信息 |
| `POST` | `/v1/accounts/{username}` | **创建或更新**账户密码。如果账户不存在则创建 |
| `DELETE` | `/v1/accounts/{username}` | 删除指定账户 |

---

### **2. 客户端管理 (Admin 插件)**
用于监控和管理当前连接到 Broker 的客户端。

| 方法 | 路径 | 功能描述 |
| :--- | :--- | :--- |
| `GET` | `/v1/clients` | 列出所有在线客户端信息 |
| `GET` | `/v1/clients/{client_id}` | 获取特定客户端的详细状态（IP、版本、流量统计等） |
| `DELETE` | `/v1/clients/{client_id}` | 强制断开指定客户端的连接（可选参数 `clean_session`） |

---

### **3. 消息发布 (Admin 插件)**
允许通过 HTTP 接口直接向 MQTT 主题推送消息。

| 方法 | 路径 | 功能描述 |
| :--- | :--- | :--- |
| `POST` | `/v1/publish` | 向指定 Topic 发布消息。支持设置 `payload`、`qos`、`retained` 等 |

---

### **4. 订阅管理 (Admin 插件)**
用于查看和手动干退客户端的订阅关系。

| 方法 | 路径 | 功能描述 |
| :--- | :--- | :--- |
| `GET` | `/v1/subscriptions` | 列出系统中所有的订阅关系 |
| `GET` | `/v1/filter_subscriptions` | 根据 `client_id`、`topic_name` 或订阅类型过滤订阅 |
| `POST` | `/v1/subscribe` | 强制为某个客户端添加主题订阅 |
| `POST` | `/v1/unsubscribe` | 强制取消某个客户端的主题订阅 |

---

### **5. 集群/联邦管理 (Federation 插件)**
当 gmqtt 运行在集群模式时使用。

| 方法 | 路径 | 功能描述 |
| :--- | :--- | :--- |
| `GET` | `/v1/federation/members` | 列出集群中所有节点的状态 |
| `POST` | `/v1/federation/join` | 指令当前节点加入已有的集群 |
| `POST` | `/v1/federation/leave` | 当前节点优雅退出集群 |
| `POST` | `/v1/federation/force_leave` | 强制将某个故障节点剔除出集群 |

---

### **提示**
*   **交互方式**：所有接口均接收并返回 `application/json` 格式的数据。
*   **配置验证**：你可以在 [default_config.yml](file:///e:/workspace/odoo-19.0/gmqtt/cmd/gmqttd/default_config.yml) 的 `api.http` 部分修改端口或绑定的 IP 地址。
*   **详细参数**：如果需要查看具体的 JSON 请求体结构，可以参考各插件目录下的 `swagger/*.swagger.json` 文件。


## 功能1：用户管理

### MQTT用户
- 模型：fts.mqtt.user
- 字段
  - 用户名 Name
  - 密码 Password
  - MQTT Broker
  - 状态  status  激活 | 停用
  - 联系人  partner_id
- 视图
  - 列表
  - 搜索，搜索用户名，可以按状态分组，可以按所属Broker分组。（注意：分组不要加string，会引起报错，要参照已有的搜索视图。）
- 约束
  - 用户名、联系人联合唯一约束。

### 同步gmqtt的用户清单
- 用户点击Broker（fts.mqtt.broker）动作（同步用户），系统调用接口同步用户数据，同步完成后跳转到用户列表。
- 同步逻辑：broker有的用户，CRose没有则创建，状态为“激活”。 CRose有的用户，但是broker没有，CRose则标记为“停用”，不需要删除。

### 创建gmqtt用户
- 为Broker创建一个创建用户的方法，参数包括：用户名、Broker、联系人。
- 系统随机生成8位数字、字母组合的密码。
- 用户创建成功后通知用户系统生成的密码。

### CRose联系人显示MQTT用户名
- 为res.partner模型增加一个字段：mqtt_username
- 该字段显示在联系人表单category_id字段的下方