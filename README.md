# iPhone Duo 杭州 / 上海补货提醒

监测 **iPhone Duo 512GB 星光白色（MK2P4CH/A）** 在杭州、上海十家 Apple Store 的到店取货库存，通过 Bark 向 iPhone 发送提醒。无自动下单功能，无需电脑常开。

## 手机配置

1. 在 iPhone 安装 [Bark](https://bark.day.app/)，允许通知。在 Bark 首页的示例推送地址 `https://api.day.app/你的设备密钥/…` 中复制设备密钥。
2. 打开本仓库 **Settings → Secrets and variables → Actions → New repository secret**，名称填 `BARK_KEY`，值可填设备密钥，或直接粘贴 App 内复制的完整 `https://api.day.app/…` 推送链接。不要提交到文件或 Issue，也不必发送到聊天中。
3. 打开 **Actions → iPhone Duo pickup monitor → Run workflow**，选择 `test-push`，确认手机收到测试通知。该通知不表示有货。
4. 再手动选择 `run`，确认工作流成功。
5. 在 **Settings → Secrets and variables → Actions → Variables** 添加 `MONITOR_ENABLED`，值为 `true`。启用后每五分钟调度一次；队列繁忙时可能延迟。

暂停监测：把 `MONITOR_ENABLED` 改为 `false`，或在 Actions 页面禁用工作流。

**默认关闭定时监测。尚未配置手机密钥、未执行测试通知之前，不能确认手机提醒已启用。**

## 监测门店

杭州：西湖、杭州万象城。

上海：香港广场、上海环贸 iapm、南京东路、浦东、静安、环球港、五角场、七宝。

查询杭州邮编 `310000` 和上海邮编 `200000`，合并目标门店的商品库存。附近的苏州、宁波等城市不触发提醒。

## 提醒规则与故障

- 首次检测到可取货、或某店从不可取货变为可取货时提醒。持续有货不重复刷屏。
- 推送附门店、商品名称和官网购买链接，以 Apple 最终结账结果为准。
- HTTP 错误、非 JSON、未知状态、缺少目标门店均为“查询失败”，不会冒充“无货”。成功门店仍可触发补货提醒，失败门店保留上次状态。
- 连续三次查询不完整发送故障通知，最多每六小时一次；完整恢复后发送一次恢复通知。
- Bark 返回成功只代表接受推送；应在手机上实际确认通知权限与送达。
- 网络服务无法提供严格的单次投递保证；若通知已发送但 GitHub 状态保存失败，下一轮可能重复提醒。

打开任意工作流运行的 **Summary** 可查看十店查询结果和北京时间。若工作流本身停止运行或 GitHub 服务故障，程序也无法发故障通知；可同时开启 GitHub Actions 失败邮件通知。

## 免费额度与定时任务

[GitHub 标准运行器在公开仓库免费](https://docs.github.com/en/billing/concepts/product-billing/github-actions)。私有仓库消耗账号分钟额度，每五分钟运行可能超过免费额度；未经确认不要开启私有仓库长期监测。

[GitHub 定时任务](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)最短五分钟，可能延迟或丢弃；公开仓库连续六十天无仓库活动，定时任务可能被停用。不要把它视为秒级抢购服务。monitor-state 分支仅用于持久化监测结果，不以保活为目的。

## 本地检查与测试

Python 3.9+，无第三方依赖：

```sh
python3 -m unittest -v
python3 monitor.py --check
```

`--check` 只查询库存，无需密钥，不发送通知。正常监测依赖 Actions 提供的 `GITHUB_REPOSITORY`、`GITHUB_TOKEN` 和 Secret `BARK_KEY`。

## 状态初始化（部署时执行一次）

需在独立 `monitor-state` 分支创建 `state.json`，内容：

```json
{"version": 1, "part": "MK2P4CH/A", "stores": {}, "failures": 0, "last_error_push": 0}
```

该分支不能设为工作流默认分支；工作流始终使用 `main`。仓库必须允许工作流使用 `contents: write` 来保存监测状态。密钥始终放 Secrets，状态文件不包含密钥。
