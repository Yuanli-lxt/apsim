# 已完成与未完成内容总结

## 已完成内容

### 1. 数据准备

- 已完成：已有独立观测验证数据。
- 证据路径：`data/processed/observations/independent_validation_observations_p02_maize_p01_wheat.csv`
- 说明：文件包含日期、作物、plot_id、变量名、数值、单位、来源文件和 sheet 等字段，可用于产量、生物量、LAI、土壤水分等观测对比。

### 2. APSIM 模型配置

- 已完成：已有 APSIM Classic 主模板。
- 证据路径：`models/apsim_classic/modified_from_truth.apsim`
- 说明：当前仓库主模型是 APSIM Classic `.apsim`。本轮未发现 `.apsimx` 主模型文件，不能判断为 APSIM Next Gen 项目。

### 3. 气象数据准备

- 已完成：已有 APSIM `.met` 气象输入。
- 证据路径：`data/weather/apsim_met/p0-1-24-25.met`
- 说明：文件头包含 `[weather.met.weather]`，字段包括 `year day radn maxt mint rain`。用户已确认该 `.met` 的原始来源为实测真实气象数据。

### 4. 土壤数据准备

- 已完成：已有 HWSD/HDSW 原始数据、转换脚本和派生 profile。
- 证据路径：
  - `data/raw/hwsd/HWSD2_DB/HWSD2.mdb`
  - `data/raw/hwsd/HWSD2_RASTER/HWSD2.bil`
  - `data/processed/soil/soil_profile.csv`
  - `data/processed/soil/soil_profile.json`
  - `data/processed/soil/processing_report.txt`
  - `scripts/hwsd_to_apsimsoil.py`
  - `scripts/apply_hwsd_to_apsim.py`
- 说明：已有脚本和产物，可确认 HWSD/HDSW 到 APSIM soil profile 的处理已发生。后续仍需保留每次转换的经纬度、字段映射、参数假设和版本记录。

### 5. APSIM 模拟运行

- 已完成：已有多轮 APSIM 迭代运行结果和 best 结果。
- 证据路径：
  - `output/best/metrics.json`
  - `output/best/prediction_vs_truth.csv`
  - `output_hdsw/output/best/metrics.json`
  - `output_sobol/best/metrics.json`
  - `output_hdsw_sobol_water_yield/best/summary_zh.md`
  - `results/results_manifest.json`
- 说明：`results/results_manifest.json` 记录四类大型结果目录，说明项目已经产生大量 APSIM 运行和搜索输出。

### 6. 产量结果分析

- 已完成：已有产量预测、观测对比和误差指标。
- 证据路径：
  - `output/best/metrics.json`
  - `output/best/prediction_vs_truth.csv`
  - `output_sobol/best/metrics.json`
  - `scripts/compare_apsim_prediction_observation.py`
  - `outputs/figures/legacy_prediction_observation_figures/yield_scatter_observed_vs_predicted.png`
  - `outputs/figures/legacy_prediction_observation_figures/yield_sequence_comparison.png`
- 说明：`metrics.json` 中包含 `yield_sim_kg_ha`、`yield_obs_kg_ha`、`yield_error_abs`、`yield_error_rel` 等字段。绘图脚本还引入 `mean_absolute_error`、`mean_squared_error`、`r2_score`。

### 7. 生物量、土壤水分和物候评估

- 已完成：已有生物量、土壤水分和物候误差进入评分。
- 证据路径：
  - `output/best/metrics.json`
  - `output_sobol/best/metrics.json`
  - `outputs/figures/legacy_prediction_observation_figures/biomass_scatter_observed_vs_predicted.png`
  - `outputs/figures/legacy_prediction_observation_figures/biomass_sequence_comparison.png`
  - `outputs/figures/legacy_prediction_observation_figures/soil_water_scatter_observed_vs_predicted.png`
  - `outputs/figures/legacy_prediction_observation_figures/soil_water_sequence_comparison.png`
- 说明：指标文件包含 `total_biomass_error_rel_mean`、`leaf_biomass_error_rel_mean`、`stem_biomass_error_rel_mean`、`phenology_error_days_mean` 等。

### 8. Sobol 敏感性分析

- 已完成：已有 Sobol 参数清单、样本、运行、收集、指数计算、绘图、稳定性比较和 PPT 报告脚本。
- 证据路径：
  - `scripts/sobol/01_inventory_cultivar_parameters.py` 至 `scripts/sobol/12_make_extended_outputs_ppt.py`
  - `outputs/sobol/organized_outputs_screened_N64_20260515_163418/`
  - `outputs/sobol/organized_outputs_screened_N128_20260515_185604/`
  - `outputs/sobol/organized_outputs_screened_N128_extended_outputs_20260518_120007/`
  - `outputs/sobol/ppt_report_sobol_20260517_utf8/`
  - `outputs/sobol/ppt_report_sobol_20260517_5slides_utf8/`
- 说明：已有较完整的 Sobol 分析链条，但路径配置仍比较本地化。

### 9. LAI 同化迁移

- 已完成：已有 LAI 同化脚本、配置、数据和测试结果迁移。
- 证据路径：
  - `docs/assimilation/README.md`
  - `scripts/assimilation/legacy_scripts/apsim_lai_multi_da_compare.py`
  - `configs/assimilation/legacy_configs/`
  - `data/raw/lai/assimilation_data/lai_mission/`
  - `outputs/assimilation/test_results/smoke_summary/method_comparison_summary.csv`
  - `outputs/assimilation/test_results/key_runs/*/yield_truth_matchups.csv`
- 说明：该部分已有结果，但 README 明确提醒配置保留旧路径，不能直接视作完全可复现。

## 未完成内容

### 1. 完整 pipeline 入口

- 未完成：未发现统一的一键流程入口。
- 证据路径：本轮检查未发现 `run_pipeline.py`、`Snakefile`、`Makefile`、`pyproject.toml` 或统一实验配置目录。
- 说明：当前流程由多个脚本和历史结果组成，复现需要知道具体运行顺序和参数。

### 2. 路径参数化

- 未完成：多个脚本仍存在绝对路径或旧目录依赖。
- 证据路径：
  - `sobol/scripts/sobol_common.py`
  - `sobol/scripts/05_run_apsim_batch.py`
  - `assimilation/scripts/legacy_enkf/lai_data_mission.py`
  - `assimilation/README.md`
- 说明：这会影响迁移、重跑和多人协作。

### 3. 气象来源可追溯性

- 未完成：已有 `.met` 文件，但未发现生成该 `.met` 的脚本或原始气象来源记录。
- 证据路径：`data/weather/apsim_met/p0-1-24-25.met`
- 说明：用户已确认该 `.met` 来源为实测真实气象数据；仍建议补充原始观测文件位置、站点元数据和转换记录。

### 4. WRF 接入

- 未完成：未发现 WRF 到 APSIM 的本地打通证据。
- 证据路径：用户确认 WRF/ERA5/GFS 文件在仓库外另有存放位置；仓库内未发现 `wrfout_d0*`、`met_em*`、ERA5/GFS 处理、WRF 提取脚本、WRF 转 `.met` 脚本。
- 说明：仓库内 WRF-APSIM 接入仍应作为未来开发内容，而不是已完成内容。

### 5. APSIM Next Gen `.apsimx`

- 未完成/未确认：未发现主 `.apsimx` 模型。
- 证据路径：仅发现 `models/apsimx_optional/optional_apsimx_soil_node.json`，未发现 `.apsimx` 主模型。
- 说明：当前项目应按 APSIM Classic `.apsim` 管理。

## 不能确认的内容

- 已由用户确认 `data/weather/apsim_met/p0-1-24-25.met` 的原始来源是实测真实气象数据；仍需补充原始观测文件和转换记录。
- 不能确认所有 `output*` 结果都能在当前机器上原样重跑，因为 APSIM 安装路径、上级 `processing` 模块和旧路径依赖未完全归档。
- 不能确认 LAI 同化迁移后的全部配置能直接运行，因为 `assimilation/README.md` 明确要求重跑前检查路径。
- 不能确认 WRF 模拟已完成或 WPS 已完成，因为没有发现对应文件和日志。
