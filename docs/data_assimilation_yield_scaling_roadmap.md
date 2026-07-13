# APSIM 数据积累、LAI 同化与面状产量预测优化路线

## 0. 仓库现状判断

本仓库当前已经具备点位 APSIM Classic 模型、实测气象 `.met`、独立观测验证、Sobol/搜索结果、LAI 同化迁移脚本和 WRF 接入规划。需要避免把尚未入库的外部数据流程写成已完成。

已确认基础：

- APSIM 主模型：`models/apsim_classic/modified_from_truth.apsim`。
- 当前气象驱动：`data/weather/apsim_met/p0-1-24-25.met`，字段为日尺度 `year/day/radn/maxt/mint/rain`，该 `.met` 已由用户确认为实测真实气象数据来源。
- 点位观测：`data/processed/observations/independent_validation_observations_p02_maize_p01_wheat.csv`，当前主要覆盖 P01 小麦、P02 玉米，含 LAI、生物量、土壤水分、产量等。
- LAI 同化：`scripts/assimilation/legacy_scripts/apsim_lai_multi_da_compare.py` 已有稀疏真值校正卫星 LAI、open loop、EnKF、4DVar、EnKF+4DVar 对比骨架，但配置仍保留旧路径和 VIIRS 数据名。
- 产量评估：`scripts/evaluation/compare_apsim_prediction_observation.py` 当前按点位/作物/日期把 APSIM 输出与观测合并评估，不是区域面预测。
- WRF/ERA5/GFS：用户确认文件在仓库外；仓库内没有 `wrfout_d0*`、`met_em*`、WRF 转 APSIM `.met` 脚本或 WRF 驱动产量验证结果。

## 1. 数据积累怎么做

建议把数据积累从“文件越来越多”改成“每条样本都能追溯、能重跑、能用于点到面扩展”。仓库应新增统一数据台账和最小元数据规范。

### 1.1 建议目录

```text
data/
  raw/
    field_observations/
    sentinel2/
    weather_station/
    wrf/
    era5/
    gfs/
    management/
    boundaries/
  processed/
    observations/
    satellite_lai/
    weather_daily/
    weather_grids/
    apsim_met/
    yield_labels/
    feature_cube/
  metadata/
    data_inventory.csv
    sites.csv
    plots.csv
    seasons.csv
    variable_dictionary.csv
```

### 1.2 每条样本必须保留的字段

点位观测表建议统一为长表：

```text
sample_id, site_id, plot_id, crop, season, date, variable_name, value, unit,
lon, lat, geometry_id, source_file, source_sheet, method, qc_flag, version
```

区域/栅格样本建议统一为：

```text
grid_id, geometry_id, crop, season, date, variable_name, value, unit,
resolution_m, crs, source_product, processing_level, qc_flag, version
```

关键原则：

- 产量标签要区分 `实测样方产量`、`地块收割产量`、`统计年鉴/乡镇产量`、`APSIM 模拟产量`。
- 气象要区分 `station_observed`、`wrf_nearest`、`wrf_bilinear`、`wrf_area_mean`、`era5`、`gfs_forecast`。
- Sentinel-2 LAI 要保留云掩膜、有效像元比例、地块内像元数、聚合方法和观测误差。
- 每次 APSIM 运行要保存 `.apsim`、作物 XML、`.met`、土壤参数、管理措施、输出和指标。

### 1.3 最小可实施清单

1. 建 `data/metadata/data_inventory.csv`，记录所有外部数据路径，不把大文件强行搬进仓库。
2. 建 `sites.csv / plots.csv / seasons.csv`，把 P01/P02 的经纬度、作物、季节、播收日期、管理措施补齐。
3. 把现有 `independent_validation_observations_p02_maize_p01_wheat.csv` 映射到标准长表，并保留原始来源字段。
4. 为每个 APSIM 结果目录补 `run_metadata.json`，记录模型、气象、土壤、管理、参数来源。
5. 新增 `scripts/data/build_data_inventory.py` 和 `scripts/data/validate_observation_schema.py`，先做台账和字段检查。

## 2. Sentinel-2 LAI 同化的时空尺度一致

Sentinel-2 MSI 是高分辨率多光谱数据，常用 L2A 表面反射率，空间分辨率按波段为 10 m、20 m、60 m，双星名义重访约 5 天。SNAP Biophysical Processor 可由 Sentinel-2 或 Landsat 8 反射率推导 LAI、FAPAR、FCOVER、Cab、CW；Sentinel-2 生物物理处理器使用 20 m 波段组合生成 LAI 等变量。

因此建议项目采用：

```text
Sentinel-2 L2A BOA reflectance
  -> 云/阴影/雪/异常像元掩膜
  -> SNAP Biophysical Processor 或 SL2P 类 LAI 反演
  -> 20 m LAI 栅格
  -> 地块/网格聚合
  -> 日尺度观测表
  -> APSIM LAI 同化
```

### 2.1 空间尺度统一

APSIM 当前是点位/plot 模型，Sentinel-2 LAI 是像元/地块尺度。不要直接拿一个像元塞进同化，应先定义同化支持尺度。

推荐三种模式：

| 模式 | 用途 | 做法 |
|---|---|---|
| plot 模式 | 延续 P01/P02 点位验证 | 用 plot 边界或点缓冲区提取 Sentinel-2 LAI，中位数/均值作为该 plot 当天观测 |
| grid 模式 | 面状产量预测 | 建 20 m 或 100 m 网格，每个网格有一条 LAI 时间序列和一套 APSIM 输入 |
| field 模式 | 地块级汇报 | 用地块边界聚合 LAI，再运行一个代表性 APSIM 或聚合多个网格 APSIM |

第一阶段建议：优先做 plot 模式，因为能直接接现有 P01/P02 真值；第二阶段再做 grid/field。

### 2.2 时间尺度统一

APSIM 输出是日尺度，Sentinel-2 是过境日观测，受云影响不连续。建议不要把 Sentinel-2 硬插值成每天同化，而是在“有可靠观测的日期”同化：

1. Sentinel-2 过境日生成一条 LAI 观测。
2. 对同一天多景或多像元聚合为一条 `date, crop, plot_id/grid_id, lai, std`。
3. APSIM 每日输出 LAI；同化脚本在观测日期读取同日 APSIM LAI。
4. 对真值校准 Sentinel-2 时，`join_tolerance_days` 可先设 3-5 天；作物快速生长期建议减到 2-3 天。
5. 缺测不补同化，只在建深度学习特征时可用时序插值、平滑或 mask。

### 2.3 观测误差 std 怎么设

现有脚本支持 `default_std` 和卫星 `std` 列。建议 Sentinel-2 LAI 处理后写出：

```text
date, crop, plot_id/grid_id, lai, lai_std, valid_pixel_count,
valid_pixel_fraction, cloud_fraction, source_product, aggregation
```

`lai_std` 初始可由三部分构成：

```text
lai_std = max(0.15, within_polygon_lai_sd, calibration_residual_std)
```

云量高、有效像元少、LAI 过饱和时期，增大 `lai_std`，让 EnKF/4DVar 少相信这条观测。

### 2.4 需要改造的仓库接口

当前 `apsim_lai_multi_da_compare.py` 的 `select_obs_for_assimilation()` 返回列只有 `date/lai/std/source`，会丢掉 `crop` 和 `plot_id/grid_id`。建议后续改成保留：

```text
date, crop, plot_id, grid_id, lai, std, source
```

并让 `extract_lai_vector()` 按 `crop` 和空间单元匹配 APSIM 输出。短期可只保留 `crop`，因为脚本已有 crop 到 `wheatlai/maizelai` 的映射逻辑。

## 3. 从点上产量预测到面上产量预测

当前产量预测仍是点位模型：一个 `.apsim` + 一个 `.met` + 一组土壤/管理参数，对 P01/P02 点位观测做验证。要做“面上”，本质是让 APSIM 或混合模型在很多空间单元上批量运行并形成地图。

### 3.1 推荐路线：APSIM 网格化 + 遥感校正

```text
研究区边界/地块边界
  -> 生成 grid_id 或 field_id
  -> 每个单元匹配土壤、气象、作物、管理、Sentinel-2 LAI
  -> 批量生成 APSIM 输入
  -> 批量运行 APSIM
  -> 输出 grid_id/field_id 产量
  -> 与样方/收割/统计产量校正
  -> 输出 GeoTIFF/矢量产量图
```

第一版空间单元建议不要直接 20 m 全覆盖跑 APSIM，计算量会很大。更稳妥：

- 先用 100 m 或地块单元跑 APSIM。
- Sentinel-2 LAI 保持 20 m，先聚合到 100 m/地块。
- 土壤用 HWSD/HDSW 或后续更高分辨率土壤栅格按单元提取。
- 气象先用同一个站点或 WRF 网格插值，后续再替换为 WRF 空间场。

### 3.2 面预测最小工程版本

新增：

```text
configs/spatial_yield/grid_yield_run.example.json
scripts/spatial/build_prediction_grid.py
scripts/spatial/extract_spatial_drivers.py
scripts/spatial/generate_apsim_grid_runs.py
scripts/spatial/collect_grid_yield.py
scripts/spatial/export_yield_map.py
```

核心中间表：

```text
grid_id, lon, lat, geometry_wkt, crop, season,
soil_profile_id, weather_source_id, met_file,
management_id, lai_timeseries_id, apsim_file, yield_kg_ha
```

输出：

```text
outputs/spatial_yield/<run_id>/
  grid_inputs.csv
  grid_yield_predictions.csv
  yield_map.tif
  yield_map.geojson
  metrics_by_validation_source.csv
  run_metadata.json
```

### 3.3 校正/验证策略

点位验证不足以证明面预测可靠。建议至少准备三类标签：

- 样方收获点：用于细尺度误差。
- 地块收割产量：用于地块均值校正。
- 区县/乡镇统计产量：用于区域总量约束，但不能直接当像元真值。

评价指标分层：

- 点位：RMSE、MAE、Bias、R2、相对误差。
- 地块：地块均值误差、排序相关。
- 区域：总产误差、空间分布合理性。

## 4. 深度学习产量预测怎么接入

深度学习不应替代当前 APSIM，而应先作为“APSIM + 遥感 + 气象”的校正/融合层。原因是当前真实产量标签很少，直接端到端深度学习容易过拟合。

### 4.1 可调研/可实现模型路线

| 模型 | 输入 | 优点 | 风险 | 本项目建议 |
|---|---|---|---|---|
| RandomForest/XGBoost | 季节统计特征 | 小样本稳、解释性强 | 时序信息弱 | 第一基线 |
| 1D-CNN/LSTM/GRU | LAI/NDVI/气象时间序列 | 能学季节动态 | 标签少会过拟合 | 第二阶段 |
| CNN-RNN/ConvLSTM | 遥感影像块 + 时间 | 同时学空间和时间 | 数据量和算力要求高 | 有足够地块标签后再做 |
| Transformer/Temporal Fusion Transformer | 多源时序 | 表达能力强、可加 mask | 更吃数据 | 第三阶段 |
| Physics-guided ML | APSIM 输出 + DL 残差 | 和现有仓库最匹配 | 需要设计特征 | 推荐主线 |

### 4.2 推荐主线：APSIM 残差校正

先让 APSIM 产生每个 plot/grid 的过程变量，再用机器学习预测残差：

```text
yield_true = APSIM_yield + ML_residual(Sentinel2_LAI, WRF/weather, soil, management, APSIM_states)
```

特征建议：

- Sentinel-2：LAI 最大值、峰值日期、积分 LAI、拔节/抽穗/灌浆窗口 LAI、云缺测比例。
- 气象：积温、降水累计、极端高温天数、低温天数、辐射累计、关键期水分胁迫指标。
- 土壤：质地、容重、LL/DUL/SAT、SWCON、KL、初始水分。
- APSIM：模拟产量、生物量峰值、LAI 峰值、物候日期、土壤水分误差指标、水分胁迫天数。
- 管理：播期、灌溉、施肥、品种参数。

### 4.3 最小可运行实验

新增：

```text
scripts/ml/build_yield_feature_table.py
scripts/ml/train_yield_baselines.py
scripts/ml/train_yield_sequence_model.py
configs/ml/yield_baseline.example.json
outputs/ml_yield/<run_id>/
```

第一轮不要直接上复杂 Transformer，先做：

1. naive baseline：历史均值/作物均值。
2. APSIM-only baseline：只用 APSIM yield。
3. XGBoost/RandomForest：APSIM + LAI + 气象 + 土壤统计特征。
4. LSTM/TCN：仅在样本数量扩展后做。

## 5. WRF 气象精度和过程文件怎么学习

WRF 的“精度”不能用一个固定数字回答。WRF 是可配置的中尺度数值天气模式，官方说明其应用尺度可从几十米到数千公里；具体空间分辨率由 `namelist.input` 中 `dx/dy` 和嵌套 domain 决定，时间步长通常也要随网格尺度调整。因此本项目应把 WRF 精度拆成三层：

1. 模式网格精度：domain 的 `dx/dy`、投影、嵌套关系、输出间隔。
2. 气象变量精度：与站点实测/当前 `.met` 对比后的 `maxt/mint/rain/radn` 误差。
3. 作物响应精度：WRF 驱动 APSIM 后对产量、生物量、土壤水分、物候的误差。

### 5.1 需要向外部 WRF 目录收集的过程文件

```text
WPS/
  namelist.wps
  geogrid.log
  ungrib.log
  metgrid.log
  geo_em.d0*.nc
  met_em.d0*.nc

WRF/
  namelist.input
  rsl.out.0000
  rsl.error.0000
  wrfinput_d0*.nc
  wrfbdy_d01
  wrfout_d0*_<date>

Boundary/
  ERA5/GFS source files
  download scripts or request metadata

Postprocess/
  variable extraction scripts
  station comparison tables
  WRF-to-APSIM .met files
```

### 5.2 WRF 转 APSIM 的变量和验证

APSIM 当前 `.met` 只要求：

```text
radn, maxt, mint, rain
```

WRF 侧第一版建议提取：

- `T2` -> 日最高/最低温，K 转 deg C。
- `RAINC + RAINNC` -> 累积降水，差分并按本地日累计为 mm/day。
- 短波辐射变量 -> 日总辐射，转换为 MJ/m2/day；变量名需按实际 WRF 输出确认。
- 时间坐标 -> 本地日期、year、day-of-year。

验证顺序：

1. 检查 WRF 文件变量、时间覆盖、经纬度/投影。
2. 生成站点最近格点和双线性插值两版 `.met`。
3. 与当前实测 `.met` 对比日尺度 `maxt/mint/rain/radn`。
4. 用同一 APSIM 模型分别跑实测 `.met`、WRF 最近格点 `.met`、WRF 插值 `.met`。
5. 输出 weather-driver 对比报告。

### 5.3 “WRF 精度是多少”的建议回答口径

目前仓库不能直接回答 WRF 精度是多少，因为 WRF 文件和验证结果不在仓库内。正确口径应为：

- WRF 空间分辨率要从 `namelist.input` 的 `dx/dy` 和 domain 设置读取。
- WRF 气象精度要用站点实测或当前 `.met` 做变量级验证。
- WRF 对产量预测的有效精度要通过 APSIM 产量/生物量/土壤水分/物候误差评估。

第一版报告可以给出：

```text
domain_id, dx_m, dy_m, output_interval, station_match_method,
maxt_rmse, mint_rmse, rain_mae, rain_bias, radn_rmse,
yield_error_rel, biomass_error_rel, soil_water_error, phenology_error_days
```

## 6. 推荐实施顺序

1. 数据台账和元数据先行：先把现有点位、气象、APSIM 结果、外部 WRF/Sentinel-2 路径登记清楚。
2. Sentinel-2 LAI plot 模式：把旧 VIIRS 输入替换成 Sentinel-2 LAI 表，保持 P01/P02 点位验证。
3. WRF 最小链条：从外部 `wrfout` 生成一个站点 `.met`，与当前实测 `.met` 和 APSIM 输出对比。
4. 面状产量最小版：用 100 m 或地块单元批量生成 APSIM 输入和产量表。
5. 机器学习基线：先做 APSIM 残差校正，不急于端到端深度学习。
6. 深度学习扩展：当区域样本和多年标签积累后，再做 LSTM/TCN/Transformer 或 ConvLSTM。

## 7. 外部参考

- Copernicus Data Space Sentinel-2 documentation: https://documentation.dataspace.copernicus.eu/Data/Sentinel2.html
- ESA SNAP Biophysical Processor overview: https://step.esa.int/main/wp-content/help/versions/13.0.0/snap-toolboxes/eu.esa.opt.opttbx.biophysical/BiophysicalOpOverview.html
- ESA SNAP Sentinel-2 Biophysical Processor description: https://step.esa.int/main/wp-content/help/versions/9.0.0/snap-toolboxes/org.esa.s2tbx.s2tbx.biophysical/BiophysicalOpProcessorDescription.html
- UCAR WRF model overview: https://www.mmm.ucar.edu/models/wrf
- WRF Users Guide chapter 5: https://www2.mmm.ucar.edu/wrf/users/docs/user_guide_v4/v4.4/users_guide_chap5.html
- CNN-RNN crop yield prediction example: https://pmc.ncbi.nlm.nih.gov/articles/PMC6993602/
