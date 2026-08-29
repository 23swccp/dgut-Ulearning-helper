# 刷课控制器重构项目说明与 AI 实施提示词

> 用途：将本文档交给其他 AI 编程助手，使其能够在不了解本项目历史的情况下，安全、渐进地重构课件学习辅助模块。
>
> 本文档描述的是工程改造要求，不是平台接口分析文档。不得借此实现自动答题、伪造学习进度、伪造用户活跃、绕过验证码或规避平台检测。

## 一、项目概况

项目名称：东莞理工学院优学院助手

工作目录：

```text
C:\Users\34293\Desktop\dgut.yxy-checkin_assistant
```

当前技术栈：

- Python 3.10+
- React 18 + TypeScript + Vite
- Chromium DevTools Protocol（CDP）
- `requests`
- `websocket-client`

当前应用由以下部分组成：

```text
启动浏览器版.bat       Windows 启动入口
browser_launcher.py    浏览器检测、服务生命周期
web_server.py          本地 HTTP API，仅监听 127.0.0.1
backend_commands.py    前端命令白名单与后端映射
yxy_backend.py         登录、配置、课程与签到监测
yxy_course.py          课件页 CDP 学习辅助，本次主要改造对象
web/                   React/Vite 前端
test_course.py         课件辅助离线测试
test_backend.py        后端离线测试
test_launcher.py       启动器离线测试
```

## 二、现有用户流程

用户当前操作流程必须保持不变：

1. 启动浏览器版。
2. 在“刷课”页面输入 `open` 打开优学院。
3. 用户自行登录并打开课件学习页。
4. 按 Enter 或输入 `start` 启动课件学习辅助。
5. 可输入 `speed 8` 修改视频倍速。
6. 可输入 `stop` 停止。

前端现有命令和 API 不得改变：

```text
start_course_helper
stop_course_helper
set_course_speed
```

本轮重构不得要求用户增加任何固定操作，也不得修改 `web/src` 下的页面功能或交互。

## 三、当前能力

`yxy_course.py` 当前已经具备：

- 定位 `ua.dgut.edu.cn/learnCourse` 标签页。
- 通过 WebSocket 连接 CDP。
- 向主页面注入 JavaScript 控制器。
- 读取并控制 HTML5 `<video>`。
- 设置静音和播放倍速。
- 监听视频加载、播放、倍速变化和结束事件。
- 处理 SPA 复用同一个 `<video>` 并更换媒体源的情况。
- 自动滚动同源文档。
- 通过 CDP 执行环境处理跨域文档 iframe 和 OOPIF。
- 识别真实滚动并在到达末尾后进入后续流程。
- 从页面 `console.log` 回传课件日志。
- 停止控制器时不关闭用户浏览器标签页。

这些能力应尽量复用，不要推倒重写整个应用。

## 四、当前主要问题

### 4.1 缺少统一状态机

当前视频、文档、弹窗和翻页由多个事件监听器、`setInterval` 和 Python 线程分别推动，容易出现：

- 视频结束和文档线程同时请求翻页。
- 同一按钮被重复点击。
- 页面已经切换，但旧页面定时器仍在运行。
- 内容结束后立刻跳转，页面尚未完成自身状态更新。
- 操作失败后没有统一的重试和错误状态。

### 4.2 注入脚本包含伪造鼠标活动

当前 `INJECT_JS` 中存在 `fakeMouseActivity()`，会高频构造随机 `mousemove` 事件。该逻辑必须删除，同时删除只为它服务的配置字段和调用：

```text
mouse_interval_min
mouse_interval_max
fakeMouseActivity()
```

不得用 CDP、JavaScript、Windows API 或其他方式重新实现定时移动鼠标、随机点击、随机按键等虚假活跃行为。

### 4.3 弹窗点击范围过宽

当前逻辑会扫描大量按钮，并根据“继续、确定、知道了、关闭”等宽泛文本直接 `.click()`，存在误点普通页面按钮的风险。

应改为：

- 只处理明确识别出的课件完成或普通播放器提示。
- 必须同时满足容器语义和按钮语义。
- 每次动作后验证弹窗消失或章节变化。
- 不自动处理“长时间无操作”“需要本人确认”等在场性检查。

### 4.4 内容完成与平台页面完成混为一谈

`video.ended` 或文档滚动到底只能说明本地内容结束，不一定说明页面已经完成自己的保存和 UI 更新。

正确流程应为：

```text
内容结束
→ 等待页面稳定
→ 等待完成提示或下一节按钮可用
→ 执行一次导航动作
→ 验证章节确实变化
```

### 4.5 动作执行与结果验证没有统一封装

应将“查找目标、执行动作、等待、验证结果”封装成统一动作层，而不是在各处直接调用 `.click()`。

## 五、目标架构

### 5.1 总体原则

采用“状态读取优先、视觉识别兜底”的混合架构：

```text
页面/媒体真实状态
        ↓
    状态机决策
        ↓
执行一个受控动作
        ↓
验证页面状态变化
        ↓
成功进入下一状态；失败有限重试
```

不要将整个网页改成纯截图自动化。视频状态和滚动状态应继续直接读取。

### 5.2 建议状态

可根据实现语言调整名称，但语义应清楚：

```text
IDLE                    未启动
ATTACHING               正在连接课件标签页
LOADING                 页面或媒体加载中
VIDEO_READY             视频已就绪
VIDEO_PLAYING           视频正常播放
DOCUMENT_READING        文档正在滚动
CONTENT_FINISHED        本地内容已结束
WAITING_PAGE_CONFIRM    等待页面保存并显示可继续状态
READY_FOR_NEXT          已确认允许进入下一节
NAVIGATING              已执行一次导航，等待章节变化
PAUSED                  被用户停止或暂停
ERROR                   本轮无法自动恢复
STOPPED                 控制器已停止
```

### 5.3 关键状态转换

```text
LOADING → VIDEO_READY
VIDEO_READY → VIDEO_PLAYING
VIDEO_PLAYING → CONTENT_FINISHED          video.ended

LOADING → DOCUMENT_READING
DOCUMENT_READING → CONTENT_FINISHED       真实滚动过且到达底部

CONTENT_FINISHED → WAITING_PAGE_CONFIRM
WAITING_PAGE_CONFIRM → READY_FOR_NEXT      完成提示出现或下一节按钮明确可用
READY_FOR_NEXT → NAVIGATING                只执行一次导航动作
NAVIGATING → LOADING                       URL、章节标识或媒体源发生变化

任何状态 → STOPPED                        用户执行 stop
可恢复异常 → 当前状态                     有限次数重试
不可恢复异常 → ERROR
```

### 5.4 页面观察器

注入脚本负责读取页面，不负责无限制地自行点击。建议观察：

- 当前 URL。
- 当前章节标识、标题或稳定的 DOM 标识。
- `<video>` 是否存在。
- `currentSrc`、`readyState`、`paused`、`ended`、`currentTime`、`duration`。
- 视频进度是否连续多轮不增长。
- 文档滚动容器和剩余距离。
- 下一节按钮是否存在、可见、未禁用。
- 明确的章节完成弹窗是否存在。
- DOM 变化和 SPA 路由变化。

使用 `MutationObserver` 作为变化通知，同时保留低频 watchdog 作为事件丢失兜底。不要仅依赖高频轮询。

### 5.5 结构化事件

页面注入脚本应向 Python 回传结构化事件，而不是只输出自然语言日志。可以继续通过 `Runtime.consoleAPICalled` 传递，例如：

```text
[yxy:event] {"type":"video-ended","chapter":"...","source":"..."}
[yxy:event] {"type":"document-bottom","frame":"..."}
[yxy:event] {"type":"next-ready","selector":"..."}
[yxy:event] {"type":"chapter-changed","from":"...","to":"..."}
[yxy:event] {"type":"video-stalled","currentTime":120.5}
```

日志与事件应分开解析：

- `[yxy]`：面向用户的日志。
- `[yxy:event]`：机器可读事件。

### 5.6 动作执行器

建立统一动作入口，例如 `ActionExecutor` 或同等职责的方法集合。

动作优先级：

1. DOM 语义定位并确认元素可见、未禁用。
2. 获取元素 `getBoundingClientRect()`，通过 CDP 在中心坐标发送鼠标移动、按下和松开。
3. 可选模板匹配，仅用于少数视觉固定且 DOM 定位不稳定的按钮。
4. 全部失败时记录诊断信息并停止自动动作，等待用户处理。

每个动作必须包含：

```text
前置状态
目标描述
单次执行锁
超时
最大重试次数
成功后置条件
失败日志
```

不得在没有后置条件验证的情况下连续点击。

## 六、CDP 输入实现要求

需要实现或封装以下浏览器级能力：

- `Input.dispatchMouseEvent`
  - `mouseMoved`
  - `mousePressed`
  - `mouseReleased`
  - 必要时 `mouseWheel`
- `Input.dispatchKeyEvent`
- `Input.insertText`

本项目首轮只需要可靠的鼠标点击；滚轮和键盘接口可以封装并测试，但不要为了伪造用户活跃而调用。

坐标必须基于当前标签页视口，而不是 Windows 桌面坐标。点击前应确认目标矩形：

- 位于可视视口内。
- 宽高大于零。
- 未被禁用。
- 中心点处元素与目标一致，或至少属于目标元素。

## 七、模板匹配设计

### 7.1 是否首轮启用

模板匹配属于第二阶段能力。优先完成状态机和 CDP 元素坐标点击，再决定是否引入 OpenCV。

不引入 OCR。当前 UI 固定，OCR 的依赖、模型和误识别成本暂时没有必要。

### 7.2 使用范围

模板匹配仅用于：

- 固定样式的播放或继续图标。
- DOM 无法稳定定位的课件完成按钮。
- 跨域 iframe 中无法可靠取得元素矩形、但画面稳定的控件。

不得用于：

- 判断视频是否结束。
- 判断文档是否滚动到底。
- 随机寻找可点击区域。
- 模拟持续在线。
- 自动处理验证码或在场性确认。

### 7.3 处理流程

```text
Page.captureScreenshot
→ 解码 PNG
→ 取得截图尺寸和页面视口尺寸
→ 在限定 ROI 内执行模板匹配
→ 达到置信度阈值后返回矩形
→ 将截图坐标换算为视口 CSS 坐标
→ CDP 点击中心点
→ 验证后置状态
```

必须查询实际截图宽高与布局视口，不能假设比例固定。坐标换算可按实际尺寸比例完成：

```text
css_x = image_x × viewport_width  / screenshot_width
css_y = image_y × viewport_height / screenshot_height
```

模板必须来自用户自己的无敏感信息截图。不要凭空生成模板，不要提交包含姓名、学号、课程名称或 Token 的截图。

如果仓库暂时没有模板资源，应先实现可测试的接口和假图片单元测试，不得阻塞状态机重构。

## 八、视频 watchdog

成熟的播放器控制不应只监听 `ended`，还应检测真实停滞。

建议记录：

```text
currentSrc
currentTime
duration
readyState
paused
ended
最近一次进度增长时间
恢复尝试次数
```

仅在以下条件同时满足时判定疑似停滞：

- 当前状态为 `VIDEO_PLAYING`。
- `ended == false`。
- 页面不是隐藏切换或正在换源。
- `readyState` 已达到可播放水平。
- `currentTime` 在多个检查周期内没有增长。

恢复动作应有限且可解释，例如重新调用 `play()`；达到上限后进入 `ERROR` 或等待用户处理。不要通过随机鼠标或随机按键恢复。

## 九、文档滚动要求

保留现有跨域 frame/OOPIF 支持，并继续遵循：

- 必须至少发生过一次真实滚动，才允许判定完成。
- `scrollTop + clientHeight >= scrollHeight - epsilon` 才是到底。
- 不能把左侧章节目录误判为正文。
- 同一 frame 完成事件只发送一次。
- 文档线程和视频状态机不能同时请求翻页。
- 页面或 frame 更换后清理旧 frame 状态集合。

## 十、生命周期与清理

所有定时器、观察器、线程和事件监听必须有明确生命周期。

`stop()` 后必须确保：

- 注入脚本的 `running` 为 false。
- 所有注入脚本的 interval/timeout 被清理。
- `MutationObserver.disconnect()`。
- Python 文档滚动线程停止。
- CDP WebSocket 关闭。
- 不再发生点击、滚动、播放恢复或日志刷屏。
- 再次 `start()` 不会继承上一次运行的状态。

建议注入脚本保存统一的清理函数和定时器集合，而不是创建不可追踪的 `setInterval`。

## 十一、兼容性约束

必须保持：

- 不修改现有 React 页面功能和用户操作流程。
- 不改变 `backend_commands.py` 的现有命令名称。
- 不关闭用户浏览器标签页。
- 不影响课程签到监测模块。
- 不把 Token、Cookie、账号或真实课程数据写入日志和测试。
- 不读取题目答案。
- 不填写或提交测验。
- 不直接伪造或提交课件学习进度。
- 不模拟虚假鼠标活动、随机点击或随机按键。

## 十二、建议实施顺序

### 阶段 A：清理与状态机

1. 为当前代码建立补充测试。
2. 删除 `fakeMouseActivity` 及其配置。
3. 为注入脚本建立统一资源清理机制。
4. 引入明确的课件状态和转换规则。
5. 将自然语言日志与结构化事件分开。
6. 禁止视频和文档路径重复触发导航。

### 阶段 B：可靠动作层

1. 把散落的 `.click()` 收敛到统一入口。
2. 增加元素可见性、禁用状态和中心点检查。
3. 增加 CDP 鼠标点击封装。
4. 为动作增加后置状态验证和有限重试。
5. 收窄弹窗识别规则。

### 阶段 C：恢复与诊断

1. 增加视频 watchdog。
2. 页面切换时重置媒体和 frame 状态。
3. 增加失败诊断数据，但默认不保存含个人信息的整页截图。
4. 确认 stop/start 生命周期完整。

### 阶段 D：可选模板匹配

1. 抽象截图和模板定位接口。
2. 使用测试图片验证坐标映射、ROI 和置信度。
3. 用户提供脱敏模板后再接入真实按钮。
4. 模板匹配只能作为动作定位兜底。

## 十三、测试要求

所有测试必须离线运行，不连接学校接口或真实浏览器。

至少补充以下测试：

- 状态转换合法性。
- 同一内容完成事件只触发一次导航。
- 视频结束后不会立即无条件点击。
- 文档必须真实滚动后才能完成。
- 视频与文档不能同时触发导航。
- 页面章节变化后旧状态被清理。
- stop 后不再执行动作。
- 重复 start 不会创建多套控制器。
- CDP 点击按 `move → press → release` 顺序发送。
- CDP 点击坐标使用视口坐标。
- 动作未满足后置条件时有限重试并最终失败。
- 注入脚本不再包含 `fakeMouseActivity`、随机 `mousemove` 或随机点击。
- 注入脚本不包含答案读取、答题和测验提交逻辑。
- 模板坐标到 CSS 坐标换算正确。
- 低置信度模板结果不会点击。

验证命令：

```powershell
python -m unittest -v test_backend.py test_course.py test_launcher.py
python -m py_compile yxy_course.py yxy_backend.py backend_commands.py web_server.py browser_launcher.py
cd web
npm run build
```

## 十四、完成定义

只有满足以下条件才算完成：

- 原有前端命令和操作流程保持不变。
- 所有旧测试和新增测试通过。
- 前端生产构建通过。
- `stop()` 后没有遗留线程、观察器和定时器动作。
- 视频、文档、完成等待和导航具有明确状态。
- 导航动作具备去重、超时和结果验证。
- 不再存在伪造用户活跃的代码。
- 没有自动答题、答案读取、测验提交或学习进度伪造。
- 代码变更集中、可读，并保留现有工作区中无关的未提交修改。

---

# 可直接复制给其他 AI 的完整提示词

```text
你正在维护 Windows 项目：
C:\Users\34293\Desktop\dgut.yxy-checkin_assistant

请先完整阅读以下文件，再开始修改：
- README.md
- 交接文档.md
- 功能列表.md
- yxy_course.py
- yxy_backend.py
- backend_commands.py
- test_course.py
- test_backend.py
- test_launcher.py
- 刷课控制器重构项目与AI提示词.md

目标：在不改变 React 前端功能、不增加用户操作步骤、不改变现有 start/stop/open/speed 命令的前提下，把 yxy_course.py 重构为“真实页面状态驱动 + 明确状态机 + 统一动作执行 + 操作后验证”的可靠课件学习辅助控制器。

必须遵守的边界：
1. 不读取答案，不自动答题，不填写或提交测验。
2. 不直接伪造或提交学习进度。
3. 删除现有 fakeMouseActivity、mouse_interval_min、mouse_interval_max 以及随机 mousemove 调用。
4. 不以 CDP、JavaScript 或 Windows API 重新实现定时鼠标移动、随机点击、随机按键或其他虚假活跃行为。
5. 不自动处理“长时间无操作”或其他需要本人确认的在场性检查。
6. 不提交 Token、Cookie、账号、真实课程数据或包含个人信息的截图。
7. 不覆盖工作区内与本任务无关的现有修改。

工程要求：
1. 保留现有 CDP 标签页定位、视频控制、跨域 frame/OOPIF 文档滚动和日志回流能力。
2. 为课程控制器建立清晰状态：加载、视频就绪、视频播放、文档阅读、内容结束、等待页面确认、允许下一节、导航中、错误、停止。
3. video.ended 和文档到底只表示本地内容结束；必须等待页面显示完成提示或下一节按钮明确可用后，才能执行一次导航。
4. 使用 MutationObserver 接收页面变化，同时保留低频 watchdog 兜底。
5. 监听页面 URL、章节标识、视频 currentSrc、readyState、paused、ended、currentTime、duration 以及文档滚动状态。
6. 页面脚本通过 [yxy:event] JSON 日志向 Python 回传结构化事件；普通日志继续使用 [yxy]。
7. 将散落的 .click() 收敛到统一动作入口。动作必须有前置状态、单次执行锁、超时、最大重试次数和成功后置条件。
8. 优先 DOM 语义定位；必要时获取元素矩形并使用 CDP Input.dispatchMouseEvent 按 mouseMoved、mousePressed、mouseReleased 顺序点击。
9. 点击后必须验证弹窗消失、章节标识变化、URL 变化或媒体源变化；不得无验证连续点击。
10. 收窄自动弹窗处理规则，避免根据“继续、确定、关闭”等宽泛文字点击普通按钮。
11. 增加视频 watchdog：只有在视频应播放、readyState 足够、未 ended 且 currentTime 多轮不增长时才判定停滞；恢复次数必须有限。
12. stop() 必须清除注入脚本所有 interval、timeout、MutationObserver 和事件动作，停止 Python 文档线程并关闭 CDP；再次 start 不继承旧状态。
13. 第一阶段不要引入 OCR。
14. 模板匹配只作为可选后续阶段。若没有用户提供的脱敏模板，请只实现可测试接口，不要制作或提交真实页面模板。
15. 不修改 web/src 下的前端代码，除非发现构建失败由本任务直接引起；若必须修改，先停止并说明原因，不要自行扩大范围。

实施方式：
- 先检查 git status 和现有 diff，区分用户已有修改。
- 先补测试，再小步修改实现。
- 不要一次性重写无关模块。
- 对每项状态转换和动作验证写清楚注释。
- 所有测试使用 mock 或纯函数，不连接学校接口和真实浏览器。

至少新增测试：
- 状态转换合法性。
- 同一完成事件只导航一次。
- 视频和文档不会同时导航。
- stop 后不再动作。
- 页面换章后重置旧状态。
- CDP 点击事件顺序与坐标正确。
- 后置条件失败时有限重试。
- INJECT_JS 不包含 fakeMouseActivity、随机 mousemove、答案读取或测验提交。

完成后执行：
python -m unittest -v test_backend.py test_course.py test_launcher.py
python -m py_compile yxy_course.py yxy_backend.py backend_commands.py web_server.py browser_launcher.py
在 web 目录执行 npm run build

最终回复需要说明：
- 修改了哪些文件。
- 状态机和动作验证如何工作。
- 删除了哪些旧的伪活跃逻辑。
- 添加了哪些测试。
- 所有验证命令的结果。
- 仍需真实页面人工验证的项目。
```
