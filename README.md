# 莞工小皮卡（dgut-bot）

[![Release](https://img.shields.io/github/v/release/23swccp/dgut-bot?display_name=tag&sort=semver)](https://github.com/23swccp/dgut-bot/releases/latest)
![Platform](https://img.shields.io/badge/platform-Windows-0078D4)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)
[![License](https://img.shields.io/badge/license-MIT-2f2f2f)](LICENSE)

**莞工小皮卡**是一款面向东莞理工学院优学院的本地浏览器助手，提供课程签到监测、课件学习辅助、实验性测验自动作答和应用内更新。

程序由 **Python 本地服务 + React 浏览器界面**组成，只监听本机回环地址。登录缓存、配置、浏览器资料和日志均保存在本机。

> [!IMPORTANT]
> 本项目是非官方个人工具，与学校及优学院平台无官方关联。请遵守学校规定、课程要求和平台规则。自动答题只填写固定占位内容，不获取正确答案，可能产生错误作答；请了解风险后再启用。

## 功能概览

| 模块 | 能力 |
| --- | --- |
| 登录与课程 | 自动读取本机 Token 缓存；缓存失效时打开登录页；按名称、教师或课程 ID 选择课程 |
| 签到监测 | 轮询当日课堂活动；处理一键签到、数字码签到及活动数据中已有签到码的二维码签到 |
| 课件学习 | 视频倍速与播放恢复、文档阅读、章节衔接、走神提示恢复、停滞检测与有限重试 |
| 自动答题 | 实验性支持选择题、判断题、填空题；总开关和三个题型可分别配置 |
| 可观测性 | 展示连接状态、课程和页面、任务计划、视频进度、重试、停滞及关键事件 |
| 自动更新 | 后台检查和下载、SHA-256 校验、安装确认、失败回滚与本地数据保护 |

## 快速开始

### 使用发布版（推荐）

1. 从 [Releases](https://github.com/23swccp/dgut-bot/releases/latest) 下载 `dgut-bot-vX.Y.Z-windows-x64.zip`。
2. 右键 ZIP 选择“全部解压缩”，**完整解压到本地文件夹**。不要在压缩包预览窗口里直接双击 `dgut-bot.exe`。
3. 双击 `dgut-bot.exe` 启动。

不需要安装 Python，不需要安装 Node.js，也不需要执行 `pip install`。程序会优先使用设置中保存的浏览器，然后依次检测 Edge、Chrome 和其他 Chromium 浏览器。

解压后的目录结构：

```text
dgut-bot-vX.Y.Z-windows-x64/
├─ dgut-bot.exe      双击运行
├─ _internal/        程序运行组件（PyInstaller onedir）
├─ web/dist/         前端构建产物
└─ README.md
```

运行过程中会按需在程序目录生成 `config.json`、`auth.json`、`browser_profile/`、`签到记录.md` 和 `.update/` 等本地数据；配置、登录缓存和日志都写在发行目录，不会写进系统临时目录。账号密码恢复界面目前尚未开放编辑。

### 从源码运行

源码开发模式还需要 Node.js 20 或兼容版本：

```powershell
git clone https://github.com/23swccp/dgut-bot.git
cd dgut-bot
python -m pip install -r requirements.txt
cd web
npm ci
cd ..
./启动浏览器版.bat
```

首次启动时如果 `web/node_modules` 不存在，启动器也会尝试自动执行 `npm ci`。

## 使用方法

### 课程签到

1. 启动后进入“课程签到”。程序会自动读取本机登录缓存。
2. 缓存有效时直接显示课程选择器；缓存无效时自动打开优学院登录页。
3. 在浏览器完成登录后稍候片刻，小皮卡会自动检测登录状态并读取课程。
4. 搜索并选择一门课程，按 Enter 开始监测。
5. 输入 `/` 或 `stop` 停止监测；输入 `/` 可返回课程选择。

签到模块默认每 5 秒检查一次。已处理的活动在本次监测中不会重复提交。

### 课件学习辅助

1. 使用小皮卡启动的同一个调试浏览器打开具体课件学习页，URL 需包含 `ua.dgut.edu.cn/learnCourse`。
2. 返回小皮卡并切换到“刷课”模块。
3. 按 Enter 或输入 `start` 启动。

| 命令 | 作用 |
| --- | --- |
| `start` / Enter | 启动课件学习辅助 |
| `open` | 打开优学院课件网站 |
| `speed 8` | 设置视频倍速，支持 1–16 |
| `stop` / `/` | 停止运行 |
| `clear` | 清空本地显示日志 |

控制器启动后会先尝试返回课程第一张可见页面，再依据页面中的视频、文档和测验组合生成任务计划。课件页可以留在后台，程序不会主动切换到该标签页。

### 自动答题（实验性）

当前占位策略：

- 选择题：选择 `C`。
- 判断题：选择“错误”。
- 填空题：每个空填写英文逗号 `,`。

在“设置 → 刷课”中可以关闭自动答题，或分别控制选择题、判断题和填空题。三个题型全部关闭时，总开关会同步关闭。未知题型会跳过并记录，不会盲目点击。

## 无课件时测试自动答题

仓库内置依据真实页面调研结果制作的本地模拟环境。它会启动隔离的 Chromium，并使用产品中的 `QuizHandler` 和真实 CDP 鼠标、键盘事件测试总开关与各题型组合。

无界面运行完整测试：

```powershell
python quiz_simulator.py
```

显示浏览器并观察过程：

```powershell
python quiz_simulator.py --show --hold 30
```

模拟器只监听本机，不连接优学院，也不会提交任何真实课程数据。详见 [模拟环境说明](quiz_simulator/README.md)。

## 设置、日志与本地数据

| 文件或目录 | 用途 | 是否提交到 Git |
| --- | --- | --- |
| `config.json` | 浏览器、签到日志和课件学习设置 | 否 |
| `auth.json` | Token 与用户 ID 缓存 | 否 |
| `account.json` | 预留的本机账号恢复信息；当前界面尚未开放编辑 | 否 |
| `browser_profile/` | 独立 Chromium 配置与登录状态 | 否 |
| `签到记录.md` | 签到结果与错误详情 | 否 |
| `browser-launcher.log` | 启动诊断日志 | 否 |
| `browser-service.log` | 后台服务日志 | 否 |
| `.update/` | 更新下载、状态和更新器日志 | 否 |

服务只监听 `127.0.0.1`，不会向局域网或公网开放接口。前端不会读取账号密码明文；签到日志会隐藏常见的 Authorization、Token、Password、Cookie 和 Bearer 凭据。

签到日志默认保存在项目目录的 `签到记录.md`，可在设置中关闭或修改路径。相对路径始终以程序目录为基准。记录格式示例：

```text
2026-08-30-10:16 | 课程名称 | 一键签到 |
  - attendanceID: 123456
  - HTTP/status: exception
  - response: 错误内容
```

## 应用内更新

程序启动后会检查 GitHub Release。发现新版本时在后台下载完整 ZIP（`dgut-bot-vX.Y.Z-windows-x64.zip`），支持断点续传和最多三次自动重试；下载完成后校验 SHA-256，并等待用户确认安装。

安装期间会关闭小皮卡自己的标签页、停止签到与刷课服务，然后由独立更新器（发行包内 `_internal/updater/updater.exe`）在独立进程中替换程序文件，全程不依赖系统 Python。失败时自动回滚，旧版本仍可启动。以下本地数据在更新中不会被覆盖：

```text
config.json
auth.json
account.json
browser_profile/
签到记录.md
运行日志与更新状态
```

## 常见问题

### 找不到浏览器

打开设置重新检测浏览器，或选择“自定义路径”并填写 `msedge.exe`、`chrome.exe` 等 Chromium 浏览器程序的完整路径。

### 登录后仍然读不到课程

确认登录操作发生在小皮卡启动的独立调试浏览器中，而不是日常使用的另一个浏览器窗口。完成登录后稍候片刻，课程签到模块会自动检测登录状态。

### 刷课提示“未找到课件学习页”

确认具体学习页已经在同一个调试浏览器中打开，并且地址包含 `ua.dgut.edu.cn/learnCourse`。课程首页或普通门户页不能作为控制目标。

### 关闭小皮卡页面后后台没有立即退出

正常关闭会通过 `pagehide` 通知后台。浏览器丢弃关闭通知时，心跳兜底会在约两分钟后回收本地服务。

### 签到或测验操作失败

先查看界面关键事件和 `签到记录.md`。需要诊断页面结构时，可参考 [测验页面结构调研](测验页面结构调研.md) 和 `quiz_probe.py`；平台页面升级后，既有选择器可能需要同步调整。

## 技术架构

```text
dgut-bot.exe（PyInstaller onedir，windowed）
└─ browser_launcher.py 入口
   ├─ web_server.py / backend_commands.py
   │  ├─ yxy_backend.py       登录、课程与签到
   │  ├─ yxy_course.py        课件状态机与 CDP 控制
   │  ├─ yxy_quiz.py          测验识别与作答
   │  └─ yxy_updater.py       更新检查与移交
   ├─ React + Vite 前端（web/dist，由本地服务托管）
   ├─ _internal/updater/updater.exe  独立更新器
   └─ 独立 Chromium browser_profile/
```

关键设计原则：

- 页面操作使用 Chromium DevTools Protocol 产生真实输入事件，不直接调用课程或作答接口。
- 页面导航必须验证 page ID、页码或可信内容确实变化，避免误翻页。
- 视频、文档和测验可以在同一页面组合执行；测验优先，其余任务完成后才允许翻页。
- 关键事件使用带序号的游标队列传递，刷新或短暂重连不会重复消费事件。

## 开发与测试

安装开发依赖：

```powershell
python -m pip install -r requirements.txt
cd web
npm ci
```

运行回归测试：

```powershell
# Python
python -m pytest -q test_backend.py test_course.py test_launcher.py test_quiz.py test_updater.py

# 前端
cd web
npm test -- --run
npm run build
```

发布新版本：

1. 同步修改 `version.py` 和 `web/package.json` 中的版本号。
2. 提交并推送 `v*` tag；tag 必须与 `version.py` 中的 `APP_VERSION` 一致，否则发布停止。
3. GitHub Actions 在 windows-latest 上构建前端、运行 Python 与前端测试、用 PyInstaller（固定版本）打包 onedir 发行版、对打包结果执行启动冒烟测试，最后把 `dgut-bot-vX.Y.Z-windows-x64.zip` 与 `manifest.json` 上传到 Release。

本地构建发行版：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows_release.ps1
```

脚本会清理本项目 `build/`、`dist/` 临时目录，构建前端与 PyInstaller 包，使用 `assets/dgut-bot.ico` 写入程序图标，组装发行目录并生成 ZIP、`manifest.json` 和 SHA-256。

## 项目文档

- [完整功能列表](功能列表.md)
- [开发与维护交接文档](交接文档.md)
- [测验页面结构调研](测验页面结构调研.md)
- [本地测验模拟环境](quiz_simulator/README.md)

## 贡献

欢迎通过 Issue 报告可复现的问题，或通过 Pull Request 提交修复。提交前请：

1. 不要包含 `config.json`、`auth.json`、`account.json`、`browser_profile/` 或任何真实日志。
2. 为行为变更补充测试。
3. 运行 Python 与前端测试，并确认 `npm run build` 通过。

## 许可证与免责声明

本项目采用 [MIT License](LICENSE) 开源。你可以使用、复制、修改、合并、发布和分发本软件，但必须在副本或重要部分中保留原版权声明和许可证声明。

本项目按现状提供，不保证与平台未来版本持续兼容，也不保证签到、学习记录或测验结果一定成功。因使用本项目造成的课程记录、成绩、账号或其他后果由使用者自行承担。
