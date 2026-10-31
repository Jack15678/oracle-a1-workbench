# 复制给 agent 的任务提示词

## 在我的本地电脑运行

```text
请使用 https://github.com/Jack15678/oracle-a1-workbench ，先阅读 README.md 和 AGENTS.md。
识别本机系统/CPU，优先从 GitHub Releases 获取对应免安装包，校验发布的 SHA-256。
不要让我另装 Python、WSL 或 OCI CLI；缺少兼容包时先说明，再按 README 使用源码依赖。
打开首次配置向导，协助我添加 API 公钥并导入匹配的配置预览，不打印私钥。
配置放系统用户数据目录，不进仓库。代理可选，SSH 入站先关闭。
运行 inspect、只读 auth 和 egress，报告 Home Region、执行环境出口 IP、国家、检查时间。
启动 localhost:8766 工作台，只有我明确开始后才请求创建实例。
保留规格上限、可用域遍历、429 退避，不启动并发副本。
创建返回 OCID 后只读核验，确认 RUNNING 后才说已运行。
最后说明启动位置、日志位置、停止和刷新实例状态的方法。
```

## 让云端 bot 在它自己的电脑运行

```text
请使用 https://github.com/Jack15678/oracle-a1-workbench ，阅读 README.md 和 AGENTS.md，
在你自己的持久 Linux 环境运行 Oracle A1 等待任务，不要求我的电脑装软件或保持开机。
先检查终端、系统/CPU架构、持久存储、持续后台执行能力。
优先下载匹配的 GitHub Release 包并核对 SHA-256，使用其中 a1 命令，无需 UI。
源码路径则创建 Python 虚拟环境，安装 requirements.txt，用 python launcher.py。
不满足时告诉我具体缺失条件，不声称已开始长期运行。
告诉我可用的安全文件/secret 提供方式，用它配置 OCI API 凭据和 SSH 公钥。
不要让我把私钥发到公开聊天，不要把凭据写进仓库。

从 config.example.json 生成私人配置，运行 inspect --config、auth --config、egress --config。
开始前报告实际检测的 IP、国家、时间和时区，明确这是你的执行环境出口，
不是我的电脑 IP，不承诺指定国家或固定 IP。SSH 入站可以先关闭。

上述条件就绪且已取得凭据后，我授权你按默认保守规格运行 run --config，
包括必要的专用网络和实例创建。不要扩大规格、并发运行或删除资源。
用可靠的持续任务机制，记录 PID、配置与日志路径，读取 JSON 事件。
收到 OCID 后停止创建并只读核验；仅在 RUNNING 后报告已运行，
附编号、Home Region、规格和核验时间。超时/中断保留 OCID，以 verify 恢复。
遇权限/配额/其他非容量错误停止并报告原因。
不启动 UI，不暴露公网管理端口，给出停止和后续核验命令。
```
