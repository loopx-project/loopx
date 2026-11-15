# Task Group: 扩展架构与界面设计

scope: 只读扩展的数据契约、权限边界和界面体验。

## Task 1: 通过宿主读取已有数据

### rollout_summary_files

- rollout_summaries/workbench.md

### learnings

- 数据源负责真实状态，界面只展示当前快照。
- 接口权限在服务端校验，停用扩展后撤销读取。
- 设计需要同时验证亮色、深色以及手机布局。

## Task 2: 保留信息来源

文档更新时间不代表每条记录的生成时间。不同项目可能共用同一个记忆来源。
