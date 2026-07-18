# 齐河县1 km APSIM格网测试及1/10 km尺度比较

## 测试目的与边界

本测试将既有齐河县2020轮作区模拟扩展至1 km，用于检查1 km是否显著改变县域面积加权
单产，以及是否改善相对正式县级统计单产的误差。

1 km、10 km使用相同的2020小麦—玉米轮作掩膜、AgERA5 v2.0逐日气象、HWSD v2土壤、
普通农户管理、品种参数和APSIM Classic 7.10 r4221。测试不修改品种或作物生理参数。
2018和2019仍沿用2020轮作掩膜及统一管理，因此跨年差异主要来自气象和连续状态，不代表
真实逐年种植范围与管理变化。

## 运行记录

- run_id：`qihe_1km_2018_2020_ordinary_farmer_20260716_v1`
- 分析run_id：`qihe_1km_vs_10km_analysis_20260716_v1`
- APSIM独立案例：118；成功/失败：118/0
- APSIM运行时间：517.74 s
- 有效1 km轮作格网：1,507；土壤子单元：2,280
- HWSD土壤单元：14；AgERA5节点：30
- 表示轮作面积：58,918.84 ha

1 km共有1,558个县域相交格网，其中1,507个包含轮作面积。APSIM按“HWSD土壤单元×
AgERA5节点”去重运行118个案例，再按土壤轮作面积映射回全部有效格网。这避免了重复计算，
同时保持格网面积权重。

## 县域平均产量：1 km与10 km

| 年份 | 作物 | 1 km原始(kg/ha) | 10 km原始(kg/ha) | 10 km相对1 km |
|---:|---|---:|---:|---:|
| 2018 | 小麦 | 6,251.84 | 6,248.72 | -0.050% |
| 2018 | 玉米 | 11,229.52 | 11,233.14 | +0.032% |
| 2019 | 小麦 | 6,457.44 | 6,460.26 | +0.044% |
| 2019 | 玉米 | 10,679.45 | 10,692.25 | +0.120% |
| 2020 | 小麦 | 7,095.24 | 7,092.02 | -0.045% |
| 2020 | 玉米 | 12,551.53 | 12,482.41 | -0.551% |

最大尺度差异为2020年玉米的-0.551%。在当前AgERA5和HWSD输入约束下，1 km与10 km
的县域均值基本一致；将格网细化到1 km不会自动提高县域平均产量精度。

## 与正式统计单产比较

校正结果沿用既有2020年5 km固定系数：小麦1.003874、玉米0.604069。没有为1 km或
10 km重新估计系数。2020是系数校准年份；2018—2019才是跨年验证年份。

| 年份 | 作物 | 正式统计 | 1 km原始 | 1 km固定系数后 | 校正后相对偏差 |
|---:|---|---:|---:|---:|---:|
| 2018 | 小麦 | 6,781.20 | 6,251.84 | 6,276.06 | -7.45% |
| 2019 | 小麦 | 6,916.50 | 6,457.44 | 6,482.46 | -6.28% |
| 2020 | 小麦 | 7,128.75 | 7,095.24 | 7,122.72 | -0.08% |
| 2018 | 玉米 | 7,260.60 | 11,229.52 | 6,783.40 | -6.57% |
| 2019 | 玉米 | 7,533.30 | 10,679.45 | 6,451.12 | -14.37% |
| 2020 | 玉米 | 7,569.60 | 12,551.53 | 7,581.99 | +0.16% |

2018—2019验证指标：

| 分辨率 | 作物 | 原始MAPE | 固定系数后MAPE | 校正后RMSE(kg/ha) |
|---:|---|---:|---:|---:|
| 1 km | 小麦 | 7.22% | 6.86% | 470.93 |
| 10 km | 小麦 | 7.22% | 6.86% | 471.32 |
| 1 km | 玉米 | 48.21% | 10.47% | 836.31 |
| 10 km | 玉米 | 48.32% | 10.40% | 830.68 |

1 km没有明显改善县级统计误差。小麦固定系数接近1，改善很小；玉米平均量级明显改善，
但2019仍低估14.37%，说明固定比例校正不能修正错误的年际响应。2020校正后接近正式统计
是系数定义导致的结果；1 km残差不严格为零，是因为系数来自5 km且未在1 km重估。

## 空间异质性与解释限制

2020年1 km格网面积加权CV为小麦5.28%、玉米9.50%；10 km分别为3.70%和9.00%。
1 km显示了更细的高低产斑块，但这些斑块继承14个HWSD单元、30个AgERA5节点及统一管理
的组合差异。AgERA5约0.1°、HWSD属性和县级统一管理并不提供独立的1 km真实气象、管理
或产量观测，因此图件只能称为“模拟空间异质性”，不能称为1 km空间验证。

## 核心输出

分析目录：
`outputs/spatial/county_pilot_2020/grid_resolution_1km_validation/qihe_1km_vs_10km_analysis_20260716_v1/`

- `annual_yield_comparison_1km_10km.csv`：逐年县域单产、正式统计和偏差。
- `official_validation_metrics_1km_10km.csv`：2018—2019跨年验证指标。
- `resolution_10km_vs_1km.csv`：县域均值尺度差异。
- `grid_cell_annual_yields_1km_10km.csv`：逐格网年度原始和校正产量。
- `spatial_variability_1km_10km.csv`：空间均值、标准差、CV和极值。
- `grid_yields_2018_2020.gpkg`：`yield_1km`与`yield_10km`空间图层。
- `figures/qihe_2020_yield_map_1km_vs_10km_raw.*`：2020原始产量地图。
- `figures/qihe_2020_yield_map_1km_vs_10km_corrected.*`：固定系数校正地图。
- `figures/official_vs_apsim_1km_10km_2018_2020.*`：正式统计对照图。
- `analysis_metadata.json`：图件契约、输入哈希、案例数和证据边界。

APSIM运行目录中的`ordinary_farmer/run_metadata.json`记录完整命令、输入路径、SHA-256、
运行时间和成功数。

## 复现命令

```powershell
python scripts/spatial_data/prepare_corrected_baseline_inputs.py --resolution-m 1000 --reuse-existing-weather
python scripts/apsim_inputs/run_corrected_baseline.py --resolution-m 1000 --scenario ordinary_farmer --output-root outputs/spatial/county_pilot_2020/grid_resolution_1km_validation/qihe_1km_2018_2020_ordinary_farmer_20260716_v1
python scripts/apsim_inputs/analyze_qihe_1km_resolution_test.py --one-km-run outputs/spatial/county_pilot_2020/grid_resolution_1km_validation/qihe_1km_2018_2020_ordinary_farmer_20260716_v1 --output outputs/spatial/county_pilot_2020/grid_resolution_1km_validation/qihe_1km_vs_10km_analysis_20260716_v1
```

## 结论

1. 1 km模拟完整成功，但县域平均产量与10 km几乎相同。
2. 1 km主要增加模拟空间异质性的表达，没有改善县级正式统计误差。
3. 2020年5 km固定系数可直接用于1 km和10 km的一致尺度比较，无需分别重估。
4. 当前证据支持10 km用于县域批量试验、1 km用于展示模拟空间格局；不支持宣称1 km产量
   已得到空间验证。
5. 若要证明1 km优于粗分辨率，需要增加乡镇、地块或样方产量，以及更细的逐年管理、土壤
   与气象观测。

