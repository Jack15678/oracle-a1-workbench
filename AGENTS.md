# 给执行和开发 agent

先读 README，确认用户选的是本地 UI 还是云端自主运行。所有代码在此目录；私人配置/运行数据位于系统用户数据目录。不要将父文件夹或用户数据加入 Git。

## 为用户运行

1. 检查 Linux 终端、Python 3.10+、OCI CLI、持久存储与持续执行能力。Windows UI 需要 Windows Python + WSL；云端 bot 直接使用自己的 Linux。如果平台退出对话会回收环境，报告这个限制，不声称任务能继续。
2. 通过平台安全文件/secret 取得授权凭据。不要要求公开粘贴私钥，不打印私钥、完整 OCI 配置或 token。SSH 私钥也需私密持久保存。
3. 从 config.example.json 在用户数据目录创建 config.json，运行 inspect --config。配置路径属于执行环境。
4. 运行 egress --config。在创建前报告 IP、国家、时间与时区；云端明确这是 bot 的出口。不承诺固定 IP 或国家选择；域名分流时，出口探测不是 Oracle 请求源 IP 的直接证据。
5. 根据用户授权运行 run --config，读取 JSON 事件。任务可能创建 VCN/网关/路由/安全列表/子网/实例/启动盘。不要扩大规格、并发运行或缩短退避。
6. 接收 OCID 后停止创建。只有 verification.lifecycle=RUNNING 且 state=ready 才报告已运行，并附编号、区域、shape/shape_config、checked_at。
7. PROVISIONING 是创建已接受。超时/失败/中断保留 OCID，以 verify --config --instance-id 恢复。不得删除 instances.json 来重新抢机，不得自动删除/终止资源。
8. 持续运行使用平台确认支持的任务机制，记录 PID/日志位置，按需跟踪事件并报告结果或具体阻塞项。云端流程不打开 UI，不开放公网管理端口。

任务提示词见 docs/AGENT_PROMPTS.md。run / inspect / egress / verify 均支持 --config；worker 是 UI 内部模式。

## 修改代码

- 使用 Python 标准库和现有静态 UI，无需构建系统。
- 保持代理显式传递，清理继承的代理环境，避免探测与 OCI 配置分离。
- Home Region 从区域订阅识别；资源操作锁定该区域，规格外循环、可用域内循环。
- 不降低资源限制，不吞掉库存查询错误，不移除跨 UI/CLI 的用户级进程锁。
- result 是创建结果；verification 是后续查询，不挤占最近 5 条创建结果。
- 运行 `python -m unittest discover -s tests -v`；测试不依赖云凭据，不创建资源。状态变更补离线测试。
- 发布前检查 staged files 和提交邮箱，排除凭据/个人数据，保留第三方版权声明。
- 不擅自使用子 agent；A/B 测试前询问用户是否需要。
