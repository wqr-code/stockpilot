# StockPilot · 电商需求预测与补货决策助手

单仓场景的本地演示应用：从销售与库存数据出发，完成需求预测、补货建议、需求单、生产反馈和入库登记，并提供经营分析与可选的 AI 解读。

**在线体验：** [https://stockpilot-n6qf.onrender.com](https://stockpilot-n6qf.onrender.com)

在线演示使用 Render 免费实例。长时间无人访问后，首次打开可能需要约 50 秒唤醒。

## 本地运行

下文的 `127.0.0.1:3211` 是启动程序后在自己电脑上访问的地址。GitHub 只托管源码；上方在线演示由 Render 单独部署。

建议 Python 3.12。在项目目录执行（Windows）：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m demo.web
```

macOS / Linux 使用 `.venv/bin/python` 替代上述 Python 路径。浏览器打开 http://127.0.0.1:3211 ，在“数据与设置”加载示例或导入 CSV，再设置生产提前期与目标服务水平。

首次安装依赖需要联网；预测计算在本地运行，首次计算可能需要等待。

## 可选：启用 AI 解读

没有模型 API 也能使用数据看板、预测计算和补货单据。AI 解读需要部署者自行配置兼容 OpenAI Chat Completions 的模型服务：

| 使用方式 | API 来源 | 调用额度与费用 |
| --- | --- | --- |
| 访问已部署的公开网站 | 使用该网站部署者在服务器中配置的 API；访客无需填写密钥 | 由网站部署者承担 |
| 自行部署或本地运行 | 使用者配置自己的 API | 由使用者承担 |

公开网站未配置模型 API 时，预测、补货和经营看板仍可正常使用，只有 AI 综合解读不可用。模型密钥仅保存在部署环境或本地 `.env.demo` 中，不会发送到浏览器。

1. 复制 `.env.demo.example` 为 `.env.demo`。
2. 在本地文件填写自己的 `DEMO_API_KEY`、`DEMO_BASE_URL`、`DEMO_MODEL`。
3. 重启服务。

项目不附带作者的密钥，也不连接作者的模型代理。模型费用由你配置的 API 账户承担。启用后进入经营分析会自动生成解读；切换数据或统计范围可能产生新请求，相同版本和范围复用缓存。相关销售与库存摘要会发送到你配置的服务。

不要提交 `.env.demo`。如果将应用部署到服务器并向别人开放，用户会使用服务器配置的模型账户；在线模式提供基础账号隔离，但模型费用仍由部署者承担，因此应控制公开演示的调用额度。

## 部署为在线应用

仓库包含 `render.yaml`，可在 Render 使用 Blueprint 连接本仓库部署。该配置会创建一个 FastAPI Web Service 和 PostgreSQL 数据库，并开启账号登录；不同账号的上传数据和业务单据分别保存。

1. 在 Render 新建 Blueprint，选择本 GitHub 仓库并应用 `render.yaml`。
2. 等待数据库和 Web Service 构建完成，打开 Render 提供的 `onrender.com` 地址。
3. 如需 AI 解读，在 Web Service 的 Environment 中配置 `DEMO_API_KEY`、`DEMO_BASE_URL`、`DEMO_MODEL`。

部署保持单进程运行，因为当前预测队列和内存工作区由一个进程协调。免费服务会休眠，免费 PostgreSQL 也有期限限制，适合项目展示；长期开放时应升级数据库并为 AI 调用增加总额度。模型密钥只放在服务端环境变量中。

## 数据与流程

- 默认示例：16 个商品、730 天连续销售和库存历史，包含不同备货状态。商品目录有公开资料来源，经营流水为模拟数据，详见 `demo/data/README.md`。
- 数据文件独立位于 `demo/data/`，生成逻辑位于 `demo/standard_sample.py`。
- 用户导入数据及操作保存到 `demo/data/workspace.local.json`，不随源码包分发。
- 需求单先生成草稿，再确认需求、录入生产反馈、登记入库；不自动对接生产系统。
- 预测比较与安全库存算法见 `ALGORITHM_REFERENCE.md`；这不是产能排程或完整 ERP。
- TheLook 探索数据不包含在发布包中，也不是默认示例。

## 开发与验证

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
.venv\Scripts\python scripts/package_release.py
```

源码包生成于 `dist/stockpilot-source.zip`。打包脚本使用文件白名单，排除模型密钥、本地工作区、日志和探索数据，并检查常见密钥格式。发布前仍应检查包内容及 Git 提交历史。

主要入口：`demo/web.py`（后端）、`demo/planning_workspace.py`（备货流程）、`demo/upload.js`（页面）、`demo/model_client.py`（模型接入）、`demo/analytics_agent.py`（分析 Agent）。流程、业务持久化与记忆边界见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 来源与许可

基于 [5EBIN/Your.Store](https://github.com/5EBIN/Your.Store) 学习与迭代，保留 AGPL-3.0 许可证 `LICENSE`。此版本已移除 Shopify、MCP 和旧聊天模块，来源及改写范围见 [ATTRIBUTION.md](ATTRIBUTION.md)。算法参考及其 MIT 许可见 `ALGORITHM_REFERENCE.md` 和 `RETAIL_FORECASTING_LICENSE.txt`。再分发时请保留相关许可和来源。
