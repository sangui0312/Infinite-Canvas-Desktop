# Infinite Canvas Desktop

基于 Infinite Canvas 开发的 Windows 桌面应用，使用 Electron 承载画布界面，集成 Python 后端、媒体处理工具与 GitHub 在线更新。

本项目维护桌面窗口、安装程序、本地服务生命周期、独立数据存储和版本发布，提供普通画布与智能画布的桌面使用体验。

**[下载安装包](https://github.com/sangui0312/Infinite-Canvas-Desktop/releases/latest)** · **[版本记录](https://github.com/sangui0312/Infinite-Canvas-Desktop/releases)** · **[提交问题](https://github.com/sangui0312/Infinite-Canvas-Desktop/issues)**

## 项目来源

原作者：[哔哩哔哩 UID 78652351](https://space.bilibili.com/78652351)。

桌面版本基于 [liangjian785-debug/Infinite-Canvas](https://github.com/liangjian785-debug/Infinite-Canvas) 的代码进行二次开发，由 [sangui0312](https://github.com/sangui0312) 维护。感谢原作者及贡献者提供的画布能力。本项目沿用上游 [LICENSE](LICENSE)。

## 功能

- 普通画布与智能画布：节点编辑、连线、图片和视频输入输出。
- API 工作流：自定义平台、模型配置、提示词、循环与按组循环。
- 图片处理：裁剪、拼接、压缩下载、图层拆分和 PSD 导出。
- 素材与项目管理：素材库、画布和项目导入导出、图片与视频批量下载。
- 桌面集成：独立窗口、文件保存对话框、退出前保存和本地服务管理。
- 版本更新：通过 GitHub Releases 下载并校验完整安装包，手动确认安装。

在线生图、视频生成等能力需要配置相应服务的 API Key，具体模型能力以所接入平台为准。

## 界面预览

![桌面画布](docs/screenshots/desktop.png)

![API 设置](docs/screenshots/api-settings-0.1.1.png)

## 安装与使用

支持 **Windows 10 / 11 x64**。安装包集成 Electron、Python、FFmpeg 和 PSD 处理依赖。

1. 在 [Releases](https://github.com/sangui0312/Infinite-Canvas-Desktop/releases/latest) 下载 `InfiniteCanvas-Desktop-<版本>-x64.exe`。
2. 退出正在运行的旧版本，运行安装程序。
3. 选择安装目录；选择磁盘根目录时，安装器会补全 `Infinite Canvas` 子文件夹。
4. 从桌面或开始菜单启动应用。
5. 打开 **API 设置 → 新增平台**，配置服务地址、协议、模型和 API Key，保存后在画布中选择平台和模型。

安装包当前未进行 Windows 代码签名。每个版本随附 `SHA256SUMS.txt`，用于核对下载文件。

### 从 Web 版本迁移

在原应用中导出项目或画布，再通过桌面版的导入功能恢复。桌面版使用独立数据目录，API 配置需要在本机重新填写。

## 软件更新

更新源为本仓库的 GitHub Releases，使用 `electron-updater` 检查版本并验证安装包完整性。

```text
软件更新 → 检查更新 → 查看公告 → 下载更新
→ 完整性校验 → 重启并安装 → 保存画布与确认任务 → 安装并启动
```

- 下载与安装分别确认，普通退出不会触发安装。
- 完整安装包包含桌面壳、画布代码、Python 和工具依赖。
- 桌面版本采用语义化版本号，例如 `0.2.0`；应用中的更新判断使用桌面版本。
- 更高版本首次启动时备份旧运行副本，服务健康检查失败时尝试恢复该副本。
- 完整桌面版本的回退可使用 [历史 Releases](https://github.com/sangui0312/Infinite-Canvas-Desktop/releases)，操作前备份个人数据。

**0.1.x 版本需要先手动安装 0.2.0 或更高版本一次，之后可使用应用内更新。**

## 数据存储与备份

默认数据根目录为 `%LOCALAPPDATA%\InfiniteCanvas`：

| 路径 | 用途 |
| --- | --- |
| `runtime\app` | 当前使用的后端和运行依赖 |
| `runtime\shell-backup-*` | 桌面升级时保留的旧运行副本 |
| `userdata\data` | 项目、画布与应用配置 |
| `userdata\assets` | 素材、上传与生成结果 |
| `userdata\output` | 输出文件 |
| `userdata\API` | 本机 API 配置 |
| `userdata\workflows` | 用户工作流 |
| `browser-profile` | 界面设置与桌面会话 |

备份前关闭应用，复制整个 `userdata`；如需保留界面设置，同时备份 `browser-profile`。自定义素材目录需另外备份。API 配置可能包含密钥，请妥善保存。

卸载默认保留个人数据。旧运行副本的备份目前由用户手动管理。

## 技术结构

| 组件 | 实现 |
| --- | --- |
| 桌面窗口 | Electron 44.7.2 |
| 界面 | HTML / CSS / JavaScript |
| 本地服务 | Python 3.10.11 / FastAPI |
| 媒体处理 | FFmpeg / Pillow / pytoshop |
| 安装程序 | electron-builder 26.15.3 / NSIS |
| 在线更新 | electron-updater 6.8.9 / GitHub Releases |

应用启动时准备独立运行副本，再启动仅监听 `127.0.0.1:31300` 的后端服务，通过本机会话验证访问。窗口关闭或安装更新前先保存画布，并在存在运行任务时请求确认。

```text
desktop/                  桌面主进程、窗口桥接和更新控制器
backend/main.py           画布后端
backend/desktop_entry.py  桌面服务入口、会话与恢复逻辑
backend/static/           画布和设置界面
backend/workflows/        默认工作流
backend/tools/            媒体工具和连接器资源
build/installer.nsh       安装目录处理
tools/                    运行包准备、构建和验证脚本
tests/                    桌面壳、后端和更新测试
runtime-manifest.json     运行依赖的下载地址及文件校验清单
```

已停用的六类独立节点移除了专属创建、编辑与执行实现；旧画布通过统一兼容视图展示已保存的结果，支持查看和下载。

## 本地开发

开发环境：Windows x64、Git、Node.js 24.19+、Python 3.11+、PowerShell 7。开发机 Python 用于准备运行依赖，应用后端使用集成的 Python 3.10.11。

```powershell
git clone https://github.com/sangui0312/Infinite-Canvas-Desktop.git
cd Infinite-Canvas-Desktop

python -X utf8 tools/prepare_runtime.py
$env:NODE_OPTIONS = '--use-system-ca'
npm ci
node --use-system-ca node_modules/electron/install.js
npm run dev
```

`prepare_runtime.py` 根据 `runtime-manifest.json` 下载固定版本的运行包，校验大小和 SHA-256 后恢复依赖。也可以指定已下载的归档进行离线准备：

```powershell
python -X utf8 tools/prepare_runtime.py --archive "D:\Downloads\InfiniteCanvas-Runtime-windows-x64-v1.zip"
```

运行依赖作为 Release 附件分发，源码构建使用本仓库中的 `backend` 目录。开发及冒烟测试模式禁用真实更新安装。

## 构建与验证

```powershell
# 准备依赖、执行检查并构建 Windows 安装包
pwsh -File tools/build_windows.ps1 -InstallDependencies

# 已安装 npm 依赖时
pwsh -File tools/build_windows.ps1
```

构建脚本依次验证运行依赖，执行桌面壳和后端测试，生成安装包与更新公告，并检查打包资源、真实桌面窗口以及媒体处理流程。

主要产物位于 `dist`：

| 文件 | 用途 |
| --- | --- |
| `InfiniteCanvas-Desktop-<版本>-x64.exe` | 完整安装包 |
| 同名 `.exe.blockmap` | 更新辅助文件 |
| `latest.yml` | 更新版本、安装包校验值与公告 |
| `win-unpacked/` | 本地验证用的完整应用目录 |

可按需单独运行现有检查：

```powershell
npm test
backend/python/python.exe -B -X utf8 -m unittest discover -s tests -p test_desktop_backend.py
node tools/run_smoke.cjs --packaged
node tools/check_media.cjs
```

自动检查使用隔离测试目录，覆盖窗口启动、画布保存、API 设置、更新状态、下载、视频读取、ZIP、PSD 和导入导出。付费模型调用与实际 Windows 覆盖安装需要按版本另行验证。

## 发布流程

1. 更新 `package.json`、`package-lock.json` 和 `backend/static/desktop-update-notes.json` 的桌面版本与公告。
2. 运行依赖变化时，更新运行包及 `runtime-manifest.json` 中的地址、大小与哈希。
3. 执行构建和验证，为安装包等发布文件生成 `SHA256SUMS.txt`。
4. 推送源码，为对应提交创建 `v<版本>` 标签及草稿 Release。
5. 上传完整安装包、blockmap、`latest.yml`、校验文件及所需依赖和来源附件。
6. 核对附件与哈希后公开 Release，再验证匿名下载和在线版本检测。

构建命令默认不发布，发布工作由维护者执行。API 配置、个人素材、登录信息、缓存与测试数据排除在 Git 和安装包之外。

## 问题反馈

请通过 [Issues](https://github.com/sangui0312/Infinite-Canvas-Desktop/issues) 提交桌面版本、Windows 版本、复现步骤及必要的脱敏日志或截图。

下载异常时先检查 GitHub 连通性；端口冲突时检查 `31300` 的占用情况。退出本地应用后，已提交到远端平台的任务可能仍会继续执行。

## 许可证与致谢

原作者：[哔哩哔哩 UID 78652351](https://space.bilibili.com/78652351)。桌面版本由 [sangui0312](https://github.com/sangui0312) 维护，遵循上游 [LICENSE](LICENSE) 中的使用、二次开发开源及署名要求。商业用途请联系相应权利人取得授权。

第三方组件遵循各自许可证，来源与说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 及随包许可文件。
