# 聚合搜索 / Search Governor

Search Governor 是一个与搜索供应商解耦的聚合搜索治理引擎。它通过唯一的 `search` 入口调用手工注册的 adapter，将不同 Agent web search、搜索 Skill、API、浏览器脚本或爬虫的结果统一为 Candidate JSONL，再执行归一化、去重、预算分配、规则或模型重排、正文获取、信源评估与证据输出。

> Search Governor is a provider-neutral search governance engine with one search entry and a strict subprocess adapter contract.

## 当前状态

| 能力 | v0.1.1 状态 |
|---|---|
| CLI 与 adapter 协议 | 已支持 |
| 手工供应商注册 | 已支持 |
| fast / full / deep 治理模式 | 已支持 |
| OpenClaw 插件注册 | 已支持，是目前唯一验证过的 Agent 集成 |
| 其他 Agent 插件 | 架构可扩展，尚无已验证实现 |

项目不附带任何真实搜索供应商。公开仓库只包含治理内核、规范、mock adapter 和 OpenClaw 集成；本机供应商必须放在被 Git 忽略的 `providers.local/`。

## 唯一搜索入口

```text
sg search / Agent search-governor
        -> manually registered provider adapters
        -> Candidate JSONL
        -> normalize / dedupe / budget / rerank / fetch / evaluate
```

不存在第二个公开的结果注入入口。无论来源是 Agent 已配置的 web search、独立脚本还是 API，都必须先包装为统一 adapter，再由 `sg search` 调用。

## 安装

正式支持 Python 3.12、Linux 和 WSL。原生 Windows 与 macOS 尚未认证。

```bash
git clone https://github.com/wet86y/search-governor.git ~/.local/share/search-governor
cd ~/.local/share/search-governor
bash scripts/install.sh
sg health
```

安装脚本会创建 `.venv`、链接 `~/.local/bin/sg`，并在缺失时从 `config/.env.example` 创建本地 `config/.env`。

## 注册供应商

1. 在 `providers.local/<provider-id>/` 放置 `source.json` 和 adapter。
2. 在 `providers.local/registry.json` 手工添加该 ID、manifest 路径和启用状态。
3. 在本地 preset 覆盖文件中配置权重。
4. 运行 `sg health` 和验证脚本。

```json
{
  "sources": [
    {"id": "my_search", "path": "my_search/source.json", "enabled": true}
  ]
}
```

Adapter 从 stdin 读取一个请求 JSON，向 stdout 输出一行一个 Candidate JSON；stderr 可输出诊断信息，以及以 `SG_REPORT_JSON=` 开头的结构化参数报告。完整协议见 [Provider Adapter Contract](docs/PROVIDER_ADAPTER_CONTRACT.md)。

Agent 已配置的搜索工具也按同一方式接入。例如 OpenClaw web search provider 可由本地 adapter 调用稳定的 `openclaw infer web search --provider <id>` 命令，再映射为 Candidate JSONL。禁止把 `search-governor` 自身注册为内部来源。

## 搜索

```bash
sg search "query" --mode fast
sg search "query" --mode full
sg search "query" --mode deep \
  --point-question "What must be answered?" \
  --goal "Why this search is needed" \
  --boundaries "Scope constraints" \
  --output-use "How the evidence will be used"
```

没有配置本地供应商时，正式 `sg search` 会明确报错；公开 mock 只用于测试：

```bash
sg search "contract test" --providers mock --allow-disabled-sources \
  --allow-rule-fallback --no-fetch --format json
```

fast 在没有模型配置时使用规则排序。模型重排与 deep 分析通过 `config/*.local.json` 和本地密钥启用，公开模板不绑定模型厂商。deep 缺少分析模型时默认失败；显式使用 `--allow-analysis-fallback` 才会生成带醒目标记的确定性证据提纲。

模式与模板各自只负责一件事：模式决定总预算和处理等级，模板决定供应商集合及权重。兼容策略中 fast 的总预算为 15，full/deep 为 40；OpenClaw 插件的 fast 入口选择本地 `speed` 模板，但不使用调试预算覆盖参数。本地模板示例分配为：fast speed `6/3/3/3`、full/deep 五来源等权 `8/8/8/8/8`、四来源等权 `10/10/10/10`。

正文获取依次尝试供应商原生/内联正文和 Search Governor 直接 HTTP；只有 blocked、rate-limited、empty 等允许的失败类型才进入配置的浏览器回退。OpenClaw 集成可提供 browser 回退脚本，但平台爬虫应作为私有 provider 自主管理其浏览器/CDP 运行时。

## OpenClaw 集成

当前唯一验证过的 Agent 集成位于 `integrations/openclaw/`：

```bash
openclaw plugins install --link \
  /home/lenovo/.local/share/search-governor/integrations/openclaw --force
openclaw plugins inspect openclaw-search-governor-websearch --runtime
```

插件注册：

- `web_search` provider：`search-governor`
- companion tool：`search_governor_status`
- companion tool：`search_governor_read`

status/read 是搜索完成后的异步正文状态与读取工具，不是额外搜索入口。插件不会覆盖 OpenClaw 原生 `web_fetch`。

仓库同时提供可部署的 OpenClaw Skill 基础模板。维护者可在被忽略的 `integrations.local/openclaw-skill.local.md` 中追加本地平台路由规则，再生成只引用当前仓库 CLI 的薄 Skill：

```bash
python3 scripts/build_openclaw_skill.py
openclaw skills install build.local/openclaw-search-governor \
  --as openclaw-search-governor --force
```

生成 Skill 将普通快速请求交给插件，将 full/deep 请求交给同一个 `sg search`；特殊平台 provider 只有在用户明确要求时才通过 `--providers` 启用。

安装前需要保留旧 Skill 时，可先使用 `scripts/deploy_openclaw_skill.py` 将其原子归档；正式安装与生产验收使用 OpenClaw CLI 的本地目录安装入口。

## 本地与公开边界

整个运行目录可以位于 `~/.local/share/search-governor`，但以下内容永不进入 Git：

- `providers.local/` 和 `integrations.local/`
- `build.local/`、`legacy.local/` 和生成 Skill
- `config/.env` 与 `config/*.local.json`
- `data/`、缓存、日志、浏览器状态、连接器数据和 `.venv`

发布资产必须由 `scripts/export_bundle.sh` 从 Git tree 生成，禁止直接压缩工作目录。

维护者可设置 `SEARCH_GOVERNOR_DISABLE_LOCAL=1`，在本机工作目录中模拟纯公开发行环境。

## License

Apache License 2.0。真实供应商 adapter 不属于公开发行内容。
