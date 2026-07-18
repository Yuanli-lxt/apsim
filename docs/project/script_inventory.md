# 脚本功能清单

## 迁移后脚本位置

本项目已经按推荐结构把脚本移动到分类目录：

- `scripts/search/run_process_bio_search.py`
- `scripts/search/diagnose_hdsw_water_yield_matrix.py`
- `scripts/search/local_search_output_sobol_iw_crit.py`
- `scripts/search/local_two_stage_output_sobol.py`
- `scripts/search/local_fraction_fine_output_sobol.py`
- `scripts/soil/hwsd_to_apsimsoil.py`
- `scripts/soil/apply_hwsd_to_apsim.py`
- `scripts/evaluation/compare_apsim_prediction_observation.py`
- `scripts/reports/create_apsim_water_content_report.py`
- `scripts/sobol/*.py`
- `scripts/assimilation/legacy_scripts/**/*.py`

下面的表格保留原审计信息；路径已迁移的脚本以后以上述新位置为准。

## 主流程脚本

| 脚本路径 | 主要功能 | 输入 | 输出 | 是否可复跑 | 依赖文件 | 当前风险 |
|---|---|---|---|---|---|---|
| `scripts/run_process_bio_search.py` | bio-first 单因子/迭代搜索；支持 HDSW water-yield search；生成 APSIM 运行、指标和 best 目录 | `modified_from_truth.apsim`、`independent_validation_observations_p02_maize_p01_wheat.csv`、`output_hdsw/output/best/truth.apsim` 等 | `output_sobol/` 或 `output_hdsw_water_yield/` 下的 `iter_*`、`best/metrics.json`、`prediction_vs_truth.csv`、`summary_zh.md` | 部分可复跑 | `../processing/run_joint_single_factor_rounds.py`、APSIM Classic 环境、本地输出目录 | 依赖上级 `processing` 模块；输出目录与现有 `results/` junction 命名不完全一致；逻辑很大，缺少拆分；存在对旧结果目录的依赖。 |
| `scripts/hwsd_to_apsimsoil.py` | 将 HWSD v2.0 栅格和属性库转换为 APSIM Soil/profile 可用输出 | HWSD raster、HWSD DB、经纬度、crop、field map 等 | `soil_profile.csv`、`soil_profile.json`、`optional_apsimx_soil_node.json`、`processing_report.txt` | 可复跑 | `rasterio`、`pyproj`、`shapely`、`pandas`、可选 `pyodbc/geopandas` | 需要 GIS/数据库依赖；MDB 读取依赖本地驱动；字段映射需要人工确认。 |
| `scripts/apply_hwsd_to_apsim.py` | 将土壤 profile 写入 APSIM Classic `.apsim` 文件 | `--apsim-in`、`--soil-csv`、`--outdir` | 修改后的 `.apsim`、对比/manifest 类输出 | 可复跑 | `hdsw/soil_profile.csv`、输入 `.apsim` | 只针对特定 APSIM XML 结构；写入后需要 APSIM 小样本运行验证。 |
| `scripts/compare_apsim_prediction_observation.py` | 对比 APSIM 输出和独立观测，计算指标并绘图 | 观测 CSV、APSIM `.out`，脚本内默认指向 `output/iter_981/.../Rotation Sample Phases.out` | `figures/*.png`、控制台指标 | 部分可复跑 | `sklearn`、`matplotlib`、`pandas` | 配置在脚本顶部，默认预测文件指向固定迭代；不是命令行参数化；中文变量名在当前终端显示存在编码错显风险。 |
| `scripts/diagnose_hdsw_water_yield_matrix.py` | 诊断 HDSW 水分-产量矩阵结果 | `output_hdsw_sobol_water_yield` 或相关输出 | 诊断报告、矩阵摘要 | 部分可复跑 | 已有 HDSW 搜索输出 | 偏结果诊断；依赖既有输出结构。 |
| `scripts/local_search_output_sobol_iw_crit.py` | 围绕 `output_sobol/best` 搜索 InitialWater 和 `crit_fr_asw` | `output_sobol/best` | 本地 grid 搜索目录和指标 | 部分可复跑 | `scripts/run_process_bio_search.py` 相关函数/结构、APSIM 输出 | 明确锁定 weather/fertilizer/sowing density 等；依赖当前 best 状态。 |
| `scripts/local_two_stage_output_sobol.py` | 对 `output_sobol` best 做两阶段确定性本地搜索 | `output_sobol/best`、候选参数 | `two_stage_local_*`、`best/metrics.json` | 部分可复跑 | 既有 Sobol best 和主搜索评估逻辑 | 默认目标和约束写在脚本中；适合补充搜索，不是通用 pipeline。 |
| `scripts/local_fraction_fine_output_sobol.py` | 对 FractionFull 做细网格搜索 | `output_sobol/best` | `fraction_fine_*` 结果 | 部分可复跑 | 既有 Sobol best | 不修改土壤/灌溉/天气等；依赖特定 best。 |
| `scripts/create_apsim_water_content_report.py` | 生成 APSIM 土壤含水量预测改进 PPT 汇报 | 本地结果表/图件 | PPTX 报告 | 部分可复跑 | `python-pptx`、本地结果 | 报告脚本，输入路径需进一步核对。 |

## Sobol 脚本

| 脚本路径 | 主要功能 | 输入 | 输出 | 是否可复跑 | 依赖文件 | 当前风险 |
|---|---|---|---|---|---|---|
| `sobol/scripts/sobol_common.py` | Sobol 公共路径、常量、APSIM Classic XML/输出读取工具 | 环境变量 `SOBOL_BASE_APSIM`、`SOBOL_OUTPUT_DIR`，默认本地路径 | 被其他脚本引用 | 部分可复跑 | `modified_from_truth.apsim`、`F:\APSIM710-r4221\Model` | 存在硬编码绝对路径；迁移前需参数化。 |
| `sobol/scripts/01_inventory_cultivar_parameters.py` | 扫描 `.apsim` 和 APSIM Model crop XML 中 cultivar 参数 | `BASE_APSIM`、`MODEL_DIR` | cultivar 参数清单 CSV | 部分可复跑 | APSIM Model 目录 | 依赖 APSIM 安装目录中的 `Wheat.xml`/`Maize.xml`。 |
| `sobol/scripts/02_build_parameter_ranges.py` | 构建 Sobol 参数范围 | 参数清单 | `parameter_ranges.csv` | 可复跑 | 01 输出 | 参数范围规则需人工审核。 |
| `sobol/scripts/03_generate_sobol_samples.py` | 生成 Sobol 样本 | 参数范围、`--N` | wide/long 样本 CSV | 可复跑 | SALib/参数范围 | N 和二阶交互影响运行量。 |
| `sobol/scripts/04_generate_apsim_runs.py` | 根据样本生成 APSIM 运行文件 | Sobol 样本、`BASE_APSIM` | sample `.apsim`、simulation index、参数 trace | 部分可复跑 | APSIM Classic XML 结构 | 写模型文件前需备份；依赖输出模块文件名修改逻辑。 |
| `sobol/scripts/05_run_apsim_batch.py` | 顺序批量运行 APSIM Classic | simulation index、APSIM exe | APSIM 输出、更新 sim index | 部分可复跑 | `F:\APSIM710-r4221\Model\Apsim.exe` | APSIM 可执行文件路径硬编码；脚本提示不要并行运行。 |
| `sobol/scripts/06_collect_outputs.py` | 收集 APSIM 输出并汇总标准作物变量 | APSIM `.out/.csv/.txt` | `sobol_model_outputs.csv`、available columns、mapping/missing report | 部分可复跑 | Sobol run outputs | 输出变量别名需要随 `.apsim` report 变化维护。 |
| `sobol/scripts/07_calculate_sobol_indices.py` | 计算 Sobol 指数 | `sobol_model_outputs.csv` | `sobol_indices_summary.csv`、missing report | 可复跑 | SALib、模型输出 | 目标变量缺失时会跳过或记录 missing。 |
| `sobol/scripts/08_plot_sobol_results.py` | 绘制 Sobol 结果图 | Sobol index summary | PNG/PDF 图 | 可复跑 | `matplotlib`、Sobol 结果 | 图件依赖输入表完整性。 |
| `sobol/scripts/09_diagnose_n64_results.py` | 诊断 N=64 Sobol 结果并生成论文表格 | N64 final_results | publication tables、诊断文本 | 可复跑 | N64 final results | 默认 final dir 是绝对路径。 |
| `sobol/scripts/10_compare_n64_n128_stability.py` | 比较 N64/N128 稳定性 | N64/N128 final_results | 稳定性图和 CSV | 可复跑 | 两套 Sobol 结果 | N128 路径需命令行提供；N64 默认路径硬编码。 |
| `sobol/scripts/11_search_apsim_output_variables.py` | 扫描 APSIM output/report 变量并提出候选扩展变量 | `.apsim` | inventory/search/recommended CSV/MD | 可复跑 | `lxml`、APSIM XML | 脚本明确建议不自动写入不确定变量，需要 GUI/小样本确认。 |
| `sobol/scripts/12_make_extended_outputs_ppt.py` | 基于扩展输出结果生成 PPT | extended final results、图件 | PPTX、QA、speaker notes | 部分可复跑 | `python-pptx`、扩展 Sobol 输出 | 报告路径固定到现有 extended 输出。 |
| `sobol/scripts/create_cn_sobol_ppt_utf8.py` | 生成中文 Sobol PPT | Sobol final results | PPTX、QA、notes、assets | 部分可复跑 | `python-pptx`、图表数据 | 报告脚本，路径固定在脚本中。 |
| `sobol/scripts/create_cn_sobol_ppt_5slides_utf8.py` | 生成 5 页中文 Sobol PPT | Sobol final results | 5 页 PPTX、QA、notes | 部分可复跑 | `python-pptx`、图表数据 | 报告脚本，路径固定在脚本中。 |

## LAI 同化脚本

| 脚本路径 | 主要功能 | 输入 | 输出 | 是否可复跑 | 依赖文件 | 当前风险 |
|---|---|---|---|---|---|---|
| `assimilation/scripts/apsim_lai_multi_da_compare.py` | 多方法 LAI 同化比较，包含 open-loop、EnKF、4DVar/hybrid 等；可输出 LAI、生物量、产量 matchups | JSON config | `method_comparison_summary.csv`、`lai_matchups.csv`、`yield_truth_matchups.csv`、图件等 | 部分可复跑 | `assimilation/configs/*.json`、APSIM exe、模板 `.apsim`、LAI/真值数据 | `assimilation/README.md` 明确配置保留旧路径，重跑前需要修正。 |
| `assimilation/scripts/legacy_enkf/apsim_enkf_lai.py` | APSIM Classic + EnKF LAI 同化 | JSON config | history、posterior mean APSIM run | 部分可复跑 | APSIM exe、模板 `.apsim`、观测 CSV | legacy 脚本；配置路径需检查。 |
| `assimilation/scripts/legacy_enkf/apsim_enkf_lai_fixed_v3.py` | EnKF LAI 同化修订版本 | JSON config | history、posterior mean APSIM run | 部分可复跑 | 同上 | legacy 脚本，需确认与主同化脚本差异。 |
| `assimilation/scripts/legacy_enkf/lai_data_mission.py` | 早期 LAI 数据/EnKF 任务脚本 | 硬编码 CONFIG | APSIM ensemble 输出和 LAI 更新文件 | 不建议直接复跑 | `F:\APSIM710-r4221\Model\Apsim.exe`、`F:\APSIM710-r4221\yuan\rotation_fuben.apsim` | 绝对路径硬编码明显；旧 mission 结构依赖强。 |
| `assimilation/scripts/legacy_enkf/prepare_lai_from_viirs_truth.py` | 用稀疏真值校准 VIIRS LAI 并导出同化输入 | VIIRS CSV、truth xls、参数 | `lai_viirs_calibrated_for_assim_*.csv/json` | 部分可复跑 | `assimilation/data/lai_mission/` | 默认路径仍带 `mission/...`，需按迁移后目录修正。 |

## 本轮未发现的脚本类型

- 未发现 WRF 输出 `wrfout_d0*` 提取脚本。
- 未发现 ERA5/GFS 下载或边界数据预处理脚本。
- 未发现 WPS `met_em*` 到 WRF 的完整流程脚本。
- 未发现 WRF 气象变量生成 APSIM `.met` 的脚本。
- 未发现 APSIM 使用 WRF `.met` 运行的专门示例脚本。

## 脚本层面的共性风险

- 绝对路径：`sobol/scripts/sobol_common.py`、`sobol/scripts/05_run_apsim_batch.py`、`assimilation/scripts/legacy_enkf/lai_data_mission.py` 等存在 `F:\APSIM710-r4221\...` 硬编码。
- 旧目录兼容：`output*` 现在是 junction，旧脚本仍可能依赖这些入口。
- 配置分散：主流程、Sobol、LAI 同化各自维护路径和常量。
- 可复现边界不清：大型结果不纳入 Git，只保留索引；这有利于控制仓库体量，但需要更完整的 run manifest。
- 编码风险：部分中文文档或终端输出在当前 PowerShell 中出现错显，建议统一 UTF-8。
- 缺少统一入口：尚未看到 `run_pipeline.py`、`Makefile`、`Snakefile`、`pyproject.toml` 或一键复现实验配置。

## 2026-07-16 齐河1 km扩展脚本

| 脚本路径 | 主要功能 | 输入 | 输出 | 可复现性说明 |
|---|---|---|---|---|
| `scripts/spatial_data/prepare_corrected_baseline_inputs.py` | 新增1 km HWSD面积拆分，并可复用冻结的AgERA5文件 | 1 km轮作格网、10 m轮作掩膜、HWSD、AgERA5 `.met` | `corrected_baseline_units_1km.csv`及面积检查 | `--reuse-existing-weather`避免重写已有气象输入 |
| `scripts/apsim_inputs/run_corrected_baseline.py` | 支持1 km及自定义新输出根目录，写入run metadata和SHA-256 | 1 km单元、普通农户管理、APSIM模板 | 案例输出、汇总、`run_metadata.json` | 使用`--output-root`隔离run_id，不覆盖5/10 km结果 |
| `scripts/apsim_inputs/analyze_qihe_1km_resolution_test.py` | 提取2018—2020收获结果，比较1/10 km并对照正式统计 | 1 km新运行、10 km冻结运行、官方统计、5 km固定系数 | CSV、GPKG、SVG/PDF/PNG/TIFF、metadata | 不为1/10 km重新拟合系数 |
