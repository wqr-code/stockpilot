# 来源与迭代范围

项目从 [Your.Store](https://github.com/5EBIN/Your.Store) 的 Agent 和运营工具思路开始迭代。此精简版本移除了 Shopify 应用、MCP、旧聊天以及价格建议实现，保留 AGPL-3.0 许可证与来源记录。

StockPilot 当前业务范围是单仓的需求预测、补货需求流转、库存销售统计和只读经营分析 Agent。预测策略参考 [retail-demand-forecasting](https://github.com/hsilvosa/retail-demand-forecasting)，具体改写范围见 ALGORITHM_REFERENCE.md，MIT 原文见 RETAIL_FORECASTING_LICENSE.txt。

商品目录参考 UCI Online Retail，交易与库存为模拟数据；详见 demo/data/README.md。公开结果不得称为真实商家经营业绩或实际提效结果。
