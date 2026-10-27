# iPhone Duo Monitor Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在无常开设备的情况下监测杭州、上海十家 Apple Store，并通过 Bark 提醒。

**Architecture:** Python 标准库请求已验证的 Apple pickup-message 接口，合并两地门店并严格识别库存；GitHub Actions 调度，独立分支保存状态，Bark Secret 接收通知。

**Tech Stack:** Python 3.12, unittest, GitHub Actions, Bark HTTPS API.

**Spec:** ../specs/2026-10-27-iphone-duo-design.md

## Global Constraints
- MK2P4CH/A，杭州 R471/R532；上海 R390/R401/R359/R389/R678/R683/R581/R705。
- 无密钥不监测；无完整有效响应不声称无货；默认调度关闭直到配置完成。
- 不自动下单，不输出推送密钥，不读取用户其他项目数据。

## Review Focus
- 门店缺失或未知状态：报告错误并保留旧状态。
- API 返回 availableNow=true 但商品不可取货：不误报补货。
- 部分城市失败：仍能提醒另一城市的真实有货门店。
- Bark 失败：下一轮再次尝试，不能吞掉库存通知。
- GitHub 状态读取失败：停止推送，避免误当首次运行反复提醒。

## Task 1: 库存解析与提醒状态机
**Files:** monitor.py, test_monitor.py, config.json
**Interfaces:** parse_stores(payload, part) -> dict; advance(state, observations, errors, notify, now) -> dict.
- [ ] 编写补货、持续有货、再次补货、门店非目标、未知结构、部分失败、推送失败重试测试，运行并观察失败。
- [ ] 实现严格解析与状态推进，测试全部通过。
- [ ] 用真实杭州、上海响应验证配置覆盖十店。

## Task 2: 云端运行与配置
**Files:** monitor.py, .github/workflows/monitor.yml, README.md, .gitignore
**Interfaces:** CLI --check/--test-push；GitHubState.load/save；Bark.send。
- [ ] 添加 HTTP 错误、Bark 状态、GitHub 状态不可读与保存的离线测试。
- [ ] 实现 HTTP 集成、独立分支状态、摘要和无密钥失败行为。
- [ ] 加入五分钟调度、手动查询与测试推送入口、MONITOR_ENABLED 变量开关。
- [ ] 运行完整测试与真实查询，自查仓库密钥与工作流权限。
- [ ] 创建独立仓库并上传，执行云端 check；推送配置不足时明确未开启。
