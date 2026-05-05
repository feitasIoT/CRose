
# Crose Components
## 容器管理
### 重启策略管理
- 容器的重启策略通过docker-compose.yml文件设置为always，用户可以通过在CRose界面更改某个容器的重启策略值为其他，达到不用修改yml文件来管理容器重启策略的目的。

# webdav
## 用户与目录初始化
- 一个data asset可以有多个目录，因为一个data asset可以有多个data modeling
- 假设data modeling code为test, data asset nick name为ps1，那么创建的目录为：/data/testps1/上传 、/data/testps1/成功 、/data/testps1/失败
- 允许上传的目录：上传，允许查看但不允许上传、删除的目录：成功、失败。

# Node-RED
## Prod和Stage之分
Node-RED是CRose平台的核心应用，Node-RED新版本、一些Package的新版本都在stage上测试，通过之后再升级Prod。