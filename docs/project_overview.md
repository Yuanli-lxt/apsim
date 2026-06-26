# APSIM 产量预测项目总览

## 项目目标

基于本地文件证据，当前 `process_bio` 项目主要围绕 APSIM Classic 7.10 作物模拟开展以下工作：

- 使用 APSIM Classic `.apsim` 模板进行小麦/玉米轮作模拟。
- 使用独立观测数据对产量、生物量、土壤水分、物候等指标进行对比评价。
- 测试 HWSD/HDSW 派生土壤参数写入 APSIM 后的模拟效果。
- 进行 Sobol 敏感性分析和本地补充搜索。
- 迁移并保留一条 LAI 同化实验线，用于 Sentinel/VIIRS LAI 与 APSIM 的多方法同化比较。

证据路径：

- `PROJECT_STRUCTURE.md`
- `models/apsim_classic/modified_from_truth.apsim`
- `data/weather/apsim_met/p0-1-24-25.met`
- `data/processed/observations/independent_validation_observations_p02_maize_p01_wheat.csv`
- `scripts/run_process_bio_search.py`
- `scripts/hwsd_to_apsimsoil.py`
- `scripts/apply_hwsd_to_apsim.py`
- `scripts/compare_apsim_prediction_observation.py`
- `scripts/sobol/`
- `docs/assimilation/README.md`

## 当前整体状态判断

当前项目不是只有数据整理阶段。根据 `results/README.md`、`results/results_manifest.json`、`output*/best/metrics.json`、`output*/best/prediction_vs_truth.csv`、`figures/*.png` 等文件，可以确认项目已经有 APSIM 运行结果、预测-观测对比结果、评价指标和图件。

更准确的状态是：

- APSIM 主流程状态：已经可以批量/迭代运行 APSIM Classic，并产生预测-观测对比结果。
- 产量预测结果：已有产量、生物量、土壤水分、物候等比较结果。
- 模型评估结果：已有自定义评分、相对误差、产量误差、物候误差等指标；也有独立绘图脚本使用 MAE/RMSE/R2。
- 完整可复现流程：尚未完全闭环。主要原因是部分脚本仍依赖旧路径、绝对路径、junction 兼容入口、APSIM 安装目录和未纳入 Git 的大型本地结果。
- WRF 与 APSIM 打通状态：未发现本地证据表明已经打通。

## APSIM 产量预测主流程

从本地文件可以整理出当前 APSIM 主流程：

```text
models/apsim_classic/modified_from_truth.apsim
        +
data/weather/apsim_met/p0-1-24-25.met
        +
data/processed/observations/independent_validation_observations_p02_maize_p01_wheat.csv
        |
        v
scripts/run_process_bio_search.py
        |
        v
output*/iter_xxx/ 和 output*/best/
        |
        v
metrics.json / prediction_vs_truth.csv / summary_zh.md / stage_alignment.csv
        |
        v
scripts/compare_apsim_prediction_observation.py
        |
        v
outputs/figures/legacy_prediction_observation_figures/*.png
```

相关证据：

- 主模板：`models/apsim_classic/modified_from_truth.apsim`
- 气象输入：`data/weather/apsim_met/p0-1-24-25.met`，用户确认来源为实测真实气象数据
- 独立观测：`data/processed/observations/independent_validation_observations_p02_maize_p01_wheat.csv`
- 主搜索脚本：`scripts/run_process_bio_search.py`
- 早期搜索结果：`output/best/metrics.json`
- HDSW 单独验证结果：`output_hdsw/output/best/metrics.json`
- Sobol 引导搜索结果：`output_sobol/best/metrics.json`
- 图件：`outputs/figures/legacy_prediction_observation_figures/yield_scatter_observed_vs_predicted.png` 等

## HDSW/HWSD 土壤流程

本地文件显示，项目已经具备 HWSD/HDSW 土壤数据到 APSIM 土壤参数的转换和写入流程。

```text
data/raw/hwsd/HWSD2_RASTER/
data/raw/hwsd/HWSD2_DB/
        |
        v
scripts/hwsd_to_apsimsoil.py
        |
        v
data/processed/soil/soil_profile.csv
data/processed/soil/soil_profile.json
models/apsimx_optional/optional_apsimx_soil_node.json
data/processed/soil/processing_report.txt
        |
        v
scripts/apply_hwsd_to_apsim.py
        |
        v
output_hdsw/output/best/truth.apsim
```

说明：

- `models/apsimx_optional/optional_apsimx_soil_node.json` 存在，但当前主模型文件是 APSIM Classic `.apsim`，不是 Next Gen `.apsimx`。
- 本地未发现 `.apsimx` 主模型文件。

## Sobol 敏感性分析流程

`scripts/sobol/` 下已有较完整的 Sobol 工作流：

```text
01_inventory_cultivar_parameters.py
        |
02_build_parameter_ranges.py
        |
03_generate_sobol_samples.py
        |
04_generate_apsim_runs.py
        |
05_run_apsim_batch.py
        |
06_collect_outputs.py
        |
07_calculate_sobol_indices.py
        |
08_plot_sobol_results.py
        |
09/10/11/12 诊断、稳定性比较、输出变量检查和报告生成
```

证据路径：

- `scripts/sobol/sobol_common.py`
- `outputs/sobol/organized_outputs_screened_N64_20260515_163418/`
- `outputs/sobol/organized_outputs_screened_N128_20260515_185604/`
- `outputs/sobol/organized_outputs_screened_N128_extended_outputs_20260518_120007/`
- `outputs/sobol/apsim_output_variable_inventory.md`
- `outputs/sobol/ppt_report_sobol_20260517_utf8/`
- `outputs/sobol/ppt_report_sobol_20260517_5slides_utf8/`

## LAI 同化流程

LAI 同化内容已从原 `assimilation/` 拆分迁移到 `scripts/assimilation/legacy_scripts/`、`configs/assimilation/legacy_configs/`、`data/raw/lai/assimilation_data/`、`docs/assimilation/` 和 `outputs/assimilation/test_results/`。

证据路径：

- `docs/assimilation/README.md`
- `scripts/assimilation/legacy_scripts/apsim_lai_multi_da_compare.py`
- `scripts/assimilation/legacy_scripts/legacy_enkf/`
- `configs/assimilation/legacy_configs/`
- `data/raw/lai/assimilation_data/lai_mission/`
- `outputs/assimilation/test_results/smoke_summary/`
- `outputs/assimilation/test_results/key_runs/`

状态判断：

- 已有脚本和结果。
- 迁移说明明确提示配置中仍保留原始路径，重跑前需要检查 `template_apsim`、`apsim_exe`、`workspace`、观测数据路径等。
- 因此该流程属于“已有结果，但当前仓库内可复现性需要修补”。

## WRF 气象模拟未来接入位置

本地仓库当前只确认有 APSIM `.met` 气象输入文件：

- `data/weather/apsim_met/p0-1-24-25.met`

用户确认 WRF/ERA5/GFS 文件在仓库外另有存放位置；本仓库未发现以下 WRF 相关本地证据：

- `wrfout_d0*`
- `met_em*`
- ERA5/GFS 原始或中间文件
- WPS/WRF 运行日志
- WRF 输出变量提取脚本
- WRF 转 APSIM `.met` 脚本
- APSIM 使用 WRF 生成 `.met` 运行的示例结果

因此 WRF 当前只能写作未来接入方向，不能写作已完成流程。建议接入位置是替换或补充当前实测气象 `.met` 驱动：

```text
WRF wrfout
  -> 提取站点/区域逐日气象变量
  -> 单位转换、日尺度聚合、质量检查
  -> 生成 APSIM .met
  -> APSIM Classic 运行
  -> 与独立观测产量/生物量/土壤水分对比
```
