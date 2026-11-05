# sing-geosite-surge

可配置的多源规则构建器：读取规则源，经格式适配、合并去重后，生成 Surge 订阅文件。每天北京时间 **07:23** 通过 GitHub Actions 更新。

[查看生成的规则与报告](https://github.com/panghuchi/sing-geosite-surge/tree/rules) · [手动运行](https://github.com/panghuchi/sing-geosite-surge/actions)

## 设计思路

**源由配置决定，转换由格式适配器负责，目标文件由统一输出器生成。** 同一种格式的多个源复用同一个适配器；来源不同，不意味着要复制一套转换逻辑。

```text
config.json
  ├─ 源 A：sing-box SRS ───→ SRS 适配器 ───┐
  ├─ 源 B：V2Ray Geosite ──→ DAT 适配器 ───┤
  ├─ 源 C：V2Ray Geosite ──→ DAT 适配器 ───┤
  └─ 其他源：对应格式 ─────→ 对应适配器 ───┘
                                          ↓
                                    统一规则模型
                                          ↓
                                按目标分类合并、规范化、去重
                                          ↓
                                    Surge 装配输出
                                          ↓
                         每个分类一个最终 RULE-SET 文件
                         可选 DOMAIN-SET、脚本及来源报告
```

这几层的职责分别是：

| 层 | 职责 | 实现位置 |
| --- | --- | --- |
| 配置 | 声明源地址、格式、目标分类、分类映射和失败策略 | `config.json` |
| 源适配 | 下载并解析特定格式，输出统一规则；不生成 Surge 文件 | `scripts/sources.py`、`scripts/geosite_dat.py` |
| 合并 | 对同一目标分类的统一规则做规范化、合并去重，并记录贡献 | `scripts/merging.py` |
| 装配 | 将合并后的规则生成 RULE-SET、DOMAIN-SET、脚本模块和报告 | `scripts/convert.py`、`scripts/regex_tools.py` |
| 发布 | 检查覆盖范围是否回退，再更新 rules 分支 | `scripts/publish.py` |

主流程不硬编码 SagerNet、Loyalsoldier 或 MetaCubeX。Geosite、GeoIP 的 SRS 都复用同一个适配器。增加同格式源只改配置；增加新格式才扩展适配器注册表 `ADAPTERS`，合并与输出逻辑不需要跟着修改。

## 源配置

所有源统一声明在 `config.json` 的 `sources` 数组中。当前有三个 Geosite 源和两个 GeoIP 源：

- SagerNet/sing-geosite：SRS 模板地址。
- Loyalsoldier/v2ray-rules-dat：geosite.dat。
- MetaCubeX/meta-rules-dat：geosite.dat。
- Telegram GeoIP：Loyalsoldier SRS。
- CN GeoIP：SagerNet SRS。

### 同一批分类的多个源

下面是配置结构示例，完整范围以仓库的 `config.json` 为准：

```json
{
  "categories": ["netflix", "paypal", "paypal@cn"],
  "strict": true,
  "sources": [
    {
      "id": "sagernet",
      "format": "sing-box-srs",
      "url_template": "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-{category}.srs"
    },
    {
      "id": "loyalsoldier",
      "format": "v2ray-geosite",
      "url": "https://raw.githubusercontent.com/Loyalsoldier/v2ray-rules-dat/release/geosite.dat"
    },
    {
      "id": "metacubex",
      "format": "v2ray-geosite",
      "url": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/release/geosite.dat"
    }
  ]
}
```

- `id`：唯一来源标识，用于报告和追踪，不参与转换逻辑判断。
- `format`：选择已注册的格式适配器，目前支持 `sing-box-srs`、`v2ray-geosite`。
- `url_template`：SRS 模板，`{category}` 替换为上游分类名，只下载选中的分类。
- `url`：DAT 整库地址，或单个 SRS 文件地址。
- `categories`：默认的 Geosite 目标分类；输出时统一命名为 `geosite-分类`。

例如三个源的 Netflix 最终都合并到 **`ruleset/geosite-netflix.list`**，不会为每个源分别生成 Netflix 订阅。更换、增加来源不改变目标文件路径。

### 分类映射与指定目标

源的分类名不一致时，用 `category_map`：

```json
{
  "id": "another-geosite",
  "format": "v2ray-geosite",
  "url": "https://example.com/geosite.dat",
  "category_map": {"category-ai-!cn": "upstream-ai-category"},
  "on_missing": "error"
}
```

默认按同名分类读取。`on_missing` 默认 `error`，缺分类就停止构建；明确允许缺少的源可设为 `skip`，缺失项仍进入报告。

单文件源或需要单独指定输出目标时，用 `targets`。键是目标名，值是上游分类名；有此字段时不使用全局 `categories`：

```json
{
  "id": "telegram-ip",
  "format": "sing-box-srs",
  "url": "https://cdn.jsdelivr.net/gh/Loyalsoldier/geoip@release/srs/telegram.srs",
  "targets": {"geoip-telegram": "telegram"}
}
```

固定 SRS URL 只允许对应一个上游分类，模板地址可以对应多个分类。不要用不同的目标名表示同一个预期输出，否则会成为两个分类。

### 新增格式

在 `scripts/sources.py` 的 `ADAPTERS` 中注册读取函数，遵循 `(config, categories, fetch, binary) → (documents, metadata)` 契约：

- `documents`：上游分类名到统一规则文档的映射。
- `metadata`：来源 URL、文件 SHA-256 等追踪信息。
- 文档为 `{"version": 1, "rules": [...]}`；规则字段采用 `domain`、`domain_suffix`、`domain_keyword`、`domain_regex`、`ip_cidr`。
- 域名与 IP 条件分开为独立规则，保留原匹配语义。适配器不得自行输出 Surge 语法。

适配器完成后，配置中指定新 `format` 即可接入同一合并和装配流程。不要在主流程增加按仓库名字判断的分支。

## 合并、去重与失败处理

源按配置顺序合并。按规则类型和内容去重，并统一“精确根域名＋前导点后缀”等等价写法。父子域名覆盖关系未做全部压缩，不同分类之间也保留重叠，便于使用不同策略。

`paypal@cn` 优先读取源中明确的独立分类，否则仅筛选 paypal 内带 cn 属性的条目，不会拿完整 paypal 代替。`aliases` 仅创建目标别名，例如原配置里的 `geosite-ai-!cn` 映射到 `geosite-category-ai-!cn`。

默认严格模式：下载、解析、转换失败或缺少必需分类时不发布；已发布目标消失或无法完整转换时，也保留上一版。空分类清单时 GitHub 工作流跳过构建与发布。

manifest 的 `sources` 记录所有来源及校验值，`categories[*].contributions` 记录各源条目数和新增数量。新增数量是去重后的规则条目数，不等于新增覆盖域名数。`upstream_revision` 是本次全部来源元数据的指纹，不是某一个仓库的提交 SHA。

## Surge 使用

同一分类的 RULE-SET 和 DOMAIN-SET 选一个即可，推荐统一使用 RULE-SET：

```ini
[Rule]
RULE-SET,https://raw.githubusercontent.com/panghuchi/sing-geosite-surge/rules/ruleset/geosite-netflix.list,你的Netflix策略,update-interval=86400
```

DOMAIN-SET 仅在整个分类可完整表示为精确域名和后缀时生成。含 IP、通配符或脚本的分类不会生成对应 DOMAIN-SET，因此两种文件的数量不必一致。

部分域名正则可转为原生 DOMAIN-WILDCARD；其余经兼容性检查的正则通过规则脚本匹配 hostname。**Google、AI、CN、geolocation-cn、geolocation-!cn 当前需要安装并启用 [Regex Support 模块](https://raw.githubusercontent.com/panghuchi/sing-geosite-surge/rules/regex-support.sgmodule)，并启用 Surge 脚本。** 具体依赖以当次报告和 manifest 的 `requires_module` 为准。

模块只注册脚本，不添加路由策略、DNS 或 MITM 设置。脚本不联网、不发 DNS 查询，远程脚本每日检查更新。缺少模块或禁用脚本时，相应分类无法完整匹配。

IP 规则带 `no-resolve`。本项目不推断分类的策略和优先级，需在 Surge 主配置中自行指定。现有 20 个 Geosite 目标、2 个 GeoIP 目标及 1 个 AI 别名的订阅路径保持不变。

## 执行与验证

GitHub 每天北京时间 **07:23** 自动运行，可能延迟。也可在 Actions → Update Surge rules → Run workflow 手动执行。规则发布到 `rules` 分支，`status.json` 记录检查时间。

本地需要 Python 3.9+ 和 [官方 sing-box 1.14.4](https://github.com/SagerNet/sing-box/releases/tag/v1.14.4)：

```sh
python3 scripts/convert.py --config config.json --output dist --sing-box /path/to/sing-box
python3 -m unittest discover -s tests -v
```

不需要再手动 clone SagerNet 或传入 `--source`。全部来源从配置读取。输出目录必须不存在，避免覆盖旧产物；`--workers` 控制目标转换并发。JavaScript 差分测试需要 Node，GitHub 工作流安装 Node 22。

产物包括 `ruleset/`、可选的 `domainset/`、正则脚本与模块、`manifest.json`、检查状态及报告。报告提供每个目标的下载链接和模块依赖。

数据及测试样本保留各上游授权要求。格式依据：[V2Ray protobuf](https://github.com/v2fly/v2ray-core/blob/master/app/router/routercommon/common.proto)、[Surge 域名规则](https://manual.nssurge.com/rules/domain.html)、[规则脚本](https://manual.nssurge.com/scripting/rule.html)。数据来源：[SagerNet/sing-geosite](https://github.com/SagerNet/sing-geosite)、[Loyalsoldier/v2ray-rules-dat](https://github.com/Loyalsoldier/v2ray-rules-dat)、[MetaCubeX/meta-rules-dat](https://github.com/MetaCubeX/meta-rules-dat)、[SagerNet/sing-geoip](https://github.com/SagerNet/sing-geoip)、[Loyalsoldier/geoip](https://github.com/Loyalsoldier/geoip)。
