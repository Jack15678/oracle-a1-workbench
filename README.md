# Oracle A1 Workbench

**[下载最新发行包](https://github.com/Jack15678/oracle-a1-workbench/releases/latest)** · [云端 agent 使用](#云端-agent-使用) · [可复制的 agent 提示词](docs/AGENT_PROMPTS.md)

等待 Oracle Cloud Ampere A1 容量的本地工作台。显示当前规格、网络出口、最近 5 次创建结果和实例状态；自动识别 Home Region，遍历可用域。Windows、macOS、Linux 共用界面和执行逻辑。

**发行包内置 Python、Oracle 官方 SDK 和运行依赖，无需安装 Python、WSL、OCI CLI、Node 或 Docker。** 打开后使用本机浏览器显示界面，服务仅监听 `127.0.0.1`。仍需要自己的 Oracle Cloud 账户和一次 API 授权。

创建接口返回 OCID 只表示“创建已接受，正在部署”。只有后续只读查询确认 `RUNNING`，才显示“已运行”。

## 下载即用

在 [GitHub Releases](https://github.com/Jack15678/oracle-a1-workbench/releases) 的 **Assets** 中选择发行包；“Source code”是开发源码，不是免安装包。

| 系统 | 下载文件名后缀 | 解压后的入口 |
| --- | --- | --- |
| Windows 10 / 11，Intel / AMD 64 位 | `windows-x64.zip` | `A1Workbench/A1Workbench.exe` |
| macOS 15+，Apple Silicon（M 系列） | `macos-arm64.zip` | `A1Workbench.app` |
| macOS 15+，Intel | `macos-x64.zip` | `A1Workbench.app` |
| Ubuntu 22.04+，x86-64 | `linux-x64.tar.gz` | `A1Workbench/A1Workbench` |
| Ubuntu 22.04+，ARM64 | `linux-arm64.tar.gz` | `A1Workbench/A1Workbench` |

Windows 请先解压整个文件夹，不能只取 `.exe`；`_internal` 与 `a1.exe` 也是运行所需。macOS 可把整个 `.app` 拖进“应用程序”。Linux 解压后运行入口，无桌面时使用 CLI。Linux 包在 Ubuntu 22.04 原生构建验证，需要 glibc 2.35+，不适用于 Alpine/musl；Debian 12 满足该基线，但未在 Debian CI 实测。macOS 15 是本项目 CI 验证基线，旧版本未验证。Windows ARM64 当前没有专用包。

这些包目前**未做 Windows 代码签名或 Apple 公证**，可能出现未知开发者提示。先确认下载来自本仓库 Releases，可核对同页 `SHA256SUMS.txt`：

```powershell
# Windows；与 SHA256SUMS.txt 中同名文件的值比较
Get-FileHash .\oracle-a1-workbench-0.2.0-windows-x64.zip -Algorithm SHA256
```

```sh
# macOS；Linux 可用 sha256sum
shasum -a 256 oracle-a1-workbench-0.2.0-macos-arm64.zip
```

确认来源后，Windows 可在系统提示中选择“更多信息 → 仍要运行”；macOS 可在“系统设置 → 隐私与安全性”中为这一个应用选择“仍要打开”。若设备管理策略不允许，使用源码入口或联系管理员。无需关闭全局安全保护。参考 [Apple 应用安全说明](https://support.apple.com/en-us/102445)。

### 首次连接账户

1. 打开 **A1Workbench**，浏览器进入 `http://127.0.0.1:8766`，使用首次配置向导。
2. 生成本机 API 密钥，把向导显示的**公钥**添加到 Oracle 控制台用户的 API Keys。私钥保存在本机私人目录。
3. 将 Oracle 的 Configuration file preview 粘贴到向导。已有密钥时也可选择已有 `.pem` 私钥并粘贴匹配的配置预览。程序校验指纹，匹配才保存。
4. 选择直连、检测到的本机代理或手动代理，检查出口和账户连接。程序自动识别 Home Region，并准备独立的 SSH 登录密钥。
5. 完成向导后在工作台确认规格与出口，点击“开始抢机”。向导不会自动创建云资源。

无需编辑配置文件或理解 Linux 路径。高级配置仍可在工作台修改。API 私钥、SSH 私钥用途不同，都需自己私密保存。浏览器提交的凭据只发送到本机服务，不进入仓库。

### 网络、SSH、停止

- 代理可选，例如 `http://127.0.0.1:7890`；不能包含账号密码，只选择自己认可的代理。
- “检测出口”访问 Cloudflare Trace，与 OCI 使用同一代理配置。若代理按域名分流，探测 IP 不是 Oracle 对源 IP 的直接回显；可检查代理软件的 Oracle 连接记录。
- SSH 放行可以暂缓：`trusted_ip` 留空关闭 SSH 入站，同时向实例写入公钥。后续登录时在 OCI 安全列表/NSG、系统防火墙中放行 **SSH 客户端**的公网 IPv4 `/32`，它与 API 代理出口不同。实例建成后修改本地字段不会改云端规则。
- “停止任务”停止轮询或核验，不删除资源；“退出应用”同时退出本机服务。关闭网页不停止服务；退出应用、睡眠或关机会中断执行，再次打开不自动恢复创建。CLI 用 Ctrl+C 或向对应任务 PID 发送 SIGTERM。

收到 OCID 后立即停止创建，每 15 秒只读查询，最多等 10 分钟：

| 状态 | 含义 / 下一步 |
| --- | --- |
| 创建已接受，正在部署 | 已有 OCID，仍为 `PROVISIONING` / `STARTING` |
| 已运行 | 查询确认 `RUNNING`，显示核验时间 |
| 状态待核验 | 超时、查询失败或停止；保留 OCID，点击“刷新实例状态” |
| 部署已结束 | 终止或失败；记录实际状态并结束，不删资源 |

“刷新实例状态”只读查询一次，不创建、启动或重启实例。旧版 `success + PROVISIONING` 会导入为待核验。

## 私人数据与升级

| 系统 | 默认数据目录 |
| --- | --- |
| Windows | `%LOCALAPPDATA%\OracleA1Workbench` |
| macOS | `~/Library/Application Support/OracleA1Workbench`；已有旧 XDG 目录时沿用它 |
| Linux | `$XDG_STATE_HOME/oracle-a1-workbench`，未设置时为 `~/.local/state/oracle-a1-workbench` |

配置、凭据、记录、日志在私人数据目录，不在安装文件夹。升级前停止任务和服务，替换应用文件夹，保留私人数据目录；不要同时运行旧版、新版。

`state.json` 是 UI 状态，`events.jsonl` / `engine-events.jsonl` 是日志，`instances.json` 记忆各账户/区域已接受的实例。**不要删除实例记录来重新抢机。** 日志含实例标识和 IP，分享前脱敏。Windows GUI 启动错误写入 `launcher.log`。`a1 gui --data-dir /path/to/private-ui-state` 只改变 UI 状态位置，执行器记录仍在系统数据目录。

旧 Windows + WSL 用户可继续在 WSL 中使用源码入口和原有 Linux 配置；Windows 原生包需通过向导导入匹配的凭据，WSL 中的 `~/.oci` / `/home/...` 路径不会自动变成 Windows 路径。若原数据目录已有实例记录，向导会保留 OCID，并只读验证新凭据能访问原实例后才完成账户关联；失败时可重新导入，不会创建另一台。导入 API 密钥不会更换已有实例的 SSH 公钥，登录仍使用原来的 SSH 私钥。不要删除旧记录来测试迁移。

## 云端 agent 使用

agent 在**自己的持久 Linux 电脑**运行，用户本机无需安装或保持开机。无需 UI、桌面或公网管理端口。Muse、Grok bot 等是否满足条件，要检查实际执行环境。

### 1. 检查环境与安全凭据渠道

需要持久 Linux 终端、可写持久目录、持续运行和之后查日志/停止任务的能力。短命沙箱或对话结束即回收的环境不满足条件，`nohup` 无法阻止平台销毁电脑；缺少时告知用户具体限制。

下载匹配架构的包并解压，下文 `./a1` 使用实际可执行文件路径。无需安装 Python/OCI CLI。也可按 [源码运行](#源码运行与开发) 安装依赖，用 `python launcher.py` 替换 `./a1`。

通过平台的**安全文件或 secret**机制取得 OCI 配置和私钥，保存到私密持久目录。不要让用户把私钥粘贴进公开聊天、issue 或仓库。SSH 私钥也需持久私密保存；无 UI 时可用 `ssh-keygen`：

```sh
mkdir -p ~/.oci ~/.ssh
chmod 700 ~/.oci ~/.ssh
# 安全文件写入完成后，按实际文件名设置权限
chmod 600 ~/.oci/config ~/.oci/api_private_key.pem
test -f ~/.ssh/oracle || ssh-keygen -t ed25519 -f ~/.ssh/oracle -N ""
```

OCI 配置示例，替换所有占位值，路径属于 bot 执行环境：

```ini
[DEFAULT]
user=YOUR_USER_OCID
fingerprint=YOUR_API_KEY_FINGERPRINT
tenancy=YOUR_TENANCY_OCID
region=YOUR_SUBSCRIBED_REGION
key_file=/absolute/path/to/your/api_private_key.pem
```

### 2. 配置、检测和报告出口

```sh
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/oracle-a1-workbench"
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"
cp /path/to/package/config.example.json "$STATE_DIR/config.json"
chmod 600 "$STATE_DIR/config.json"
# 编辑 JSON，指向自己的 OCI 配置和 SSH 公钥；代理可留空
./a1 inspect --config "$STATE_DIR/config.json"
./a1 egress --config "$STATE_DIR/config.json"
./a1 auth --config "$STATE_DIR/config.json"
```

`inspect` 只查本地依赖/文件；`auth` 只读验证账户与 Home Region，都不创建资源。启动前向用户报告 `ip`、`country`、`checked_at`（Unix 秒，转换为时间并标明时区）。明确这是 **bot 的出口，不是用户电脑的 IP**；不承诺指定国家、固定 IP 或重启后出口不变。探测失败先处理连通性。

### 3. 持续执行与 JSON 事件

取得用户创建授权后，先前台检查预检：

```sh
./a1 run --config "$STATE_DIR/config.json"
```

确认环境支持持续执行后用平台任务管理器，普通持久 Linux 也可：

```sh
nohup ./a1 run --config "$STATE_DIR/config.json" \
  > "$STATE_DIR/console.jsonl" 2> "$STATE_DIR/console.stderr.log" < /dev/null &
echo $! > "$STATE_DIR/worker.pid"
tail -n 20 "$STATE_DIR/console.jsonl"
# 停止前核对 worker.pid 对应当前任务，再 kill -TERM <该PID>
```

stdout 为逐行 JSON，关注 `region`、`request`、`result`、`verification`、`state`。`result.outcome=accepted` 或进程退出不代表已运行。只有 `verification.lifecycle=RUNNING` 后的 `state=ready` 才报告成功，附 OCID、区域、`shape` / `shape_config` 和核验时间。

```sh
./a1 verify --config "$STATE_DIR/config.json" \
  --instance-id 'ocid1.instance.REPLACE_WITH_YOUR_INSTANCE_OCID'
```

`verify` 只查一次。再次 `run` 时，已有该账户/区域记录只恢复核验，不重复创建。创建前也扫描 Home Region 全部 compartment 的未终止 A1，包括 STOPPED；查询失败就停止。

退出码：`0` 正常结束（结合事件判断 ready/existing）；`1` 错误/失败；`3` 轮数耗尽；`4` 待核验；`130` 轮询被停止。`worker`、`worker-verify`、`oci-api` 是内部协议。

## 配置与兼容入口

| JSON 字段 | 默认 / 作用 |
| --- | --- |
| `config_file`, `profile` | `~/.oci/config`、`DEFAULT`；向导保存实际路径 |
| `compartment` | 空，使用 tenancy/root |
| `ssh_key`, `trusted_ip` | `~/.ssh/oracle.pub`、空（SSH 入站关闭） |
| `proxy_url`, `subnet` | 空，直连 / 自动创建专用公共子网 |
| `name` | `oracle-a1` |
| `presets` | `2/12`、`1/6`、`1/4`；从大到小，每项先遍历全部可用域 |
| `round_min`, `round_max` | `120`、`300` 秒；每轮随机，429 至少退避 1800 秒 |
| `max_rounds` | `0` 无限；正整数限制轮数 |
| `boot_gb` | `50`；启动盘与已有启动盘/块存储合计上限 200 GB |
| `distro` | 旧 Windows/WSL 兼容字段；新安装原生执行，无需填写 |

`run`、`inspect`、`egress`、`auth`、`verify` 都支持 `--config`。未传时沿用环境变量：`COMPARTMENT_OCID`、`SSH_PUBLIC_KEY_FILE`、`TRUSTED_IP_CIDR`、`OCI_PROFILE`、`OCI_CLI_CONFIG_FILE`、`OCI_PROXY_URL`、`SUBNET_OCID`、`ROUND_MIN_SEC`、`ROUND_MAX_SEC`、`MAX_ROUNDS`。`LOG_FILE` 可改日志位置。源码旧入口 `python oracle_grab.py …` 和 `bash oci-always-free-grab.sh --config …` 仍可用。

## 常见问题

- **没有容量 / 429**：容量不足先换可用域，再换规格；429 结束本轮并延长等待。
- **权限 / 配额 / 网络错误**：停止并显示原因，不当作容量不足。需有读取 Home Region 全部 compartment 实例与存储的权限。
- **已有 A1**：包含停止中的实例。不换机、不删资源；记住的实例即使终止，也不自动重建。
- **无法 SSH**：检查私钥、入站规则和系统防火墙。默认关闭 SSH 入站。
- **Oracle 网页 technical difficulties**：网页错误不能证明实例不存在，用“刷新实例状态”或 `verify` 查询。
- **费用**：最多 2 OCPU / 12 GB，并检查存储上限；是否收费由自己的账户条款、已有资源和控制台决定。
- **页面打不开**：重新打开应用；端口 `8766` 被占用时可用 `a1 gui --port 8767`。不要把本地服务转发到公网。

## 源码运行与开发

Python 3.10+；打包使用 Python 3.12。三端原生运行，源码使用者才需要安装依赖：

```sh
git clone https://github.com/Jack15678/oracle-a1-workbench.git
cd oracle-a1-workbench
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python launcher.py gui
```

Windows 也可在安装依赖的 Python 环境运行 `start-workbench.cmd`。没有 SDK 的旧源码环境可沿用 OCI CLI；正式包始终带 SDK。静态 UI 无 Node 构建步骤。

```sh
python -m unittest discover -s tests -v
python launcher.py self-test
# 可选前端语法检查；运行 UI 不需要 Node
node --check web/app.js
```

在**目标操作系统和 CPU 架构**上打包，不交叉编译：

```sh
python -m pip install -r requirements-build.txt
python packaging/build.py --version 0.2.0 --platform linux-x64
# 其他值：windows-x64 / macos-x64 / macos-arm64 / linux-arm64
```

Intel macOS 的 cryptography 需静态链接 OpenSSL，避免与 Python 自带库冲突。该平台安装构建依赖时用下面命令替换普通 `pip install`；需本机已有 OpenSSL、Rust 和 Xcode 命令行工具，GitHub runner 已提供：

```sh
OPENSSL_STATIC=1 OPENSSL_DIR="$(brew --prefix openssl@3)" \
  python -m pip install --no-cache-dir --no-binary=cryptography -r requirements-build.txt
```

这是构建者的要求，下载发行包的用户无需安装这些组件。参考 [cryptography macOS 构建说明](https://cryptography.io/en/latest/installation/#building-cryptography-on-macos)。

构建收集依赖许可证，运行实际发行包的离线干净用户目录检查，然后产生压缩包和 SHA-256。`self-test` 不读取凭据、不连接云端；打包检查验证双入口、SDK/密码学模块、CA 证书、静态资源和 UI 到执行器通信。

GitHub Actions 的 **Native release packages** 可手动触发预览构建。推送 `v*` tag 后，五个平台原生构建、测试、打包检查全部通过，才自动创建 Release。测试不依赖凭据、不创建云资源。更改 SDK/打包版本后需重新验证。

原创代码使用 [MIT](LICENSE)。第三方运行时、资源分别遵循其许可证；发行包附依赖版本清单和许可证全文，见 [第三方声明](THIRD_PARTY_NOTICES.md)。这是独立项目，不是 Oracle 官方产品。
