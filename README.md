# sing-geosite → Surge

将 [SagerNet/sing-geosite 的 rule-set 分支](https://github.com/SagerNet/sing-geosite/tree/rule-set)中的指定 SRS 转为 Surge 订阅文件，每日通过 GitHub Actions 更新。Python 3.9+、标准库、官方 sing-box 1.14.4 解码，无需服务器。

## 先指定范围

编辑 `config.json`，填入需要的分类，例如：

```json
{
  "categories": ["telegram", "apple"]
}
```

目前清单留空，等待确定范围；空清单时 GitHub 工作流会跳过构建和发布，本地命令会明确报错，不会自动转换全仓库。分类可使用 `telegram`、`geosite-telegram` 或 `geosite-telegram.srs`。缺失分类会让构建失败。

## GitHub 启用

1. 创建公开仓库，将本项目上传到 `main` 分支，保留 `.github` 目录。
2. 填好 `config.json`，在仓库 Actions 页面启用工作流。
3. 打开 **Update Surge rules → Run workflow**，首次手动运行。
4. 成功后自动创建 `rules` 分支，查看其中的 `manifest.json` 和转换报告。

每天北京时间 **07:23**（UTC 23:23）执行，也支持手动执行和修改配置后触发。GitHub 调度可能延迟，不保证准点。使用仓库自带 `GITHUB_TOKEN`，无需个人令牌；若组织策略限制写入，需允许工作流写入仓库。

工作流采用严格模式：选中分类中任何一个不能完整转换，整次更新停止，保留上一版。下载、解码、解析失败同样不会发布。请检查 Actions 失败日志。

## 输出与订阅

- `ruleset/geosite-分类.list`：每个成功分类的标准订阅入口。
- `domainset/geosite-分类.txt`：仅在完整分类可由精确域名和后缀表达时生成。
- `manifest.json`：源文件 SHA-256、上游提交、转换状态和输出路径。
- `README.md`：转换报告。
- `status.json`：发布时的每日检查时间（UTC）。规则文件不带易变时间戳。

替换 `代理策略` 为你的 Surge 策略名：

```ini
[Rule]
RULE-SET,https://raw.githubusercontent.com/panghuchi/sing-geosite-surge/rules/ruleset/geosite-telegram.list,代理策略,update-interval=86400
```

若 manifest 同时提供 domainset，可改用：

```ini
[Rule]
DOMAIN-SET,https://raw.githubusercontent.com/panghuchi/sing-geosite-surge/rules/domainset/geosite-telegram.txt,代理策略,update-interval=86400
```

同一分类选一种即可。默认推荐 RULE-SET，避免分类以后新增关键词时需要改订阅格式。

## 转换语义与限制

支持精确域名、域名后缀和关键词。sing-box 前导点后缀只匹配子域名，不含根域名，因此转换为 `DOMAIN-WILDCARD,*.example.com`；若也有精确根域名，则合并为 `DOMAIN-SUFFIX`。DOMAIN-SET 只有在能完整表示分类时生成，不会删掉其他规则凑成域名集。请使用支持 DOMAIN-WILDCARD 的 Surge 版本。

当前不支持域名正则、逻辑条件、取反和未知字段，遇到这些内容会拒绝整个分类。域名正则不能直接换成 URL-REGEX，两者匹配对象不同。2026-11-04 的兼容性扫描中，OpenAI、Netflix、Google、CN 等分类包含域名正则；范围确认后，可进一步评估其中正则的等价转换。第一版不承诺这些分类可用。

发布还有回退保护：上一版成功分类若变成不支持或消失，会阻止发布。主动缩小范围时，应先确认需要删除哪些订阅，再从 `rules` 分支的 manifest 和对应文件中移除这些分类，之后重新运行。

每日检查更新 `status.json`，便于观察任务是否运行；规则本身没变时内容保持不变。GitHub 对无活动公开仓库的定时工作流有停用机制，仍应偶尔检查 Actions 状态。

## 本地构建

安装 [官方 sing-box 1.14.4](https://github.com/SagerNet/sing-box/releases/tag/v1.14.4)，然后：

```sh
git clone --depth 1 --branch rule-set https://github.com/SagerNet/sing-geosite.git upstream
python3 scripts/convert.py --source upstream --output dist --config config.json --strict --revision "$(git -C upstream rev-parse HEAD)"
```

也可直接指定范围和二进制位置：

```sh
python3 scripts/convert.py --source upstream --output dist-selected --include telegram apple --sing-box /path/to/sing-box --strict
```

输出目录必须不存在，避免覆盖旧产物；重新运行请选择新目录。`--workers 4` 控制并发数量。仅当显式传入 `--all` 时才全量转换；非严格模式会生成其他完整分类，并在报告中列出无法转换的分类。

## 验证

```sh
python3 -m unittest discover -s tests -v
```

覆盖精确匹配、前导点后缀与根域名语义、关键词、去重、正则及未知条件拒绝、发布回退保护。官方工具已成功反编译实测的 1,995 个 SRS；其中 1,936 个完整转换为 RULE-SET、1,866 个同时可生成 DOMAIN-SET，59 个因正则未转换。该全量产物未保留，正式范围由配置决定。Surge 客户端加载及 GitHub Actions 远程执行仍需在相应环境验证。

生成数据保留其上游授权要求，参见 [sing-geosite](https://github.com/SagerNet/sing-geosite) 及 [domain-list-community](https://github.com/v2fly/domain-list-community)。格式依据：[Surge RULE-SET](https://manual.nssurge.com/rules/ruleset.html)、[Surge DOMAIN-SET](https://manual.nssurge.com/rules/domain.html)。
