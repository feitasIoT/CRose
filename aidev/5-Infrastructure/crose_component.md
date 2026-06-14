
# Crose Components
## 容器管理
### 重启策略管理
- 容器的重启策略通过docker-compose.yml文件设置为always，用户可以通过在CRose界面更改某个容器的重启策略值为其他，达到不用修改yml文件来管理容器重启策略的目的。

# web

# db

# iotdb

# redis

# gmqtt

# Node-RED
## Prod和Stage之分
Node-RED是CRose平台的核心应用，Node-RED新版本、一些Package的新版本都在stage上测试，通过之后再升级Prod。

# webdav
## 用户与目录初始化
- 一个data asset可以有多个目录，因为一个data asset可以有多个data modeling
- 假设data modeling code为test, data asset nick name为ps1，那么创建的目录为：/data/testps1/上传 、/data/testps1/成功 、/data/testps1/失败
- 允许上传的目录：上传，允许查看但不允许上传、删除的目录：成功、失败。


# nginx
## 为什么
- frp使用域名穿透


# frps
## 为什么
- 边缘节点位于内网，打开流程编辑器等操作需要内网穿透。


# nexus
## 为什么
- 边缘节点位于内网，必须建立私有仓库。
- 能够同时用于docker镜像、Node-red节点。

## 做什么
- docker镜像仓库
- npm代理仓库
- 节点私有仓库


# gogs
## 为什么
- 流程版本管理时引入，没有做深度集成，用户可人工配置，用于流程的远程仓库，后期可能用于CI/CD。