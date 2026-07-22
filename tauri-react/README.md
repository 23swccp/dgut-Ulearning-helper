# 优学院签到助手（Tauri + React）

这是桌面端的新前端工程：React 负责界面，Tauri 负责 Windows 窗口，Python sidecar 继续负责签到业务。终端、浏览器/日志/关于设置页均已接入真实后端日志与命令。

## 开发

```powershell
npm install
npm run tauri dev
```

## 打包

```powershell
npm run build:sidecar
npm run tauri build -- --bundles nsis
```

首次在 Windows 上编译，需要安装 Rust、Python（含 `requests`、`websocket-client`、`pyinstaller`）和 Visual Studio C++ Build Tools（含 Windows SDK）。`build:sidecar` 会生成随安装包携带的 Python 后端。
