# LAI 同化迁移索引

本目录从 `E:\A_汇报\毕设过渡\代码汇总\mission` 筛选迁移而来，用于保存之前做 LAI 同化时有复用价值的脚本、配置、源数据和关键测试结果。

源目录未删除，也没有整包搬运缓存和大量 APSIM 中间运行目录。

## 目录结构

| 目录 | 内容 |
|---|---|
| `scripts/` | 多方法 LAI 同化主脚本，以及早期 EnKF/VIIRS 处理脚本。 |
| `configs/` | 原始配置 JSON 和 legacy EnKF 配置模板。 |
| `data/lai_mission/` | LAI 真值、VIIRS LAI、校准后 LAI、配对数据和观测模板。 |
| `data/templates/` | Sentinel/LAI 输入模板。 |
| `data/truth_validation/` | 小麦/玉米生育时期真值 Excel。 |
| `docs/` | 原始中文说明文档。 |
| `test_results/smoke_summary/` | smoke 测试的顶层汇总、诊断、校正后观测和图。 |
| `test_results/key_runs/` | 各方法的 matchup/history/search trace 和关键 final/open-loop APSIM 状态。 |
| `test_results/local_probe/` | 本地 simulation window probe 的 `truth.apsim`。 |

## 已保留的关键内容

- `scripts/apsim_lai_multi_da_compare.py`
- `scripts/legacy_enkf/*.py`
- `configs/*.json`
- `data/lai_mission/LAI真值.xls`
- `data/lai_mission/LAI_VIIRS_VNP15A2H_Point_2024_2025.csv`
- `data/lai_mission/lai_viirs_calibrated_for_assim_*.csv`
- `test_results/smoke_summary/method_comparison_summary.csv`
- `test_results/smoke_summary/truth_validation_biomass_timeseries_and_yield.png`
- `test_results/key_runs/*/lai_matchups.csv`
- `test_results/key_runs/*/biomass_truth_matchups.csv`
- `test_results/key_runs/*/yield_truth_matchups.csv`
- `test_results/key_runs/*/final_*` 或 `open_loop_run/`

## 未迁移的内容

以下内容被认为是可再生成或低价值中间产物，因此没有迁移：

- `__pycache__/`
- EnKF ensemble step 目录，例如 `step_000/`, `step_001/`
- 4DVar 搜索中间评估目录，例如 `var_evals/`
- 多个随机编号的重复 APSIM run 目录
- 空的 `runlog_multi_da.txt`

## 运行提示

迁移后的配置文件仍保留原始路径，用于追溯当时运行环境。若要在当前项目中重新运行，需要检查并修改：

- `template_apsim`
- `apsim_exe`
- `workspace`
- `satellite_correction.truth_csv`
- `satellite_correction.satellite_csv`
- legacy EnKF 配置中的 `observations_csv`

本次迁移清单见 `migration_manifest.json`。

