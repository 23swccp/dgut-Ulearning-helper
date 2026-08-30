# 测验自动答题模拟环境

本目录按照仓库根目录《测验页面结构调研.md》复刻优学院测验主文档的关键 DOM：

- 选择题：`a.choice-item` 与 `.option`
- 判断题：`.checking-type .choice-btn`
- 填空题：`.answer-width` 内输入框
- 提交：`.question-operation-area .btn-submit`
- 已答状态：`.question-wrapper.finished`

在仓库根目录运行：

```powershell
python quiz_simulator.py
```

脚本会启动隔离的无界面 Chromium，使用产品中的 `QuizHandler` 和真实 CDP 鼠标/键盘事件，依次验证总开关、三类题全开、仅选择题、仅判断题、仅填空题，以及当前 `config.json` 设置。

需要观看过程时运行：

```powershell
python quiz_simulator.py --show --hold 30
```

模拟器只监听本机回环地址，使用临时浏览器配置，结束后自动关闭并清理；不会访问或提交到优学院。
