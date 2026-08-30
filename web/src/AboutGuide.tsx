import { useEffect, useState } from "react";

type AboutGuideProps = {
  version: string;
  repo: string;
};

const tocItems: ReadonlyArray<{ id: string; label: string; sub?: boolean }> = [
  { id: "guide-sign", label: "课程签到" },
  { id: "sign-login", label: "启动与登录", sub: true },
  { id: "sign-course", label: "选择课程", sub: true },
  { id: "sign-monitor", label: "签到结果", sub: true },
  { id: "guide-course", label: "课件学习" },
  { id: "course-open", label: "打开课件", sub: true },
  { id: "course-command", label: "常用命令", sub: true },
  { id: "course-status", label: "运行状态", sub: true },
  { id: "guide-quiz", label: "自动答题" },
  { id: "guide-settings", label: "设置说明" },
  { id: "guide-log", label: "日志与数据" },
  { id: "guide-help", label: "常见问题" },
  { id: "guide-about", label: "关于项目" },
];

export function AboutGuide({ version, repo }: AboutGuideProps) {
  const repoUrl = repo ? `https://github.com/${repo}` : "";
  const [activeSection, setActiveSection] = useState("guide-sign");

  useEffect(() => {
    const scrollRoot = document.querySelector<HTMLElement>(".about-page")?.closest<HTMLElement>(".settings-body") || window;
    const updateActiveSection = () => {
      let current = tocItems[0].id;
      for (const item of tocItems) {
        const target = document.getElementById(item.id);
        if (target && target.getBoundingClientRect().top <= 150) current = item.id;
        else break;
      }
      setActiveSection(current);
    };

    updateActiveSection();
    scrollRoot.addEventListener("scroll", updateActiveSection, { passive: true });
    return () => scrollRoot.removeEventListener("scroll", updateActiveSection);
  }, []);

  return (
    <section className="about-page" aria-label="使用教程与关于">
      <article className="about-document">
        <main className="about-content">
          <header className="about-heading">
            <h1>优学院助手使用教程</h1>
            <div className="about-reading-meta" aria-label="文档信息">
              <span>约 1500 字</span>
              <span>大约 6 分钟</span>
              <span>v{version || "…"}</span>
            </div>
            <p>本页按照实际使用顺序介绍课程签到、课件学习和其它功能。第一次使用时，建议从课程签到开始阅读。</p>
          </header>

          <section id="guide-sign" className="guide-section" data-toc>
            <h2>课程签到<a className="header-anchor" href="#guide-sign" aria-label="课程签到的永久链接">#</a></h2>
            <p>签到模块用于读取课程、监测当天的课堂活动，并在检测到支持的签到类型后自动尝试签到。</p>

            <h3 id="sign-login" data-toc>1. 启动并完成登录<a className="header-anchor" href="#sign-login" aria-label="启动并完成登录的永久链接">#</a></h3>
            <ol>
              <li>启动程序并进入“课程签到”模块。</li>
              <li>程序会自动读取本机登录缓存，不需要按 Enter 手动读取。</li>
              <li>缓存有效时会直接显示课程列表；缓存失效时会自动准备好优学院登录页。</li>
              <li>如果出现登录页，请在程序打开的独立浏览器中完成登录，然后回到签到模块按 Enter 继续。</li>
            </ol>
            <aside className="guide-note">
              请始终在程序打开的浏览器中登录。日常使用的其它浏览器与程序的登录状态并不共用。
            </aside>

            <figure className="guide-figure">
              <img src="/about-workflow.svg" alt="优学院助手从读取登录缓存到签到和课件学习的工作流程" />
              <figcaption>程序启动后会自动读取登录缓存，再进入签到或课件学习流程。</figcaption>
            </figure>

            <h3 id="sign-course" data-toc>2. 选择签到课程<a className="header-anchor" href="#sign-course" aria-label="选择签到课程的永久链接">#</a></h3>
            <ol>
              <li>在输入框中输入课程名称、教师名称或课程 ID 进行搜索。</li>
              <li>使用上下方向键移动选项，按 Enter 确认；也可以直接用鼠标点击课程。</li>
              <li>确认课程后，再按 Enter 开始监测当天的签到活动。</li>
            </ol>

            <h3 id="sign-monitor" data-toc>3. 查看签到结果<a className="header-anchor" href="#sign-monitor" aria-label="查看签到结果的永久链接">#</a></h3>
            <p>开始监测后，程序默认每 5 秒检查一次。签到成功、重复签到、跳过原因和错误详情都会显示在终端中。</p>
            <ul>
              <li>输入 <code>/</code> 或 <code>stop</code>：停止当前监测。</li>
              <li>停止后再次输入 <code>/</code>：返回课程列表。</li>
              <li>签到记录：保存在设置中指定的日志位置。</li>
            </ul>
            <aside className="guide-note guide-note-warning">
              二维码签到只有在活动数据本身包含签到码时才能处理；程序不会识别教室现场展示的二维码图片。
            </aside>
          </section>

          <section id="guide-course" className="guide-section" data-toc>
            <h2>课件学习<a className="header-anchor" href="#guide-course" aria-label="课件学习的永久链接">#</a></h2>
            <p>刷课模块用于辅助处理课件中的视频、文档、章节切换和提示弹窗。开始前，需要先在程序的浏览器中打开具体课件。</p>

            <h3 id="course-open" data-toc>1. 打开具体课件页面<a className="header-anchor" href="#course-open" aria-label="打开具体课件页面的永久链接">#</a></h3>
            <ol>
              <li>使用程序启动的同一个浏览器登录优学院。</li>
              <li>进入课程并打开需要学习的具体课件页面。</li>
              <li>确认页面地址中包含 <code>ua.dgut.edu.cn/learnCourse</code>。</li>
              <li>返回程序的“刷课”模块，按 Enter 或输入 <code>start</code> 启动。</li>
            </ol>
            <p>课程门户、课程列表或普通课程首页不是有效的控制目标。</p>

            <h3 id="course-command" data-toc>2. 常用命令<a className="header-anchor" href="#course-command" aria-label="常用命令的永久链接">#</a></h3>
            <table className="guide-table">
              <thead><tr><th>命令</th><th>作用</th></tr></thead>
              <tbody>
                <tr><td><code>Enter</code> / <code>start</code></td><td>启动课件学习辅助</td></tr>
                <tr><td><code>open</code></td><td>打开优学院课件网站</td></tr>
                <tr><td><code>speed 8</code></td><td>把视频倍速调整为 8×，支持 1–16</td></tr>
                <tr><td><code>stop</code> / <code>/</code></td><td>停止当前刷课任务</td></tr>
                <tr><td><code>clear</code></td><td>清空当前模块的显示日志</td></tr>
              </tbody>
            </table>

            <h3 id="course-status" data-toc>3. 查看运行状态<a className="header-anchor" href="#course-status" aria-label="查看运行状态的永久链接">#</a></h3>
            <p>运行期间，状态区域会显示当前课程、课件页面、视频进度、任务计划、重试次数和停滞状态。课件页面可以留在后台，程序会继续处理已连接的页面。</p>
          </section>

          <section id="guide-quiz" className="guide-section" data-toc>
            <h2>自动答题<a className="header-anchor" href="#guide-quiz" aria-label="自动答题的永久链接">#</a></h2>
            <p>自动答题需要在“设置 → 刷课”中启用。开启总开关后，可以分别启用选择题、判断题和填空题。</p>
            <ul>
              <li><strong>选择题：</strong>使用当前设定的固定选项。</li>
              <li><strong>判断题：</strong>使用当前设定的固定判断结果。</li>
              <li><strong>填空题：</strong>填写当前设定的占位内容。</li>
            </ul>
            <p>三个子选项全部关闭时，“自动答题”总开关也会自动关闭。无法识别的题型会被跳过，并在运行日志中留下说明。</p>
            <aside className="guide-note guide-note-warning">
              自动答题不会搜索题库，也不能保证答案正确。该功能可能产生错误作答，请了解风险后再启用。
            </aside>
          </section>

          <section id="guide-settings" className="guide-section" data-toc>
            <h2>设置说明<a className="header-anchor" href="#guide-settings" aria-label="设置说明的永久链接">#</a></h2>

            <h3>浏览器</h3>
            <p>用于选择程序连接的 Chromium 浏览器。通常保持自动检测即可；只有自动检测失败时才需要填写浏览器路径。</p>

            <h3>刷课</h3>
            <p>可以设置视频倍速，以及是否处理选择题、判断题和填空题。</p>

            <h3>账号登录恢复</h3>
            <p>学号和密码输入框目前已经锁定，暂时不能编辑。后续完成账号自动重新登录功能后再开放。</p>

            <h3>日志与数据</h3>
            <p>可以控制是否保存签到与错误详情，也可以修改签到日志的保存位置。相对路径会以程序目录为基准。</p>
          </section>

          <section id="guide-log" className="guide-section" data-toc>
            <h2>日志与本地数据<a className="header-anchor" href="#guide-log" aria-label="日志与本地数据的永久链接">#</a></h2>
            <table className="guide-table">
              <thead><tr><th>文件或目录</th><th>用途</th></tr></thead>
              <tbody>
                <tr><td><code>config.json</code></td><td>保存浏览器、签到日志和刷课设置</td></tr>
                <tr><td><code>auth.json</code></td><td>保存 Token 和用户 ID 缓存</td></tr>
                <tr><td><code>browser_profile/</code></td><td>保存独立浏览器配置和登录状态</td></tr>
                <tr><td><code>签到记录.md</code></td><td>保存签到结果与错误详情</td></tr>
              </tbody>
            </table>
            <p>程序的本地服务只监听 <code>127.0.0.1</code>。签到日志会隐藏常见的 Token、Authorization、Password、Cookie 和 Bearer 凭据。</p>
          </section>

          <section id="guide-help" className="guide-section" data-toc>
            <h2>常见问题<a className="header-anchor" href="#guide-help" aria-label="常见问题的永久链接">#</a></h2>

            <h3>登录后仍然没有显示课程</h3>
            <p>确认登录发生在程序启动的独立浏览器中。完成登录后，回到课程签到模块按 Enter 重试。</p>

            <h3>刷课提示“未找到课件学习页”</h3>
            <p>确认已经在同一个浏览器中打开具体课件，且页面地址包含 <code>ua.dgut.edu.cn/learnCourse</code>。</p>

            <h3>签到或测验操作失败</h3>
            <p>先查看界面中的关键事件和签到日志。优学院页面结构升级后，既有页面选择器可能需要同步调整。</p>
          </section>

          <section id="guide-about" className="guide-section guide-about" data-toc>
            <h2>关于项目<a className="header-anchor" href="#guide-about" aria-label="关于项目的永久链接">#</a></h2>
            <p>优学院助手 v{version || "…"} 是非官方个人项目，与学校及优学院平台没有官方关联。请遵守学校规定、课程要求和平台规则。</p>
            {repoUrl && <p><a href={repoUrl} target="_blank" rel="noreferrer">在 GitHub 查看项目 ↗</a></p>}
          </section>
        </main>

        <aside className="about-sidebar">
          <nav className="about-toc" aria-label="此页内容">
            <strong>此页内容</strong>
            {tocItems.map(item => (
              <a
                key={item.id}
                className={`${item.sub ? "sub " : ""}${activeSection === item.id ? "active" : ""}`.trim()}
                href={`#${item.id}`}
                onClick={() => setActiveSection(item.id)}
              >
                {item.label}
              </a>
            ))}
          </nav>
        </aside>
      </article>
    </section>
  );
}
