# Windows 远程运维接入

## 构建离线发行版

从 [Win32-OpenSSH 官方发行页](https://github.com/PowerShell/Win32-OpenSSH/releases) 选择适用于目标系统的 x64 MSI，核对发布状态和许可证，再设置 `WBSM_OPENSSH_MSI` 为本地完整路径，`WBSM_OPENSSH_SHA256` 为该发行页列出的 SHA-256，然后运行 `build_exe.bat`。随发行包提供所选 OpenSSH 版本的许可证和第三方声明。普通构建也包含向导，可以接入已经安装好 OpenSSH 的电脑。

## 在目标机启用

打开主菜单「远程运维」，点击启用并确认 Windows 管理员提示。首次启用自动创建类似 `wbsm-k7m2` 的专用普通账号，并显示 16 位随机密码。已配置的账号继续沿用。点击「修改账号」或「修改密码」可手动输入或随机生成。改名后用新账号名连接，原 Windows 账号的 SID 和权限保留。如需随程序启动服务，可勾选「随 WBSM 启动」。既有 OpenSSH 配置经过检查后复用，更新 SSH 账号设置可能需要重启 sshd。新安装默认开放本机子网的 SSH 入站连接。

向导自动显示主机名、检测到的 IPv4 地址、SSH 端口和主机密钥指纹。「复制 AI 接入信息」只复制连接信息。首次连接时核对指纹，再在本地终端输入密码。无人值守执行可另行给部署账号配置 SSH 公钥。新的接入保留普通账号默认权限，额外目录写入或服务启停由管理员授权。既有授权保留。禁用接入会阻止专用账号的新登录，已有连接需另外关闭。

「OpenSSH」一行显示 `sshd` 的服务状态。刷新无需提权，启动、停止和重启需要管理员确认。停止或重启可能中断使用该 OpenSSH 服务的连接。勾选「随 WBSM 启动」后，打开 WBSM 时会在接入账号已启用、服务已停止的情况下请求管理员授权启动服务。服务配置为 Windows 手动启动。关闭窗口后服务继续运行。

要让 Codex 或 Claude Code 免密连接，在开发机准备 SSH 密钥，然后在目标机点击「导入公钥」，选择该开发机的 `.pub` 文件并确认管理员授权。私钥留在开发机。导入时保留已登记的公钥。「复制 AI 接入信息」会附上 SSH 和 SFTP 命令以及指纹验证提示。

「一键移除远程运维」会先确认，再移除专用账号和公钥、卸载已识别的 OpenSSH Server。业务文件保留。SSH/SFTP 会断开，请在目标机本地操作。无法识别的 OpenSSH 安装会提示错误。

## 从开发机验收

```powershell
ssh ACCOUNT@HOST "powershell.exe -NoProfile -Command Get-Process"
ssh ACCOUNT@HOST "powershell.exe -NoProfile -Command Get-Service -Name MyApp"
sftp ACCOUNT@HOST
```

通过 SFTP 在已授权的测试目录上传和下载同一个文件，核对内容。使用专门的测试服务验证启停，并确认未授权目录的写入及未授权服务的启停被拒绝。已有计划任务的配置、启用状态及触发行为也列入验收。

## 命令、部署和调试

假设已将 SSH 别名保存为 `win-prod`，在开发机仓库目录执行以下命令。脚本以实际 SSH 部署账号运行。

```powershell
python remote_cli.py win-prod --probe --password-prompt
python remote_cli.py win-prod --directory 'D:\Apps\MyApp' --script .\verify.ps1 --password-prompt
python remote_cli.py win-prod --interactive
sftp -o BatchMode=yes -o StrictHostKeyChecking=yes -b .\transfer.txt win-prod
```

SFTP 批处理示例需要公钥认证。初次使用密码时，可在本地终端运行 `sftp win-prod`。

- **脚本和文件。** 用 UTF-8 保存本地 PowerShell 脚本。较大的脚本先通过 SFTP 上传，再用短命令在指定工作目录运行。批处理传输文件中逐行写 SFTP 的 `put` 或 `get` 操作，含空格的路径加双引号。上传发布包后比较本地和远端的 SHA-256，再替换文件。
- **服务。** 用 `Get-Service -Name MyApp` 查询状态，部署脚本按应用需要备份、停止、替换、启动和验证。服务进入 Running 后，再执行应用健康检查。断线或命令超时后先查版本和服务状态，再决定是否重试。需要断线后也能自动回滚的发布应在目标机本地执行，由应用部署流程管理。
- **Docker。** `--probe` 分别报告 Docker CLI 路径、引擎是否可达、Compose 是否可用。检查输出确认 SSH 账号连到正确的引擎，再执行对应项目的 Compose 操作。向导不会自动授予 Docker 引擎访问权限。
- **依赖。** `--probe` 在 SSH 登录环境中查找 Python、PowerShell 7、WinGet 和 Docker。目标机离线时在开发机获取并校验安装包，再用 SFTP 上传。系统级安装在目标机按已有管理员授权流程处理。
- **交互。** 默认脚本运行禁止终端提示，保留远端输出和退出码。确实需要文本输入时，从有终端的开发端运行 `--interactive`。GUI 弹窗需要对应的 Windows 桌面会话。

SSH 会话可以执行普通部署账号有权执行的命令，SFTP 也继承该账号按 Windows ACL 已有的权限。为服务授权前核对其运行身份，写入高权限服务会加载的程序或脚本等同于授予对该运行环境的控制。
