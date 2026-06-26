# 文件与目录清单

## 当前项目地图

```text
process_bio/
  docs/                         项目说明、审计文档、迁移说明
  configs/
    assimilation/legacy_configs/ LAI 同化迁移配置
    apsim/ sobol/ weather/ experiments/ 预留配置分区
  data/
    processed/observations/      独立验证观测数据
    processed/soil/              HDSW/HWSD 派生土壤 profile 和报告
    raw/hwsd/                    HWSD2 数据库和栅格
    raw/lai/assimilation_data/   LAI 同化迁移数据
    weather/apsim_met/           APSIM .met 气象输入
    weather/wrf/ era5/ gfs/      WRF/ERA5/GFS 预留入口；实际文件在仓库外
  models/
    apsim_classic/               APSIM Classic 模板
    apsimx_optional/             可选 APSIMX soil node
  scripts/
    soil/                        土壤转换和写入脚本
    search/                      APSIM 搜索、HDSW 水分-产量搜索、本地补充搜索
    sobol/                       Sobol 敏感性分析脚本
    evaluation/                  预测-观测评估脚本
    reports/                     报告生成脚本
    assimilation/legacy_scripts/ LAI 同化迁移脚本
  outputs/
    figures/                     图件输出
    sobol/                       Sobol 工作区、结果、报告
    assimilation/test_results/   LAI 同化测试结果
    apsim_runs/ hdsw/ reports/ weather_qc/ 预留输出分区
  results/
    manifests/results_manifest.json 大结果归档索引
    README.md                    大结果说明
  output*                        兼容旧脚本的大结果 junction，暂时保留
```

## 关键文件位置

| 路径 | 类型 | 说明 |
|---|---|---|
| `models/apsim_classic/modified_from_truth.apsim` | APSIM Classic 模板 | 主模型模板，已从根目录迁入。 |
| `data/weather/apsim_met/p0-1-24-25.met` | APSIM 气象输入 | 用户确认原始来源为实测真实气象数据。 |
| `data/processed/observations/independent_validation_observations_p02_maize_p01_wheat.csv` | 独立验证观测 | 长表格式观测数据，用于产量/生物量/LAI/土壤水分等对比。 |
| `data/raw/hwsd/HWSD2_DB/HWSD2.mdb` | 原始土壤数据 | HWSD2 属性数据库。 |
| `data/raw/hwsd/HWSD2_RASTER/` | 原始土壤数据 | HWSD2 栅格文件。 |
| `data/processed/soil/soil_profile.csv` | 派生土壤 profile | HWSD/HDSW 转换后的表格化土壤参数。 |
| `data/processed/soil/soil_profile.json` | 派生土壤 profile | HWSD/HDSW 转换后的结构化土壤参数。 |
| `data/processed/soil/processing_report.txt` | 土壤处理报告 | HWSD/HDSW 转换报告。 |
| `models/apsimx_optional/optional_apsimx_soil_node.json` | 可选 APSIMX soil node | 仅为可选输出；当前主流程仍是 APSIM Classic `.apsim`。 |
| `outputs/figures/legacy_prediction_observation_figures/` | 图件 | 迁移前已有预测-观测对比图。 |
| `outputs/sobol/` | Sobol 工作区 | 原 `sobol/` 中除脚本外的结果、报告和中间文件。 |
| `configs/assimilation/legacy_configs/` | 配置 | 原 `assimilation/configs/`。 |
| `data/raw/lai/assimilation_data/` | LAI 数据 | 原 `assimilation/data/`。 |
| `outputs/assimilation/test_results/` | 同化结果 | 原 `assimilation/test_results/`。 |
| `docs/assimilation/` | 同化文档 | 原 `assimilation/README.md`、`assimilation/docs/`、`migration_manifest.json`。 |

## WRF/ERA5/GFS 文件状态

用户确认 WRF/ERA5/GFS 文件在仓库外另有存放位置。本仓库当前没有迁入这些文件，也没有发现 WRF -> APSIM `.met` 转换脚本。

当前仓库内与气象相关的已确认输入是：

- `data/weather/apsim_met/p0-1-24-25.met`

该 `.met` 的原始来源已由用户确认为实测真实气象数据。

## 暂时保留在根目录的内容

| 路径 | 原因 |
|---|---|
| `output/` | 兼容旧脚本的大结果 junction。 |
| `output_hdsw/` | 兼容旧脚本的大结果 junction。 |
| `output_hdsw_sobol_water_yield/` | 兼容旧脚本的大结果 junction。 |
| `output_sobol/` | 兼容旧脚本的大结果 junction。 |
| `results/` | 当前仍承担大结果索引功能。 |
| `.claude/`、`.vscode/` | 本地工具/编辑器配置。 |
| `__pycache__/` | Python 缓存，可后续清理；本轮未处理。 |

