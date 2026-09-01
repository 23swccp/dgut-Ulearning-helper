# 项目维护提示

开始修改前先阅读 `交接文档.md`；涉及版本、安装器、自动更新或 Gitee 时，再完整阅读 `发布与更新维护.md`。

## 发布规则

- 当前应用版本以 `version.py` 的 `APP_VERSION` 为准；发布时同步更新 `web/package.json`、`web/package-lock.json`、关于页版本徽章及手动补发工作流默认标签。
- 只有用户明确要求发布时才创建并推送 `v*` 标签。不要替换已发布版本的安装器；任何程序内文件变化都发布新的补丁版本。
- 普通用户只应下载 `DgutBot-win-Setup-Qing-Zhi-Xia-Zai-Zhe-Ge.exe`。GitHub Release 中的 `.nupkg`、`RELEASES` 和 `releases.win.json` 供 Velopack 自动更新使用。
- GitHub 是应用内更新源；Gitee 仅作为首次安装器的国内备用下载源。完整 `Setup.exe` 可离线安装，首次启动的更新检查仍需访问 GitHub。
- GitHub Actions Secret `GITEE_TOKEN` 已配置，用于同步源码、标签和 Release 安装器。绝不能读取、输出、提交或写入文档；失效时只提示维护者重新设置同名 Secret。

## 发布前验证

```powershell
python -m pytest -q test_backend.py test_course.py test_launcher.py test_quiz.py test_updater.py test_gitee_release.py
cd web
npm test -- --run
npm run build
```

发布后必须确认 GitHub 与 Gitee 的 `main`/版本标签指向同一提交、两个 Release 均存在，并验证 Gitee 安装器公开下载的文件名和大小。详细步骤见 `发布与更新维护.md`。
