# process_bio 项目结构

本项目用于 APSIM Classic 作物产量模拟/预测、HDSW/HWSD 土壤参数测试、Sobol 敏感性分析和 LAI 同化实验整理。

## 当前顶层目录

| 路径 | 作用 |
|---|---|
| `docs/` | 项目审计、结构说明、脚本清单、WRF-APSIM 未来接入方案。 |
| `configs/` | 后续统一配置目录；当前包含迁移后的 LAI 同化配置。 |
| `data/` | 小体量输入和处理后数据；气象 `.met`、观测 CSV、HDSW/HWSD 土壤数据已迁入。 |
| `models/` | APSIM Classic 模板和可选 APSIMX soil node。 |
| `scripts/` | 整理后的脚本入口，按 soil/search/sobol/evaluation/reports/assimilation 分类。 |
| `src/` | 预留的可复用 Python 包目录。 |
| `outputs/` | 图件、Sobol 工作区、同化测试结果等输出归档。 |
| `results/` | 大型 APSIM 搜索结果的索引和归档入口；`results/manifests/` 保存 manifest。 |
| `output*` | 兼容旧脚本的大结果 junction，暂时保留，不建议删除。 |

## 关键文件位置

| 路径 | 作用 |
|---|---|
| `models/apsim_classic/modified_from_truth.apsim` | 当前脚本使用的主要 APSIM Classic 模板文件。 |
| `data/weather/apsim_met/p0-1-24-25.met` | APSIM 气象输入文件；用户确认原始来源为实测真实气象数据。 |
| `data/processed/observations/independent_validation_observations_p02_maize_p01_wheat.csv` | 独立验证观测数据。 |
| `data/raw/hwsd/` | HDSW/HWSD 原始土壤数据库和栅格文件。 |
| `data/processed/soil/` | HDSW/HWSD 派生土壤 profile 和处理报告。 |
| `models/apsimx_optional/optional_apsimx_soil_node.json` | 可选 APSIMX soil node 输出；当前主流程仍为 APSIM Classic。 |
| `scripts/search/run_process_bio_search.py` | 主搜索脚本，用于 APSIM 参数调优和 HDSW 水分-产量搜索模式。 |
| `scripts/soil/hwsd_to_apsimsoil.py` | 将 HWSD/HDSW 来源数据转换为 APSIM 可用 soil/profile 输出。 |
| `scripts/soil/apply_hwsd_to_apsim.py` | 将生成的土壤参数写入 APSIM 文件。 |
| `scripts/evaluation/compare_apsim_prediction_observation.py` | 对比 APSIM 预测值和验证观测值，并输出图件。 |
| `scripts/sobol/` | Sobol 敏感性分析脚本。 |
| `scripts/assimilation/legacy_scripts/` | 迁移后的 LAI 同化脚本。 |
| `outputs/figures/legacy_prediction_observation_figures/` | 迁移前已有的预测-观测对比图件。 |
| `outputs/sobol/` | 迁移后的 Sobol 工作区、结果和报告。 |
| `outputs/assimilation/test_results/` | 迁移后的 LAI 同化测试结果。 |

## WRF/ERA5/GFS 状态

用户确认 WRF/ERA5/GFS 文件在仓库外另有存放位置。本仓库当前只保留 APSIM 可直接使用的实测气象 `.met` 文件；WRF 到 APSIM `.met` 的转换脚本和运行结果尚未在仓库内建立。

## 结果目录说明

`output`、`output_hdsw`、`output_hdsw_sobol_water_yield`、`output_sobol` 仍作为兼容入口保留。它们对应的大型结果索引见：

- `results/README.md`
- `results/manifests/results_manifest.json`

在所有旧脚本完成路径参数化前，不建议删除这些 junction。
