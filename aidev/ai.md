
1. 通过Readme.md文件理解CRose平台的6大核心功能，确保接下来的任务不能跑偏。
2. 通过docker-compose.yml和docker-compose-ai.yml文件，了解CRose平台的组件关系和配置，尤其是容器间的通信和数据共享。
3. 接下来的目标是实现完整的模型训练与应用功能，包括流程转换原子数据、数据集管理、训练任务管理、AI Flow，首先根据下面的模型定义，补充完善模型字段、视图等内容。

# fts.ai.training   训练
## fields
- dataset_ids (fts.ai.dataset, m2m)

# fts.ai.prompt   提示词
## fields
- name
- content
- category (fts.ai.category, m2m, 例如：生成、解释)
- is_template (boolean)

# fts.ai.dataset   数据集
将众多dataset.message集合到一起，模型训练时选择多个dataset。
## fields
- name
- message_ids (fts.ai.dataset.message, m2m)
- message_count (compute, count(message_ids))
- category_ratio（计算生成与解释的比例，例如： “7:3”，只考虑message上的生成、解释，其他category不考虑）

# fts.ai.dataset.message   原子数据
chatML这种格式能让模型更好地区分“系统指令”、“用户提问”和“模型回答”，考虑到内容长度，所以分三个字段。
## fields
- name (number, ir.sequence)
- format (例如：ChatML )
- system (text)
- user (text) 
- assistant (text)
- category (fts.ai.category, m2m, 例如：生成、解释)

4. 流程转换原子数据
- fts.nr.flow增加一个方法，用于将流程转换为原子数据
  - 首先，搜索is_template为True的提示词
  - 然后，根据提示词，将一个流程创建为多个原子数据

5. 数据集管理
- 创建数据集，填写名称后，开始添加原子数据。
- 计算message_count\category_ratio

6. 训练任务管理
- 创建任务，选择数据集，填写模型参数之后，点击开始训练。
- 将数据集保存为Llama-factory训练所需的数据集文件，位置参考docker-compose-ai.yml文件。
- 通过命令行启动训练。
- 显示训练结果。

7. AI Flow 
- 知识库
- 向量化
- RAG
- 提示词
- 模型

