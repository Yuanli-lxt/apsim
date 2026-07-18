# 项目整理迁移记录

> 归档说明：本文记录一次历史迁移操作；当前文档结构见`../../README.md`。

## 本轮执行内容

- 删除 `skill_know.md`。
- 将 APSIM Classic 模板迁移到 `models/apsim_classic/modified_from_truth.apsim`。
- 将实测气象 `.met` 迁移到 `data/weather/apsim_met/p0-1-24-25.met`。
- 将独立观测数据迁移到 `data/processed/observations/`。
- 将 HDSW/HWSD 原始数据迁移到 `data/raw/hwsd/`。
- 将 HDSW/HWSD 派生土壤 profile 和报告迁移到 `data/processed/soil/`。
- 将可选 APSIMX soil node 迁移到 `models/apsimx_optional/`。
- 将原 `scripts/*.py` 按功能迁移到 `scripts/soil/`、`scripts/search/`、`scripts/evaluation/`、`scripts/reports/`。
- 将原 `sobol/scripts/*.py` 迁移到 `scripts/sobol/`，将原 `sobol/` 中的结果和报告迁移到 `outputs/sobol/`。
- 将原 `assimilation/` 拆分迁移到 `scripts/assimilation/legacy_scripts/`、`configs/assimilation/legacy_configs/`、`data/raw/lai/assimilation_data/`、`docs/assimilation/`、`outputs/assimilation/test_results/`。
- 将原 `figures/` 迁移到 `outputs/figures/legacy_prediction_observation_figures/`。
- 将 `results/results_manifest.json` 迁移到 `results/manifests/results_manifest.json`。
- 更新 `.gitignore` 以匹配整理后的脚本、文档和配置结构。

## 用户确认信息

- `data/weather/apsim_met/p0-1-24-25.met` 的原始气象来源为实测真实气象数据。
- WRF/ERA5/GFS 文件在仓库外另有存放位置。

## 保留项

- `output/`、`output_hdsw/`、`output_hdsw_sobol_water_yield/`、`output_sobol/` 仍作为旧脚本兼容 junction 保留。
- `results/` 仍作为大型结果归档和索引目录保留。
- APSIM 模板内容未修改。
