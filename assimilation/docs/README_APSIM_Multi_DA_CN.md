# APSIM + 稀疏真值校正 Sentinel LAI + 多同化方法对比

## 1. 目标

这个脚本用于以下流程：

1. 使用少量 LAI 真值点，校正 Sentinel / Sentinel-2 LAI 时间序列。
2. 把校正后的 LAI 用于同一个 `.apsim` 文件做同化。
3. 对比多种方法（`open_loop`、`enkf`、`4dvar`、`enkf_4dvar`）的 LAI 拟合、产量和生物量结果。

## 2. 关键文件

- `apsim_lai_multi_da_compare.py`：主脚本
- `apsim_multi_da_config_template.json`：配置模板
- `lai_obs_template.csv`：稀疏真值模板（可直接作为 truth_csv）
- `sentinel_lai_template.csv`：Sentinel LAI 模板

## 3. 运行步骤

1. 复制并修改配置文件路径、列名：
   - `template_apsim`
   - `apsim_exe`
   - `workspace`
   - `satellite_correction.truth_csv`
   - `satellite_correction.satellite_csv`
2. 核对 APSIM 输出列名：
   - `report.lai_col`
   - `report.biomass_col`
   - `report.yield_col`
   - 双作物时增加：
     - `report.state_col`（例如 `currentState`）
     - `report.crop_lai_map`（例如 `{"wheat":"wheatlai","maize":"maizelai"}`）
3. 运行：

```bash
python mission/apsim_lai_multi_da_compare.py mission/apsim_multi_da_config_template.json
```

## 4. 输出结果

在 `workspace` 下会生成：

- `corrected_satellite_observations.csv`：校正后的 Sentinel LAI
- `assimilation_observations_used.csv`：实际用于同化的 LAI 序列
- `method_comparison_summary.csv`：方法总对比（LAI RMSE/MAE/Bias，Yield/Biomass）
- `truth_validation_loaded_from_excel.csv`：由 `xlsx` 解析后的真值表
- `truth_validation_biomass_timeseries_and_yield.png`：方法生物量时序 + 最终产量对比图
- 每个方法目录（如 `enkf/`, `4dvar/`, `enkf_4dvar/`）下的明细：
  - `lai_matchups.csv`
  - `biomass_truth_matchups.csv`（若有生物量真值且匹配列成功）
  - `yield_truth_matchups.csv`（若有产量真值且匹配列成功）
  - `assimilation_history.csv`（EnKF）
  - `search_trace.csv` / `objective_evaluations.csv`（4DVar）

## 5. 注意

1. `4dvar` 在这里是“参数优化型 4DVar”（对参数做目标函数最小化），不依赖 APSIM 内部状态 API。
2. `enkf_4dvar` 先跑 EnKF，再用 EnKF 后验均值与方差作为 4DVar 先验。
3. 若 `method_comparison_summary.csv` 中某方法 `status=failed`，请优先检查：
   - APSIM 可执行路径
   - 输出列名
   - 参数 selector 是否能在 `.apsim` 中匹配到数值节点
