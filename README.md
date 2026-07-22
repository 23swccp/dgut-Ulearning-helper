# 优学院签到助手

当前主程序是 **Tauri + React 桌面版**。React 负责终端式界面和设置页，Python 负责浏览器登录、课程读取与签到轮询。

## 目录说明

```text
优学院脚本/
├─ tauri-react/       主桌面程序：TypeScript、React、Tauri 与 sidecar 通信层
├─ yxy_backend.py     Python 签到后端（浏览器、课程、轮询、日志）
├─ test_backend.py    后端基础回归测试
├─ requirements.txt   Python 开发依赖
├─ release/           可发给朋友的安装包与免安装发布包
├─ archive/           本地保留的旧界面原型（不上传到 GitHub）
├─ browser_profile/   本机浏览器登录配置目录；属于个人运行数据，不提交、不发送
└─ config.json        本机浏览器与日志设置；属于个人运行数据，不提交、不发送
```

## 日常测试

直接运行已经打包的程序：

`tauri-react/src-tauri/target/release/yxy-desktop.exe`

发给朋友时，优先使用：

`release/优学院签到助手_初代发布包/安装版/优学院签到助手_0.1.0_x64-setup.exe`

## 开发命令

在 `tauri-react` 目录运行：

```powershell
npm run tauri -- dev
```

修改 Python 后端后，先重新生成 sidecar，再生成安装包：

```powershell
npm run build:sidecar
npm run tauri -- build --bundles nsis
```

## 后端测试

```powershell
python -m unittest -v test_backend.py
```

## 安全提醒

- `browser_profile/`、`config.json`、运行日志和登录信息都可能是个人数据，开源或发送项目之前必须排除。
- 发布给普通用户只发送 `release/` 里的安装包；不要发送整个源码目录。
