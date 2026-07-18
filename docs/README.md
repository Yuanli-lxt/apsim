# 文档导航与状态说明

本目录采用“根导航 + 研究线 + 专题阶段”的分层方式。历史文件保留原内容并移动到`archive/`，
当前研究文档按用途归入子目录。

```text
docs/
├─ qihe/
│  ├─ baseline/       输入、校准与冻结基线
│  ├─ validation/     跨年、尺度和真值对照
│  ├─ management/     施肥、预热与初始氮
│  └─ plans/          当前研究计划
├─ project/           项目总览、脚本和文件名索引
├─ methods/           数据同化与WRF—APSIM未来方法
├─ sceua/             SCE-UA独立研究线
├─ assimilation/      LAI同化现有文档
└─ archive/           阶段快照、迁移记录和暂停计划
```

## 齐河县 APSIM 当前主线

建议按以下顺序阅读：

1. `qihe/baseline/county_grid_apsim_workflow.md`：2020县域格网数据、轮作掩膜和输入来源。
2. `qihe/baseline/qihe_2020_absolute_yield_calibration.md`：2020绝对产量差异及固定系数来源。
3. `qihe/baseline/qihe_2020_corrected_baseline.md`：AgERA5、HWSD面积拆分和普通农户管理基线。
4. `qihe/validation/qihe_2018_2020_crossyear_10km_validation.md`：跨年验证及5/10 km比较。
5. `qihe/validation/qihe_1km_resolution_test.md`：最新1 km实跑、1/10 km和正式统计对照。
6. `qihe/plans/qihe_fixed_factor_multiyear_plan.md`：固定系数多年验证计划。
7. `qihe/management/qihe_multiyear_fertilizer_management.md`：化肥统计折纯与分配假设。
8. `qihe/management/qihe_warmup_initial_n_sensitivity.md`：连续预热和初始NO3/NH4敏感性。

当前主线不优化品种参数、不修改作物生理参数。固定县域系数只能修正比例量级，不能修正
年际响应；1 km结果属于模拟空间异质性，尚未完成乡镇或地块尺度空间验证。

## 项目与方法索引

- `project/project_overview.md`：项目总体背景；具体数值以专题文档为准。
- `project/script_inventory.md`：脚本功能清单。
- `project/文件名对照表.md`：脚本英文文件名与中文用途对照。
- `archive/project_snapshots/`：历史文件清单、完成情况、推荐结构和迁移记录。

## 其他研究线与未来计划

- `sceua/`：山东公开数据SCE-UA研究线。
- `methods/data_assimilation_yield_scaling_roadmap.md`：数据同化与面状产量优化路线。
- `methods/wrf_apsim_integration_plan.md`：WRF—APSIM未来耦合方案。
- `archive/paused_plans/qihe_maize_cultivar_optimization_plan.md`：暂停的品种优化计划。

## 文档维护规则

1. 新实验单独建立专题文档，不覆盖旧文档中的历史数值。
2. 写明输入年份、分辨率、管理情景、气象、土壤、系数来源及验证身份。
3. 不为每个分辨率重估2020固定系数。
4. 没有乡镇、地块或样方产量时，格网图统一称为“模拟空间异质性”。
5. 输出以run_id隔离；CSV、JSON、GPKG、图件和脚本共同构成可复现证据包。
