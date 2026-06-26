# 推荐项目结构与整理方案

## 推荐目录结构

结合当前项目实际，建议后续逐步整理为：

```text
process_bio/
  README.md
  PROJECT_STRUCTURE.md
  docs/
    project_overview.md
    file_inventory.md
    script_inventory.md
    completed_work_summary.md
    wrf_apsim_integration_plan.md
    recommended_project_structure.md
  configs/
    apsim/
    sobol/
    assimilation/
    weather/
      wrf_to_apsim_met.example.json
    experiments/
  data/
    raw/
      observations/
      hwsd/
      lai/
      weather/
    processed/
      observations/
      soil/
      lai/
      weather/
    weather/
      apsim_met/
      wrf/
      era5/
      gfs/
    yield/
    soil/
    management/
  models/
    apsim_classic/
      modified_from_truth.apsim
    apsimx_optional/
  scripts/
    data_prepare/
    soil/
    weather/
    apsim_run/
    search/
    sobol/
    assimilation/
    evaluation/
    visualization/
    reports/
  src/
    apsim_runner/
    weather_processing/
    soil_processing/
    evaluation/
  outputs/
    apsim_runs/
    hdsw/
    sobol/
    assimilation/
    figures/
    reports/
  results/
    manifests/
  notebooks/
  tests/
```

说明：该结构已在本轮执行了主要迁移。`output*` junction 和 `results/` 仍保留在根目录，用于兼容旧脚本和大型结果索引。

## 当前文件到推荐目录的映射建议

| 当前路径 | 推荐位置 | 迁移条件 |
|---|---|---|
| `modified_from_truth.apsim` | `models/apsim_classic/modified_from_truth.apsim` | 已迁移。 |
| `p0-1-24-25.met` | `data/weather/apsim_met/p0-1-24-25.met` | 已迁移；用户确认来源为实测真实气象数据。 |
| `independent_validation_observations_p02_maize_p01_wheat.csv` | `data/processed/observations/` | 已迁移。 |
| `hdsw/HWSD2_DB/` | `data/raw/hwsd/HWSD2_DB/` | 已迁移。 |
| `hdsw/HWSD2_RASTER/` | `data/raw/hwsd/HWSD2_RASTER/` | 已迁移。 |
| `hdsw/soil_profile.csv` | `data/processed/soil/soil_profile.csv` | 已迁移。 |
| `hdsw/soil_profile.json` | `data/processed/soil/soil_profile.json` | 已迁移。 |
| `hdsw/optional_apsimx_soil_node.json` | `models/apsimx_optional/` | 已迁移。 |
| `scripts/hwsd_to_apsimsoil.py` | `scripts/soil/hwsd_to_apsimsoil.py` | 已迁移并修正 `PROJECT_ROOT`。 |
| `scripts/apply_hwsd_to_apsim.py` | `scripts/soil/apply_hwsd_to_apsim.py` | 已迁移。 |
| `scripts/run_process_bio_search.py` | `scripts/search/run_process_bio_search.py` | 已迁移并修正默认模型/观测路径。 |
| `scripts/compare_apsim_prediction_observation.py` | `scripts/evaluation/compare_apsim_prediction_observation.py` | 已迁移并修正默认观测/图件路径。 |
| `scripts/create_apsim_water_content_report.py` | `scripts/reports/` | 已迁移并修正报告输出路径。 |
| `scripts/local_*_output_sobol.py` | `scripts/search/` | 已迁移并修正观测数据路径。 |
| `sobol/scripts/*.py` | `scripts/sobol/` | 已迁移；主要默认路径已指向 `outputs/sobol/`。 |
| `sobol/organized_outputs*/` | `outputs/sobol/` | 已迁移。 |
| `sobol/ppt_report*/` | `outputs/sobol/` | 已迁移，仍保留在 Sobol 工作区下。 |
| `assimilation/scripts/` | `scripts/assimilation/legacy_scripts/` | 已迁移。 |
| `assimilation/configs/` | `configs/assimilation/legacy_configs/` | 已迁移。 |
| `assimilation/data/` | `data/raw/lai/assimilation_data/` | 已迁移。 |
| `assimilation/test_results/` | `outputs/assimilation/test_results/` | 已迁移。 |
| `figures/` | `outputs/figures/legacy_prediction_observation_figures/` | 已迁移。 |
| `output*` | `outputs/apsim_runs/` 或 `outputs/search/` | 旧脚本不再依赖 junction 后迁移。 |
| `results/` | `outputs/archive/` 或保留为 `results/` | 当前已承担归档索引功能，可暂时保留。 |

## 迁移风险

- `output*` 是 junction，直接删除或移动会影响旧脚本。
- `sobol/scripts/sobol_common.py` 包含绝对路径和默认输出目录。
- `sobol/scripts/05_run_apsim_batch.py` 默认使用 `F:\APSIM710-r4221\Model\Apsim.exe`。
- `assimilation/README.md` 明确提示配置保留原始路径，重跑前必须检查。
- `scripts/compare_apsim_prediction_observation.py` 默认预测文件固定到 `output/iter_981/...`。
- 大型结果没有全部进入 Git，移动前必须先生成 manifest，避免丢失可追溯性。
- 当前中文文档/终端输出存在编码错显现象，迁移时应统一 UTF-8。

## 分阶段整理方案

### 阶段 1：只补文档和索引

本轮已经完成：

- 新增 `docs/project_overview.md`
- 新增 `docs/file_inventory.md`
- 新增 `docs/script_inventory.md`
- 新增 `docs/completed_work_summary.md`
- 新增 `docs/wrf_apsim_integration_plan.md`
- 新增 `docs/recommended_project_structure.md`

继续建议：

- 给每个大结果目录补 `manifest.json`。
- 给每次 best 结果补运行命令、APSIM 版本、输入模型、输入气象、观测数据、脚本 commit/mtime。

### 阶段 2：参数化路径

优先处理：

- `scripts/compare_apsim_prediction_observation.py`
- `sobol/scripts/sobol_common.py`
- `sobol/scripts/05_run_apsim_batch.py`
- `assimilation/configs/*.json`
- `assimilation/scripts/legacy_enkf/lai_data_mission.py`

目标：

- 所有输入输出路径从 CLI 或 JSON/YAML 配置读取。
- APSIM exe 路径统一由环境变量或配置文件管理。
- 不再依赖当前机器的绝对路径。

### 阶段 3：建立稳定 pipeline

建议新增：

```text
configs/experiments/baseline_apsim_classic.json
configs/experiments/hdsw_validation.json
configs/experiments/sobol_n64.json
configs/experiments/sobol_n128.json
scripts/apsim_run/run_experiment.py
scripts/evaluation/evaluate_experiment.py
```

每个实验配置记录：

- APSIM 模板
- 气象 `.met`
- 土壤输入
- 观测数据
- 输出目录
- 评分指标
- 是否运行 APSIM
- 是否只做评估

### 阶段 4：再迁移目录

在路径参数化和 pipeline 可运行后，再逐步迁移：

1. 先移动文档和配置。
2. 再移动脚本。
3. 再移动小体量输入数据。
4. 最后处理大型输出目录和 junction。

每一步都应保留迁移 manifest。

### 阶段 5：接入 WRF

WRF 接入不应混入现有搜索脚本里直接实现。建议单独建立：

```text
configs/weather/
scripts/weather/
data/weather/wrf/
data/weather/apsim_met/
outputs/weather_qc/
```

先实现：

1. 单站点 WRF -> 日尺度气象表。
2. 日尺度气象表 -> APSIM `.met`。
3. `.met` 质量检查。
4. APSIM 小样本运行。
5. 与当前 `data/weather/apsim_met/p0-1-24-25.met` 实测气象驱动结果对比。

## 本轮仍保留的边界

- 仍不删除 `output*` junction。
- 仍不移动 `results/` 归档目录本体。
- 仍不修改 APSIM 模板内容。
- 仍不把 WRF 流程写成已完成；用户确认 WRF/ERA5/GFS 文件位于仓库外。
