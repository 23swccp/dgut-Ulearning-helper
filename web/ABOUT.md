![莞工小皮卡](/docs/dgut-bot-hero.png)

# dgut-bot

莞工小皮卡

[![Python 3.10+](/docs/badges/python.svg)](https://www.python.org/) [![Windows](/docs/badges/windows.svg)](https://www.microsoft.com/windows/) [![v0.2.1](/docs/badges/version.svg)]({{REPO_URL}}/releases) [![GitHub dgut-bot](/docs/badges/github.svg)]({{REPO_URL}})

**莞工小皮卡**是一款主要由 `Python` 编写的优学院辅助程序，能够自动处理课程签到和课件学习任务。
仅支持 Windows 系统。

启动莞工小皮卡后,程序会自动打开浏览器调试模式,后续登录等等操作务必在程序打开的浏览器进行,**不要新开另一个进程的浏览器**
## 课程签到

签到模块会读取课程、监测当天的课堂活动，并在发现支持的签到类型后自动尝试签到。目前支持数字码签到、一键签到，以及活动数据本身已经包含签到码的二维码签到。

1. 启动并完成登录

- 启动程序并进入课程签到模块。
- 程序会自动读取本机登录缓存。
- 缓存有效时会直接显示课程列表；缓存失效时会自动准备好优学院登录页。
- 如果出现登录页，请在程序打开的独立浏览器中完成登录；检测到登录状态后，程序会自动显示课程列表。

![](/docs/login-flow.svg)
> 登录提示：请始终在程序打开的浏览器中登录。日常使用的其它浏览器与程序的登录状态并不共用。

2. 选择签到课程

- 在输入框中输入课程名称、教师名称或课程 ID 进行搜索。
- 使用上下方向键或滚轮移动选项，按 `Enter` 确认；也可以直接用鼠标点击课程。
- 确认课程后，再按 `Enter` 开始监测当天的签到活动。

3. 查看签到结果

开始监测后，程序默认每 5 秒检查一次。签到成功、重复签到、跳过原因和错误详情都会显示在终端中。

- 输入 `/` 或 `stop`：停止当前监测。
- 停止后再次输入 `/`：返回课程列表。
- 签到记录：保存在设置中指定的日志位置。

> 二维码签到：只有活动数据本身包含签到码时才能处理；程序不会识别教室现场展示的二维码图片。

## 课件学习

刷课模块用于辅助处理课件中的视频、文档、章节切换和提示弹窗。开始前，需要先在程序的浏览器中打开具体课件。

- 使用程序启动的 Chromium 浏览器登录优学院。
- 进入课程并打开需要学习的具体课件页面。
- 确认页面地址中包含 `ua.dgut.edu.cn/learnCourse`。

![打开到这样子的页面](/docs/course-page.png)

- 然后返回程序的刷课模块，按 `Enter` 或输入 `start` 启动。


2. 常用命令

| 命令 | 作用 |
| --- | --- |
| `Enter` / `start` | 启动课件学习辅助 |
| `open` | 打开优学院课件网站 |
| `speed 8` | 把视频倍速调整为 8×，支持 1–16 |
| `stop` / `/` | 停止当前刷课任务 |
| `clear` | 清空当前模块内容 |

3. 查看运行状态

运行期间，状态区域会显示当前课程、课件页面、视频进度、任务计划、重试次数和停滞状态。课件页面可以留在后台，程序会继续处理已连接的页面。

自动答题

自动答题需要在设置 → 刷课中启用。开启总开关后，可以分别启用选择题、判断题和填空题。

无法识别的题型会被跳过，并在运行日志中留下说明。

> 当前规则：选择题默认选择 `C`，判断题默认选择“×”，填空题默认填写“,”。后续计划增加 CLI命令行模式，可以交给agent做题,但是我不知道怎么做

完成全部课程后，可以挂机。程序大约每隔 `90 × [0.8, 1.2]` 秒上下滚动 240 px，以维持页面活动。注意：

- 浏览器窗口至少保持约 800×600
- 缩放保持 100%
- 不要关闭课件标签页
- 尽量不要把窗口缩得特别小

防走神滚动使用固定坐标 (640, 400) 发送滚轮事件。如果窗口小于这个区域，它可能不稳定。
## 设置与数据

浏览器

用于选择程序连接的 Chromium 浏览器。通常保持自动检测即可；只有自动检测失败时才需要填写浏览器路径。



账号登录恢复

学号和密码输入框目前已经锁定，暂时不能编辑。账号密码自动登录功能尚未开放；现阶段登录缓存失效后，会打开登录页并等待用户在程序浏览器中完成登录。

日志设置

可以控制是否保存签到与错误详情，也可以修改签到日志的保存位置。相对路径以程序目录为基准。

本地数据

| 文件或目录 | 用途 |
| --- | --- |
| `config.json` | 保存浏览器、签到日志和刷课设置 |
| `auth.json` | 保存 Token 和用户 ID 缓存 |
| `browser_profile/` | 保存独立浏览器配置和登录状态 |
| `签到记录.md` | 保存签到结果与错误详情 |

程序的本地服务只监听 `127.0.0.1`，并会在下列范围内自动寻找空闲端口：

| 运行方式 | 端口范围 |
| --- | --- |
| 发行版 | `8765–8784` |
| 开发前端 | `1420–1439` |
| 开发后端 | `8765–8784` |

Vite 会自动代理到实际选中的空闲后端端口。

签到日志会自动隐藏常见的 `Token`、`Authorization`、`Password`、`Cookie` 和 `Bearer` 凭据。


---

## 关于项目

莞工小皮卡 v{{VERSION}} 是非官方个人项目，与学校及优学院平台没有官方关联。请遵守学校规定、课程要求和平台规则。

如需反馈问题或提建议，请通过 GitHub Issues 提交。

也可在贴吧直接搜索: 785434816 向我提交bug

[在 GitHub 查看项目]({{REPO_URL}})


## 开发环境与本地运行

项目后端使用 Python，前端使用 React、TypeScript 和 Vite。推荐使用 Python 3.10 或更高版本，以及 Node.js 20 或兼容版本。

### Python 第三方库

下表列出程序运行、测试和打包涉及的第三方 Python 库。`json`、`threading`、`pathlib`、`urllib`、`http.server` 等随 Python 提供的标准库不需要另外安装，因此不列入其中。

| 分类 | Python 库 | 在本项目中的用途 |
| --- | --- | --- |
| 直接运行依赖 | [requests](https://pypi.org/project/requests/) `>=2.31` | 调用优学院 HTTP 接口、检查和下载程序更新 |
| 直接运行依赖 | [websocket-client](https://pypi.org/project/websocket-client/) `>=1.6` | 连接 Chromium DevTools Protocol WebSocket |
| HTTP 运行依赖 | [urllib3](https://pypi.org/project/urllib3/) | 为 `requests` 提供连接池和重试能力；源码直接使用 `Retry` |
| HTTP 间接依赖 | [certifi](https://pypi.org/project/certifi/) | 为 HTTPS 请求提供 CA 根证书集合 |
| HTTP 间接依赖 | [charset-normalizer](https://pypi.org/project/charset-normalizer/) | 帮助 `requests` 判断响应文本编码 |
| HTTP 间接依赖 | [idna](https://pypi.org/project/idna/) | 处理 HTTP 请求中的国际化域名编码 |
| 测试依赖 | [pytest](https://pypi.org/project/pytest/) | 运行 Python 回归测试套件 |
| 打包依赖 | [PyInstaller](https://pypi.org/project/pyinstaller/) `6.22.2` | 构建 Windows onedir 主程序和独立更新器 |

日常运行源码只要求 `requirements.txt` 中的运行依赖。需要执行测试或构建发行版时，还需要安装 `pytest` 和指定版本的 `PyInstaller`。

首次开发时，在 PowerShell 中依次执行以下完整命令：

```powershell
git clone https://github.com/23swccp/dgut-bot.git
Set-Location .\dgut-bot
python -m pip install -r .\requirements.txt pytest "pyinstaller==6.22.2"
Set-Location .\web
npm ci
Set-Location ..
.\启动浏览器版.bat
```

如果已经下载过源码，以后只需要进入项目根目录再启动：

```powershell
Set-Location C:\你的路径\dgut-bot
.\启动浏览器版.bat
```

启动器会运行本地后端、寻找空闲端口并打开 Chromium 浏览器。前端开发服务器默认从 `1420–1439` 中选择端口，后端从 `8765–8784` 中选择端口。

## 项目结构

| 路径 | 用途 |
| --- | --- |
| `browser_launcher.py` | 程序入口、端口选择、浏览器启动和进程生命周期 |
| `app_paths.py` | 源码与发行版运行目录、本地数据路径的统一解析 |
| `web_server.py` | 本地 HTTP 服务与前端静态资源托管 |
| `backend_commands.py` | 前端命令与后端操作之间的适配层 |
| `yxy_backend.py` | 登录缓存、课程读取、签到和应用设置 |
| `yxy_course.py` | 课件状态机、视频控制、文档滚动和章节导航 |
| `yxy_quiz.py` | 测验页面识别与实验性自动作答 |
| `yxy_updater.py` | 更新检查、下载、校验和更新移交 |
| `updater_installer.py` | 独立更新器的安装与失败回滚逻辑 |
| `web/` | React 前端、内置文档和静态图片 |
| `quiz_simulator/` | 不连接优学院的本地测验模拟页面 |
| `scripts/` | Windows 打包、发行组装和冒烟测试脚本 |
| `packaging/` | PyInstaller 构建配置 |
| `assets/` | 应用图标的源文件、PNG 和 ICO 资源 |

## 运行架构

```text
浏览器界面（React）
        │ 本机 HTTP
        ▼
Python 本地服务
        │ Chromium DevTools Protocol
        ▼
程序启动的 Chromium ── 优学院页面
```

程序只监听 `127.0.0.1`。前端负责显示状态和接收用户操作，Python 后端负责登录、签到、刷课与更新；课件页面操作通过 Chromium DevTools Protocol 完成。

刷课控制器不会依赖固定屏幕分辨率或 OCR。视频、文档、测验可以同时出现在同一页面，控制器会先生成任务计划，完成当前页面的必要任务后再尝试进入下一页。

## 核心模块

课程签到由 `yxy_backend.py` 负责。它读取本地 Token 缓存、获取课程与当天活动，并将签到结果和错误详情写入事件流及签到日志。

课件学习由 `yxy_course.py` 负责。它维护页面状态机，控制视频播放、文档滚动、弹窗处理、停滞恢复和章节衔接。导航后必须验证页面确实发生变化，避免连续误点。

自动答题由 `yxy_quiz.py` 负责。它读取网页 DOM，滚动到目标元素后重新计算坐标，再通过 CDP 发送真实鼠标和键盘事件。当前只填写固定占位内容，不获取正确答案。

前端位于 `web/src/`。`App.tsx` 负责页面和状态交互，`AboutGuide.tsx` 将本文件渲染为内置文档，主要样式集中在 `corner.css`。

## 测试与模拟环境

提交代码前运行 Python 回归测试：

```powershell
python -m pytest -q test_backend.py test_course.py test_launcher.py test_quiz.py test_updater.py
```

运行前端测试和生产构建：

```powershell
cd web
npm test -- --run
npm run build
```

没有真实课件时，可以使用本地测验模拟环境：

```powershell
# 无界面完整测试
python quiz_simulator.py

# 显示浏览器并保留 30 秒
python quiz_simulator.py --show --hold 30
```


## 调试与问题定位

先根据问题所在层级选择日志和工具：

| 问题 | 首先检查 |
| --- | --- |
| 程序无法启动或端口异常 | `browser-launcher.log` |
| 后端服务或接口异常 | `browser-service.log` |
| 签到请求失败 | 界面事件与 `签到记录.md` |
| 页面结构识别失败 | 浏览器开发者工具、`quiz_probe.py` |
| 自动答题回归 | `quiz_simulator.py` 与 `test_quiz.py` |
| 更新失败 | `.update/` 中的状态和更新器日志 |

平台页面升级后，CSS 选择器和页面结构可能发生变化。修改识别逻辑时，应保留无法识别就跳过的安全行为，不要使用随机点击作为兜底。

## Windows 构建与发行

在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows_release.ps1
```

前端依赖没有变化时，可以使用 `-SkipNpmInstall` 加快重复构建：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows_release.ps1 -SkipNpmInstall
```

脚本会构建前端和 PyInstaller onedir 程序，然后在 `release/` 中生成：

```text
dgut-bot-vX.Y.Z-windows-x64/
dgut-bot-vX.Y.Z-windows-x64.zip
manifest.json
```

发布前需要同步修改 `version.py` 和 `web/package.json` 中的版本号。Git 标签应使用相同的 `vX.Y.Z`，否则自动发布流程会停止。

## 开发约定

1. 不要提交 `auth.json`、`account.json`、`config.json`、`browser_profile/` 或真实日志。
2. 修改行为时补充或更新对应测试，至少运行 Python 测试、前端测试和前端构建。
3. 页面操作优先使用稳定的 DOM 结构；坐标点击前先滚动并重新读取位置。
4. 未识别的页面、题型或弹窗应记录并跳过，不进行无依据点击。
5. 新增配置时同时处理默认值、保存、读取、前端展示和旧版本兼容。
6. 修改发行内容后运行启动冒烟测试，并确认 ZIP 内包含前端资源与更新器。

完整说明可在 [GitHub README]({{REPO_URL}}#开发与测试) 中继续维护。
