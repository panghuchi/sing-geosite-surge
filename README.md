# sing-geosite → Surge

将指定的 sing-box SRS 规则转换为 Surge RULE-SET / DOMAIN-SET，每天北京时间 **07:23** 通过 GitHub Actions 更新。使用 Python 标准库及官方 sing-box 1.14.4 反编译，不需要服务器。

## 使用规则

在 [rules 分支](https://github.com/panghuchi/sing-geosite-surge/tree/rules)查看每个分类的下载链接及是否需要脚本模块。同一分类的 RULE-SET 和 DOMAIN-SET 选一个即可，推荐统一使用 RULE-SET。

Netflix 示例，替换策略名称后放到 Surge 主配置的 `[Rule]` 中：

```ini
RULE-SET,https://raw.githubusercontent.com/panghuchi/sing-geosite-surge/rules/ruleset/geosite-netflix.list,你的Netflix策略,update-interval=86400
```

### 正则支持模块

**Google、AI、CN、geolocation-cn、geolocation-!cn 需要额外安装并启用此模块，同时启用 Surge 的脚本功能：**

[安装所需的 Regex Support 模块](https://raw.githubusercontent.com/panghuchi/sing-geosite-surge/rules/regex-support.sgmodule)

在 Surge 的模块管理中通过上面的 URL 添加。模块仅注册规则脚本，不添加路由规则、不改变策略、DNS 或 MITM 设置。规则脚本只读取请求的 hostname 并匹配正则，不联网、不触发 DNS 查询。远程脚本每 86400 秒检查更新。

安装模块后，仍使用各分类的普通 RULE-SET 链接。例如：

```ini
RULE-SET,https://raw.githubusercontent.com/panghuchi/sing-geosite-surge/rules/ruleset/geosite-google.list,你的Google策略,update-interval=86400
```

缺少模块、禁用模块或禁用脚本时，含 SCRIPT 的分类不能完整匹配；有的 Surge 版本会在配置检查时提示缺少脚本。**不要忽略这个依赖。** 在 `rules/manifest.json` 中，`requires_module` 字段标明所需模块；转换报告也有对应标记。将来上游规则变化时请更新模块，并以当次报告为准。

目前 Netflix 和 Disney 可完整转换为原生 DOMAIN-WILDCARD，无需脚本模块。其余原来已发布的分类继续使用原链接。

## 当前范围

配置从提供的 sing-box 片段提取，包含 20 个域名来源及 2 个 IP 来源：

- 域名：geolocation-!cn、category-ai-!cn、geolocation-cn、reddit、microsoft、apple、microsoft@cn、apple@cn、telegram、netflix、hbo、disney、cn、paypal、paypal@cn、google、youtube、github、steam、steam@cn。
- IP：Loyalsoldier/geoip 的 Telegram SRS，以及 SagerNet/sing-geoip 的 CN SRS，保留原下载地址。
- 原配置里的 `geosite-ai-!cn` 实际来自 `geosite-category-ai-!cn.srs`。同时提供 `geosite-ai-!cn.list` 别名，二者选一个即可。

因此产物清单包含 **23 个条目（22 个来源 + 1 个 AI 别名）**，均可完整表达；其中部分条目依赖上面的模块。本项目只配置分类来源，不根据下载用的 detour 推断路由策略，也不推断分类顺序。

## 转换方式

- 精确域名、后缀、关键词转换为对应的 Surge 规则。
- sing-box 的 `.example.com` 后缀不包含根域名，转换为 `DOMAIN-WILDCARD,*.example.com`。若同时存在根域名，则合并为 DOMAIN-SUFFIX。
- IPv4 / IPv6 转为 `IP-CIDR` / `IP-CIDR6`，带 `no-resolve`，不为匹配 IP 主动触发域名解析。
- 正则中可完整表达的锚点、分支、字符类、有限重复及任意字符重复，转换为 DOMAIN-WILDCARD。特别保留 `.+` 至少一个字符的语义，不简单替换成 `*`。
- 无法用通配符表达、但经过语法兼容检查的正则，通过 `SCRIPT` 子规则调用生成的 JavaScript hostname 匹配器；不使用 URL-REGEX。
- DOMAIN-SET 仅在整个分类都能用精确域名和后缀表示时生成，不丢掉正则、关键词或 IP 凑成域名列表。

逻辑规则、取反、未知字段和无法确认兼容的正则仍会拒绝转换。当前 `config.json` 已开启 `strict: true`，选中来源有任意一个失败就停止发布，保留上一版；不会再悄悄缺少分类。

## 修改范围与手动执行

修改 `config.json` 中的 `categories`，值按上游文件名填写，例如 `telegram`、`netflix`。`extra_sources` 用于 GeoIP 的 tag 和 HTTPS URL，`aliases` 维护 tag 别名。配置修改后会自动运行更新。空分类清单时工作流跳过构建及发布。

手动运行：[Actions](https://github.com/panghuchi/sing-geosite-surge/actions) → **Update Surge rules** → **Run workflow** → 选择 `main`。完成后查看 `rules` 分支的报告。

上传到自己的新公开仓库时，保留 `.github` 目录。如果需要修改仓库地址，在 `config.json` 设置 `publish_base_url` 为自己 rules 分支的 Raw 根地址；这用于生成模块中的远程脚本链接。GitHub 工作流使用自带的 `GITHUB_TOKEN`，无需个人访问令牌，组织策略需允许工作流写入仓库。

GitHub 调度可能延迟，不保证准点。规则内容无变化时保持原内容；`status.json` 记录每日检查时间。上游下载或解码失败、不支持内容出现、已发布分类覆盖范围缩小时，整次发布停止。主动移除分类前，需确认不再使用，并相应移除 rules 分支的旧 manifest 条目及文件。

## 产物

```text
ruleset/geosite-分类.list
ruleset/geoip-分类.list
domainset/geosite-分类.txt    # 能完整表达时才生成
scripts/srs-regex-*.js        # 需要脚本时才生成
regex-support.sgmodule       # 统一脚本注册模块
manifest.json
status.json
README.md                    # 转换报告及下载链接
```

manifest 记录上游提交、SRS 的 SHA-256、来源 URL、规则数、脚本依赖及不支持原因。AI 别名指向同一个正则匹配脚本，避免重复注册。

## 本地构建与验证

安装 [官方 sing-box 1.14.4](https://github.com/SagerNet/sing-box/releases/tag/v1.14.4)，Python 3.9+：

```sh
git clone --depth 1 --branch rule-set https://github.com/SagerNet/sing-geosite.git upstream
python3 scripts/convert.py --source upstream --output dist --config config.json --revision "$(git -C upstream rev-parse HEAD)"
```

直接指定分类：

```sh
python3 scripts/convert.py --source upstream --output dist-selected --include netflix disney telegram --strict --sing-box /path/to/sing-box
```

输出目录必须不存在，避免覆盖旧产物；`--workers 4` 控制解码并发，只有显式指定 `--all` 才全量转换。

测试需要 Node.js 验证生成的 JavaScript；没有 Node 时对应测试会跳过，GitHub 工作流显式安装 Node 22：

```sh
python3 -m unittest discover -s tests -v
```

测试覆盖根域名边界、Netflix 通配符、Disney 可选字符、数字重复反例、IP、发布回退，以及所选来源 159 条不同正则的差分验证。正则样本来自 SagerNet/sing-geosite 的 SRS 数据及其上游 domain-list-community，保留其原授权要求。本地完整构建及 Surge CLI 离线配置语法检查通过；该检查不会修改当前 Surge 配置，也不等同于实际流量验证。

参考：[Surge 通配符](https://manual.nssurge.com/rules/domain.html)、[规则脚本](https://manual.nssurge.com/scripting/rule.html)、[模块](https://manual.nssurge.com/profile/module.html)、[脚本更新](https://manual.nssurge.com/scripting/overview.html)。数据来源：[sing-geosite](https://github.com/SagerNet/sing-geosite)、[domain-list-community](https://github.com/v2fly/domain-list-community)、[sing-geoip](https://github.com/SagerNet/sing-geoip)、[Loyalsoldier/geoip](https://github.com/Loyalsoldier/geoip)。
