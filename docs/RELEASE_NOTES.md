下载对应系统和 CPU 架构的发行包，解压后打开 **A1Workbench**。无需另外安装 Python、WSL 或 OCI CLI。首次打开后通过页面向导连接自己的 Oracle 账户。

| 文件名后缀 | 系统 |
| --- | --- |
| `windows-x64.zip` | Windows 10 / 11，Intel / AMD 64 位 |
| `macos-arm64.zip` | macOS 15+，Apple Silicon（M 系列） |
| `macos-x64.zip` | macOS 15+，Intel |
| `linux-x64.tar.gz` | Ubuntu 22.04+，x86-64 |
| `linux-arm64.tar.gz` | Ubuntu 22.04+，ARM64 |

Windows 解压整个文件夹后打开 `A1Workbench/A1Workbench.exe`；macOS 将 `A1Workbench.app` 拖到“应用程序”；Linux 运行 `A1Workbench/A1Workbench`。每个包同时包含 `a1` 命令行执行器和第三方许可。

Linux 以 Ubuntu 22.04 / glibc 2.35 为构建验证基线；Debian 12 满足 glibc 基线，但未在 Debian CI 实测。Alpine/musl 不支持。

这些发行包目前**没有 Windows 代码签名或 Apple 公证**。系统可能提示未知开发者。请确认下载来自本仓库 Releases，并核对 `SHA256SUMS.txt`；只为已确认来源的这个应用选择系统允许的“仍要运行”/“仍要打开”。如果组织策略禁止，则使用源码入口。无需关闭全局安全保护。macOS 的“仍要打开”位于“系统设置 → 隐私与安全性”；没有出现对应提示时不要运行网上的全局关闭 Gatekeeper 命令。

CI 在每个对应架构上运行离线测试和实际打包程序的干净用户目录检查。测试不会连接真实 Oracle 账户或创建资源；仍需用户在首次向导中完成自己账户的授权验证。

创建响应只表示请求已接受，只有只读核验确认 `RUNNING` 才显示“已运行”。macOS 应用使用本机浏览器显示界面。Linux 云端 bot 直接运行 `a1 inspect/egress/auth/run/verify --config ...`，不需要桌面或公网管理端口。完整说明见随包 README 或仓库 README。
