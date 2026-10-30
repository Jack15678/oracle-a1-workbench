# Oracle A1 Workbench

本地工作台 + Linux 命令行执行器，用于等待 Oracle Cloud Ampere A1 容量。自动识别 **Home Region**，遍历可用域，显示当前规格、出口、最近 5 次创建请求与实例核验结果。Python 标准库实现，无 Node 构建步骤。

**创建接口返回 OCID 只代表“创建已接受”。只有后续只读查询确认 `RUNNING`，才显示“已运行”。**

| 使用方式 | 运行在哪 | 入口 |
| --- | --- | --- |
| 本地工作台 | 你的 Windows + WSL，或持久 Linux 电脑 | [本地使用](#本地使用) |
| 云端 agent / cloud bot | bot 自己的持久 Linux 环境 | [云端 agent 使用](#云端-agent-使用) |

可将仓库链接交给 agent，并附上 [可复制的任务提示词](docs/AGENT_PROMPTS.md)。[AGENTS.md](AGENTS.md) 说明执行顺序和状态判断。Muse、Grok bot 等平台是否支持持续运行，需要由实际执行环境检查，不能仅凭产品名称判断。

## 本地使用

### 1. 获取项目与环境

下载 ZIP 解压，或：

```sh
git clone https://github.com/Jack15678/oracle-a1-workbench.git
cd oracle-a1-workbench
```

Windows 主机需要 Python 3.10+（`python --version`）。管理员 PowerShell 检查/安装 WSL：

```powershell
wsl --list --verbose
# 没有 Ubuntu 时才安装，完成后按系统提示重启并设置 Linux 用户
wsl --install -d Ubuntu-24.04
```

下面在 **Ubuntu / Linux 终端**执行。已有 OCI CLI 可跳过安装：

```bash
sudo apt update
sudo apt install -y python3 python3-venv openssh-client ca-certificates
python3 -m venv ~/.local/share/oracle-a1/venv
~/.local/share/oracle-a1/venv/bin/python -m pip install --upgrade oci-cli
~/.local/share/oracle-a1/venv/bin/oci --version
```

执行器查找 PATH、`~/.local/bin/oci`、`~/bin/oci` 和上面的虚拟环境。无需 jq、Docker 或 PHP。参考 [Oracle CLI 安装文档](https://docs.oracle.com/en-us/iaas/Content/API/SDKDocs/cliinstall.htm)。

### 2. 准备自己的凭据

在 OCI 控制台用户设置中添加 API Signing Key，将**私钥**放到执行环境的 `~/.oci/`；把 Configuration file preview 保存为 `~/.oci/config`，修改 `key_file` 为 Linux 中私钥的实际路径。以下字段全部替换为自己的值：

```ini
[DEFAULT]
user=YOUR_USER_OCID
fingerprint=YOUR_API_KEY_FINGERPRINT
tenancy=YOUR_TENANCY_OCID
region=YOUR_SUBSCRIBED_REGION
key_file=/absolute/path/to/your/api_private_key.pem
```

Windows 文件在 WSL 下通常位于 `/mnt/c/Users/<Windows用户名>/Downloads/`，复制时使用自己的文件名。不要把凭据放进项目目录。

```bash
mkdir -p ~/.oci ~/.ssh
chmod 700 ~/.oci ~/.ssh
chmod 600 ~/.oci/config ~/.oci/api_private_key.pem  # 按实际文件名调整
# SSH 登录密钥与 OCI API 密钥不同；不要覆盖已有密钥
test -f ~/.ssh/oracle || ssh-keygen -t ed25519 -f ~/.ssh/oracle -N ""
```

执行器先使用配置里的区域查询 [区域订阅](https://docs.oracle.com/en-us/iaas/tools/oci-cli/latest/oci_cli_docs/cmdref/iam/region-subscription/list.html)，识别唯一可用的 Home Region，然后将资源操作锁定到它。配置区域必须能用于认证和查询订阅，不要求东京。

### 3. 打开工作台

Windows 双击 `start-workbench.cmd`，或在项目目录运行：

```powershell
python workbench.py --port 8766 --open
```

Linux 运行 `python3 workbench.py --port 8766`，在同一台机器打开 `http://127.0.0.1:8766`。Windows UI 通过 WSL 启动执行器，配置路径指向 WSL 文件系统。

依次点击“检查环境”“检测出口”，确认配置后“开始抢机”。环境检查仅检查文件/CLI；认证、区域和资源权限由启动时的预检确认。页面可修改的字段与 [config.example.json](config.example.json) 一致。

### 4. 代理与 SSH

- `proxy_url` 留空直连，或填写执行环境可访问的 HTTP/HTTPS 代理，例如 `http://127.0.0.1:7890`。不接受带账号密码的 URL。
- WSL NAT 下的 `127.0.0.1` 通常不是 Windows 主机，需填写可达的主机地址和监听端口。Clash 允许局域网连接会扩大监听范围，应将监听/防火墙限制在所需网络。
- 节点由代理软件选择。“检测出口”从执行器访问 Cloudflare Trace，返回 IP、国家代码、时间，与 OCI 使用相同代理配置。代理按域名分流时，还需查看 Oracle 请求的规则/连接日志；探测 IP 不是 OCI 对源 IP 的直接回显。
- `trusted_ip` 留空时关闭 SSH 入站，仍可创建实例并写入公钥。提前允许登录时，填写 **SSH 客户端**连接服务器时的公网 IPv4 `/32`，它与 API 代理出口是两回事。
- 实例已建成后，修改此字段不会更新云端规则。届时在 OCI 控制台配置安全列表/NSG 和系统防火墙，并保存好自己的 SSH 私钥。

### 5. 停止与状态核验

“停止任务”停止本地轮询/核验，不删除云资源。CLI 用 Ctrl+C 或向该任务 PID 发送 SIGTERM。关闭网页不停止服务；退出服务、睡眠或关机将中断执行。重新打开 UI 不会自动恢复创建循环。

收到实例 OCID 后不再发送创建请求；每 15 秒只读查询，最长等待 10 分钟：

| 状态 | 含义 / 下一步 |
| --- | --- |
| 创建已接受，正在部署 | 已有 OCID，仍为 `PROVISIONING` / `STARTING` |
| 已运行 | 只读查询确认 `RUNNING`，页面显示核验时间 |
| 状态待核验 | 超时、查询失败、主动停止或其他非运行状态；保留 OCID，用“刷新实例状态” |
| 部署已结束 | `TERMINATING`、`TERMINATED`、`FAILED` 或 `FAULTY`；记录状态并停止，不删除资源 |

“刷新实例状态”调用本地 `POST /api/verify`，异步执行一次只读核验，不创建/启动/重启/删除云资源。旧版 `success + PROVISIONING` 记录导入为待核验。

### 6. 私人数据位置

| 环境 | 数据目录 |
| --- | --- |
| Windows UI | `%LOCALAPPDATA%\OracleA1Workbench` |
| Linux / WSL | `$XDG_STATE_HOME/oracle-a1-workbench`，未设置时为 `~/.local/state/oracle-a1-workbench` |

UI 保存 `state.json`、`events.jsonl`；执行器保存 `engine-events.jsonl` 和按 tenancy/区域记忆的 `instances.json`。后者用于重启后继续核验已接受的实例；**不要为了重试而删除此记录**。日志含出口/实例标识，分享前脱敏。旧版用户应关闭服务后，将原 `.oracle-workbench` 内容移到对应 UI 数据目录。

特殊环境可用 `workbench.py --data-dir /path/to/private-state` 指定 UI 数据目录，例如在 WSL 中使用挂载的 Windows 用户数据目录。它只影响 UI 状态，执行器的防重复记录仍位于 Linux 用户数据目录。

## 云端 agent 使用

agent 在**它自己的持久 Linux 电脑**上运行，用户本机无需装软件或保持开机。此流程不需要 UI，不暴露 `8766` 或其他公网管理端口。

### 1. 确认能力与凭据

先检查 Linux 终端、Python 3.10+、依赖安装、持久存储、持续运行和后续查日志/停止任务的能力。短命沙箱、对话结束即关闭的环境不满足条件；`nohup` 无法让被平台销毁的电脑继续运行。若不满足，先告知用户具体缺失条件。

按上面的 Linux 步骤安装 OCI CLI。用户通过平台支持的安全文件/secret 机制提供凭据，保存到私有持久目录并设置权限。**不要让用户将私钥粘贴到公开聊天、issue 或仓库。** SSH 私钥也应私密持久保存。

### 2. 配置并先报告出口

在仓库目录执行：

```bash
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/oracle-a1-workbench"
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"
cp config.example.json "$STATE_DIR/config.json"
chmod 600 "$STATE_DIR/config.json"
# 编辑 JSON，指向自己的 OCI 配置和 SSH 公钥；不需要代理时留空
python3 oracle_grab.py inspect --config "$STATE_DIR/config.json"
python3 oracle_grab.py egress --config "$STATE_DIR/config.json"
```

开始请求前，agent 报告 `ip`、`country`、`checked_at`（Unix 秒，转换成人类可读时间并注明时区）。这是 **bot 的出口，不是用户本机 IP**。本方案不承诺国家选择、固定 IP 或平台重启后出口不变。出口检测失败时先解决连通性。

### 3. 运行并跟踪 JSON 事件

首次建议前台确认预检：

```bash
python3 -u oracle_grab.py run --config "$STATE_DIR/config.json"
```

已确认持续执行能力后，使用平台任务管理器；普通持久 Linux 也可用：

```bash
nohup python3 -u oracle_grab.py run --config "$STATE_DIR/config.json" \
  > "$STATE_DIR/console.jsonl" 2> "$STATE_DIR/console.stderr.log" < /dev/null &
echo $! > "$STATE_DIR/worker.pid"
tail -n 20 "$STATE_DIR/console.jsonl"
# 停止前先确认 worker.pid 对应当前任务，再 kill -TERM <该PID>
```

stdout 是逐行 JSON。关注 `region`、`request`、`result`、`verification`、`state`。进程退出或 `result.outcome=accepted` 不等于已运行。只有 `verification.lifecycle=RUNNING` 后的 `state=ready` 才可报告成功，附实例 OCID、区域、`shape` / `shape_config`、核验时间。

中断后用保存的编号单独核验：

```bash
python3 oracle_grab.py verify --config "$STATE_DIR/config.json" \
  --instance-id 'ocid1.instance.REPLACE_WITH_YOUR_INSTANCE_OCID'
```

`verify` 只查一次，部署未完成返回待核验。再次 `run` 时，已有该账户/区域实例记录则只恢复核验，不重复创建。创建前也会扫描 Home Region 全部 compartment 中未终止的 A1（包括 STOPPED）；任何查询失败就停止。

退出码：`0` 正常结束（结合事件判断 ready 或 existing）；`1` 错误/失败；`3` 轮数耗尽；`4` 待核验；`130` 创建轮询被停止。`worker` / `worker-verify` 是 UI 内部协议。

## 配置与兼容入口

| JSON 字段 | 默认 / 作用 |
| --- | --- |
| `distro` | `Ubuntu-24.04`；仅 Windows UI 使用 |
| `config_file`, `profile` | `~/.oci/config`、`DEFAULT` |
| `compartment` | 空；使用 tenancy/root |
| `ssh_key`, `trusted_ip` | `~/.ssh/oracle.pub`、空（SSH 入站关闭） |
| `proxy_url`, `subnet` | 空；直连 / 自动创建专用公共子网 |
| `name` | `oracle-a1` |
| `presets` | `2/12`、`1/6`、`1/4`；从大到小，每项先遍历全部可用域 |
| `round_min`, `round_max` | `120`、`300` 秒，每轮随机；429 至少退避 1800 秒 |
| `max_rounds` | `0` 无限；正整数限制轮数 |
| `boot_gb` | `50`；启动盘与已有启动盘/块存储合计上限 200 GB |

`run`、`inspect`、`egress`、`verify` 均支持 `--config`。传入时以 JSON 为准，否则读取环境变量：`COMPARTMENT_OCID`、`SSH_PUBLIC_KEY_FILE`、`TRUSTED_IP_CIDR`、`OCI_PROFILE`、`OCI_CLI_CONFIG_FILE`、`OCI_PROXY_URL`、`SUBNET_OCID`、`ROUND_MIN_SEC`、`ROUND_MAX_SEC`、`MAX_ROUNDS`。`LOG_FILE` 可覆盖执行器日志路径。旧入口仍可用：`bash oci-always-free-grab.sh --config /path/to/config.json`。

## 常见问题

- **容量不足 / 429**：容量不足换下一个可用域，再换规格；429 立即结束本轮并延长等待。
- **权限/配额/网络/查询错误**：停止并保留原始原因，不当作容量不足。预检需读取 Home Region 全部 compartment 的实例与存储，权限不足会停止。
- **已有 A1**：包含停止中的实例。工具不自动换机或删资源；已记住的实例即使终止，也不自动重新创建。
- **无法 SSH**：检查私钥、入站规则、系统防火墙。创建与登录是两项操作，默认入站关闭。
- **Oracle 网页 technical difficulties**：网页错误不能证明实例不存在。使用只读 `verify` 查询实例 API。
- **费用**：本工具仅尝试 A1，每台上限 2 OCPU / 12 GB，并检查总存储上限；不计算账单，是否收费以你的账户条款、已有资源和控制台为准。

## 开发与许可

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile oracle_grab.py workbench.py runtime.py
# 可选前端语法检查；运行 UI 不需要 Node
node --check web/app.js
```

测试使用模拟 OCI，不创建云资源；CI 在 Linux 和 Windows 运行，不需要凭据。原创代码采用 [MIT License](LICENSE)。`web/vendor/` 的 GSAP 与从 Fontshare 官方 CDN 加载的 Satoshi 字体保留各自许可，不被 MIT 重新授权；仓库不分发字体文件，字体无法加载时回退系统字体，见 [第三方声明](THIRD_PARTY_NOTICES.md)。这是独立项目，不是 Oracle 官方产品。
