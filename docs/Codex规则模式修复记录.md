# Codex 规则模式与开发环境修复记录

日期：2026-11-12（北京时间）。本次保留用户原有源码修改，未操作真实课程提交、未调用付费解题 API。

## 网络问题

本机代理客户端为 Clash Verge Rev，使用 Mihomo 和 TUN。排查开始时运行模式为 `global`。
Codex 主应用日志在 2026-11-11 23:55:50 记录 `workspace routing discovery timed out`，还出现过 `Workspace routing is unavailable`。

历史旧订阅的代理日志曾显示 `chat.openai.com` 命中 `GeoIP/cn → DIRECT` 并连接超时。当前订阅已经变化，不能据此断言当前规则集仍然遗漏该域名；当前 OpenAI 规则提供器已加载 35 条规则。
当前可直接确认的问题是出口不一致：全局模式走美国节点，规则模式的 AI 组继承通用代理组，走日本节点。因此修复同时保证核心域名优先分流，以及 OpenAI 请求使用一致的已验证出口。

已在当前订阅的规则增强文件中加入四条 prepend 规则：

```yaml
prepend:
  - DOMAIN-SUFFIX,openai.com,🤖 AI
  - DOMAIN-SUFFIX,chatgpt.com,🤖 AI
  - DOMAIN-SUFFIX,oaistatic.com,🤖 AI
  - DOMAIN-SUFFIX,oaiusercontent.com,🤖 AI
```

规则写入当前订阅的规则增强文件。文件可在 Clash Verge 的订阅设置中找到。
已通过 Clash Verge 的“重新激活订阅”重新生成配置，并从运行控制器确认四条规则位于列表最前，总规则数从 22 变为 26。修复不只写入生成的运行配置，当前订阅重新激活时仍能生成这四条规则。

AI 组选择 `美国[1] · 专线`，实际下级出口与此前可用的全局出口一致。随后通过客户端切换到规则模式，运行状态和保存的 `config.yaml` 均为 `rule`。通用代理组保留原选择。

已验证：

- `codex doctor --network-only --summary --no-color --ascii`：4 项通过，0 警告，0 失败，包括 HTTP 可达和 WebSocket HTTP 101 握手。
- 使用独立的 Codex app-server 完成 initialize / initialized 握手，然后调用 `account/read`，`refreshToken=false`；1.56 秒成功返回 ChatGPT 账号类型，输出不包含账号标识或凭证。
- 运行连接中 `chat.openai.com`、`chatgpt.com`、`ws.chatgpt.com`、`ab.chatgpt.com` 和 `codex-cloud-environments.chatgpt.com` 均命中优先域名规则并走 AI 组美国出口。
- 主应用日志核查到 00:11:08，修复后的新日志未再出现上述两类工作区路由错误；最后一次发现超时为前一天 23:56:05。桌面界面是否仍残留旧错误需要以重新加载后的实际显示为准。
- 项目依赖自检和源码 GUI 冒烟均退出 0；源码差异的空白检查通过。

配置备份保存在 Clash Verge 的本机配置目录下。备份可能含代理凭证，应保留在本机。
需要回滚时，先在客户端切回全局模式，将 AI 组恢复为 `🚀 Proxy`，再用备份中的规则增强文件恢复原文件并重新激活订阅。不要在客户端运行时直接覆盖整份 `profiles.yaml`。

这些规则绑定当前订阅；切换到另一订阅后要确认新订阅也有相同分流及一致出口。节点服务故障、账号权限和后续网络变化需另行诊断。

## 工程改进

- 原索引有 16,831 个文件；从当前 Git 索引移出 16,781 个虚拟环境、浏览器资料、日志、字节码和构建文件，原有跟踪文件剩 50 个（不含本次新增源码与文档）。本地文件仍保留，历史提交未重写。
- 补齐 `.gitignore`；新增 Python 3.12 开发依赖版本清单、Windows GitHub Actions 配置和拒绝零测试/跳过测试的检查入口。
- 修复停止后等待中的 AI 返回仍继续填写/提交，以及停止时误报完成的问题；新增 4 项取消边界回归。
- 最终本机完整检查为 62 项通过、0 失败、0 错误、0 跳过，耗时 75.624 秒；`pip check` 通过。远端 CI 尚未执行。
- 开发依赖清单的全新解析预检通过（`pip install --dry-run --ignore-installed -r requirements-dev.txt`），没有改变现有环境；这不能替代新环境实际运行和远端 CI 验收。
- 停止仍是协作式取消：底层 AI 请求和已进入的浏览器提交方法不能即时撤销；修复保证方法返回后不再进入后续写操作和导航。

## 开发插件

已通过本机 Codex CLI 安装官方目录中的 `github@openai-curated` 与 `codex-security@openai-curated`，安装列表确认二者 `installed=true`、`enabled=true`。

GitHub 用于代码评审、问题与 CI 协作；Codex Security 用于后续安全分析。安装不代表账号服务已连接或已执行远端扫描。GitHub 连接需要用户自行完成账号授权；本次没有自动创建凭证、上传代码或启动安全扫描。新插件在后续新会话中使用。

参考：[OpenAI 故障排查](https://learn.chatgpt.com/docs/reference/troubleshooting)、[Codex app-server 账号接口](https://learn.chatgpt.com/docs/app-server)、[Mihomo 控制器 API](https://wiki.metacubex.one/api/)。
