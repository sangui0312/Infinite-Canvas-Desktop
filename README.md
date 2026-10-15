# Infinite Canvas Desktop

基于 Infinite Canvas 开发的 Windows 桌面应用，使用 Electron 承载画布界面，集成 Python 后端、媒体处理工具与 GitHub 在线更新。

本项目维护桌面窗口、安装程序、本地服务生命周期、独立数据存储和版本发布，提供普通画布与智能画布的桌面使用体验。

**[下载安装包](https://github.com/sangui0312/Infinite-Canvas-Desktop/releases/latest)** · **[版本记录](https://github.com/sangui0312/Infinite-Canvas-Desktop/releases)** · **[提交问题](https://github.com/sangui0312/Infinite-Canvas-Desktop/issues)**

## 项目来源

原项目：[hero8152/Infinite-Canvas](https://github.com/hero8152/Infinite-Canvas)。原作者：[GitHub · hero8152](https://github.com/hero8152) / [哔哩哔哩 UID 78652351](https://space.bilibili.com/78652351)。

本仓库是 Infinite Canvas 的二次开发桌面版本，由 [sangui0312](https://github.com/sangui0312) 维护桌面适配与发布。感谢原作者及贡献者提供的画布能力。本项目沿用上游 [LICENSE](LICENSE)。

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

在 **更多设置 → 从旧 Web 导入** 填写旧端地址，检测连接后选择项目并开始下载。旧端必须保持启动，迁移期间请暂停编辑待导入项目。桌面版直接读取画布和素材，绕过旧项目 ZIP 导出的 2000 条资源限制。

下载完成后点击“完成导入”，软件创建新项目并将素材改为本地地址；不覆盖现有项目，不写入旧服务器，也不迁入 API 密钥和登录状态。缺失素材会列明，可重试下载或明确选择导入已有内容。中断后保留任务记录，能够复用经校验且旧端 ETag 未变化的下载文件。旧端需要登录或接口不兼容时会提示，不自动绕过访问限制。

仍可使用项目或画布 ZIP 导入；旧备份中没有打包的素材，无法仅靠导入程序恢复。

## 软件更新

更新源为本仓库的 GitHub Releases。0.3.0 起先读取 `hybrid-update.json`，根据桌面基础版本、功能版本及兼容条件选择更新方式：

- **功能文件更新**：只下载精确匹配起始版本的改动文件包，逐文件校验、备份及应用新增/修改/删除操作。保存画布并停止服务后更新，启动健康检查失败会回滚；意外中断后的下次启动先恢复未确认事务。
- **安装包更新**：桌面外壳、窗口桥接、安装器、Python 或基础依赖变化，或者缺少可用差异基线时，使用 `electron-updater` 安装升级。优先差分下载，由更新框架在差分不可用时回退完整下载。
- **不匹配处理**：本地程序文件被修改或版本不满足文件包条件时，切换到安装包更新，不强行应用文件包。

```text
软件更新 → 检查更新 → 查看公告 → 下载更新
→ 校验 → 保存画布与确认任务 → 应用文件更新或重启安装 → 健康检查
```

- 下载与安装分别确认，普通退出不会触发安装。
- 完整安装包包含桌面壳、画布代码、Python 和工具依赖。
- 分别保存功能版本与桌面基础版本；界面默认显示当前功能版本，文件更新不会虚增外壳版本。
- 更高版本首次启动时备份旧运行副本，服务健康检查失败时尝试恢复该副本。
- 完整桌面版本的回退可使用 [历史 Releases](https://github.com/sangui0312/Infinite-Canvas-Desktop/releases)，操作前备份个人数据。

**混合更新需先安装一次 0.3.0 或更高的完整桌面版。首次引入该机制的版本仍走安装包升级；后续是否提供文件更新包由发布清单决定。**

## 数据存储与备份

默认个人数据目录为安装文件夹内的 `InfiniteCanvasData`，例如 `D:\Infinite Canvas\InfiniteCanvasData`。在 **更多设置 → 数据存储** 中查看、打开或更改存放位置，也可以恢复默认位置。

| 路径 | 用途 |
| --- | --- |
| `userdata\data` | 项目、画布与应用配置 |
| `userdata\assets` | 素材、上传与生成结果 |
| `userdata\output` | 输出文件 |
| `userdata\API` | 本机 API 配置 |
| `userdata\workflows` | 用户工作流 |
| `browser-profile` | 界面设置与桌面会话 |

备份前关闭应用，复制整个 `InfiniteCanvasData`，包含 `userdata` 和 `browser-profile`。外部引用的自定义素材目录需另外备份。API 配置可能包含密钥，请妥善保存。

更改位置后，软件保存画布；有生成任务时需先完成任务，再重启并复制校验文件。目标目录已有其他资料时不覆盖或合并；恢复到本软件此前留下的迁移备份位置时，先将该旧备份另存，再迁入当前资料。空间不足、校验失败时继续使用原资料。迁移前的目录保留为备份，设置页显示各备份位置，需用户自行管理。

旧版 `%LOCALAPPDATA%\InfiniteCanvas` 资料在首次启动新版时提示迁移，也可暂时沿用。缺失的自定义磁盘不会被空资料替代。不可写的安装位置会提示选择其他数据目录。

卸载页面显示当前个人数据路径，**同时删除个人数据默认不勾选**。勾选后仅删除当前安装拥有的专用数据目录；旧迁移备份、其他安装的资料以及外部引用素材均保留。静默卸载与在线升级始终保留个人资料。重新安装到原位置会继续读取保留的路径配置。

运行文件按安装路径独立放在 `%LOCALAPPDATA%\InfiniteCanvasInstallations\<安装标识>\runtime`，路径指针保存在同目录的 `storage.ini`。更换个人资料位置不会使不同安装版本共用运行文件。卸载普通软件时清理其运行文件；保留资料时保留路径指针，便于重装读取。

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
backend/python/python.exe -B -X utf8 -m unittest discover -s tests -p 'test_*.py'
node tools/run_smoke.cjs --packaged
node tools/check_media.cjs
```

自动检查使用隔离测试目录，覆盖窗口启动、画布保存、API 设置、更新状态、下载、视频读取、ZIP、PSD 和导入导出。付费模型调用与实际 Windows 覆盖安装需要按版本另行验证。

## 发布流程

1. 更新 `package.json`、`package-lock.json`、`backend/desktop-app-version.json` 和 `backend/static/desktop-update-notes.json` 的版本与公告。基础兼容能力改变时更新 `minimumShell`。
   公告条目按时间倒序维护：最新条目在最上方，`历史更新：` 之后接最早的条目。
2. 运行依赖变化时，更新运行包及 `runtime-manifest.json` 中的地址、大小与哈希。
3. 执行构建和验证，为安装包等发布文件生成 `SHA256SUMS.txt`。构建脚本生成 `hybrid-update.json` 和 `release-tree.json`；提供上一版本的 `-BaselineManifest <release-tree.json>` 时，仅在基础文件与配置完全一致时生成文件差异 ZIP，否则自动使用安装包模式。保留每次发布的清单作为下次构建基线。
4. 推送源码，为对应提交创建 `v<版本>` 标签及草稿 Release。
5. 每次 Release 都上传完整安装包、blockmap、`latest.yml`、`hybrid-update.json`、`release-tree.json`、可选文件差异 ZIP、校验文件及所需依赖和来源附件，保持完整安装与两种更新路线可用。
6. 核对附件与哈希后公开 Release，再验证匿名下载和在线版本检测。

打包后可用 `backend/python/python.exe -X utf8 tools/check_runtime_drift.py dist/win-unpacked/resources/backend .test-data/drift-check` 确认后端真实启动一次后程序文件仍与发布基线逐字节一致；本地改写会导致功能文件更新被安全拒绝。

构建命令默认不发布，发布工作由维护者执行。API 配置、个人素材、登录信息、缓存与测试数据排除在 Git 和安装包之外。

## 问题反馈

请通过 [Issues](https://github.com/sangui0312/Infinite-Canvas-Desktop/issues) 提交桌面版本、Windows 版本、复现步骤及必要的脱敏日志或截图。

下载异常时先检查 GitHub 连通性；端口冲突时检查 `31300` 的占用情况。退出本地应用后，已提交到远端平台的任务可能仍会继续执行。

## 许可证与致谢

原项目：[hero8152/Infinite-Canvas](https://github.com/hero8152/Infinite-Canvas)。原作者：[GitHub · hero8152](https://github.com/hero8152) / [哔哩哔哩 UID 78652351](https://space.bilibili.com/78652351)。桌面版本由 [sangui0312](https://github.com/sangui0312) 维护，遵循上游 [LICENSE](LICENSE) 中的使用、二次开发开源及署名要求。商业用途请联系相应权利人取得授权。

第三方组件遵循各自许可证，来源与说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 及随包许可文件。
