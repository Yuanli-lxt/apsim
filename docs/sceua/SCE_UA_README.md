# 山东公开数据 SCE-UA 区域单产模型

## 1. 目录用途

本目录保存山东省小麦、玉米公开数据 SCE-UA 区域单产模型的训练、验证和测试结果。

当前模型使用山东省统计年鉴市级单产与 NASA POWER 逐日气象数据，分别建立冬小麦和玉米模型。它是用于验证公开数据和 SCE-UA 优化流程的**区域统计单产模型**，不是 APSIM 品种参数自动校准结果。

数据按年份拆分：

| 数据集 | 年份 | 每种作物样本数 | 用途 |
|---|---:|---:|---|
| 训练集 | 2018–2022 | 80 | SCE-UA目标函数计算和参数优化 |
| 验证集 | 2023 | 16 | 检查训练后模型的时间外推表现 |
| 测试集 | 2024 | 16 | 最终独立时间验证 |

小麦和玉米分别训练，不共享参数。

## 2. 运行入口

主脚本：

```text
scripts/calibration/public_sceua_yield_model.py
```

在项目根目录运行：

```powershell
python scripts/calibration/public_sceua_yield_model.py
```

脚本会重新读取公开输入数据，分别运行小麦、玉米 SCE-UA 优化，并覆盖本目录下的结果文件和验证图。

## 3. 输入文件

### 3.1 山东统计年鉴市级农作物数据

目录：

```text
data/raw/shandong_public/statistics/yearbook_city_crops/
```

主要文件：

```text
shandong_yearbook_2019_13-10.xls
shandong_yearbook_2020_13-10-0.xls
shandong_yearbook_2021_13-09.xls
shandong_yearbook_2022_13-09.xls
shandong_yearbook_2023_13-09.xls
shandong_yearbook_2024_13-09.xls
shandong_yearbook_2025_13-09.xls
```

用途：提供2018–2024年山东各市小麦、玉米的：

- 播种面积，单位 `ha`；
- 总产量，单位 `t`；
- 单产，单位 `kg/ha`。

脚本将莱芜市历史数据按播种面积和总产量合并到济南市，以统一到当前山东16市空间口径。

### 3.2 NASA POWER逐日气象

目录结构：

```text
data/raw/shandong_public/weather/nasa_power/<year>/
```

使用年份为2018–2024年，每年包括：

| 文件 | 变量 | 模型用途 |
|---|---|---|
| `T2M_MAX.csv` | 日最高气温，°C | 计算季节平均最高温和高温日数 |
| `T2M_MIN.csv` | 日最低气温，°C | 与最高温计算日平均温度 |
| `PRECTOTCORR.csv` | 校正降水，mm/day | 计算作物季累计降水 |
| `ALLSKY_SFC_SW_DWN.csv` | 地表太阳辐射，MJ/m²/day | 计算作物季平均辐射 |

脚本采用距离城市中心最近的 NASA POWER 网格。

季节定义：

- 冬小麦：当年1–6月；
- 玉米：当年6–9月。

这些季节窗口用于公开数据基准模型，不代表完整的 APSIM 播种—收获模拟窗口。

### 3.3 山东16市边界和中心点

```text
data/raw/shandong_public/boundaries/shandong_prefecture_datav.geojson
```

用途：提供16个地级市名称和城市中心坐标，用于匹配统计表与选择最近气象网格。

### 3.4 输入数据获取脚本

```text
scripts/spatial_data/acquire_shandong_public_data.py
```

例如重新下载2018–2025年 NASA POWER数据：

```powershell
python scripts/spatial_data/acquire_shandong_public_data.py --years 2018 2019 2020 2021 2022 2023 2024 2025
```

下载记录：

```text
data/raw/shandong_public/download_manifest.json
```

该文件保存下载URL、文件大小和SHA-256，用于数据来源追踪和完整性检查。

## 4. 模型和SCE-UA参数

模型形式为：

```text
预测单产 = 城市训练期平均单产 × baseline_scale
         + 标准化气象特征 × 对应响应参数
         + 年际趋势参数 × trend
```

参与优化的参数：

| 参数 | 含义 |
|---|---|
| `baseline_scale` | 城市历史平均单产的整体尺度系数 |
| `z_temp_mean` | 标准化季节平均温度响应系数 |
| `z_temp_max_mean` | 标准化季节平均最高温响应系数 |
| `z_rain_sum` | 标准化季节累计降水响应系数 |
| `z_rad_mean` | 标准化季节平均辐射响应系数 |
| `z_heat_days` | 标准化高温日数响应系数 |
| `trend` | 年际趋势响应系数 |

目标函数为训练集RMSE与轻微参数惩罚之和。惩罚项用于减少气象响应参数无约束增大的风险。

当前SCE-UA设置位于主脚本的 `SCEUA` 类和 `fit_crop()` 函数中，主要包括：

- complexes：3；
- 随机种子：小麦2026、玉米2027；
- 最大目标函数评价次数：1500；
- `baseline_scale`范围：0.7–1.3；
- 其余响应参数范围：-1200–1200。

## 5. 输出文件

### 5.1 `metrics.json`

路径：

```text
outputs/calibration/public_sceua_shandong/metrics.json
```

用途：保存小麦、玉米在训练、验证、测试阶段的评价指标和SCE-UA最优参数。

主要字段：

| 字段 | 含义 |
|---|---|
| `n` | 评价样本数 |
| `RMSE` | 均方根误差，kg/ha |
| `MAE` | 平均绝对误差，kg/ha |
| `Bias` | 平均预测偏差，kg/ha；负值表示低估 |
| `R2` | 决定系数 |
| `parameters` | SCE-UA搜索得到的最优参数 |

这是查看模型总体表现和最终参数的首选文件。

### 5.2 `predictions.csv`

路径：

```text
outputs/calibration/public_sceua_shandong/predictions.csv
```

用途：保存每一个“年份—城市—作物”样本的观测值、气象特征、标准化特征、预测值和数据集划分。

主要字段：

| 字段 | 含义 |
|---|---|
| `year` | 数据年份 |
| `city_cn` | 城市名称 |
| `crop` | `wheat`或`maize` |
| `yield_obs` | 统计年鉴观测单产，kg/ha |
| `temp_mean` | 作物季平均温度，°C |
| `temp_max_mean` | 作物季平均最高温，°C |
| `rain_sum` | 作物季累计降水，mm |
| `rad_mean` | 作物季平均太阳辐射，MJ/m²/day |
| `heat_days` | 作物季最高温超过32°C的天数 |
| `z_*` | 使用训练集均值和标准差得到的标准化特征 |
| `baseline` | 该城市训练期平均单产，kg/ha |
| `trend` | 标准化年份趋势变量 |
| `yield_pred` | SCE-UA最优参数对应的预测单产，kg/ha |
| `split` | `train`、`validation`或`test` |

该文件适合用于：

- 绘制观测—预测散点图；
- 按城市或年份检查残差；
- 排查异常样本；
- 与其他模型进行逐样本比较。

### 5.3 `wheat_sceua_convergence.csv`

```text
outputs/calibration/public_sceua_shandong/wheat_sceua_convergence.csv
```

用途：记录冬小麦SCE-UA搜索过程。

字段：

- `evaluations`：累计目标函数评价次数；
- `best_objective`：该阶段找到的最小目标函数值。

可用于判断优化是否持续改善、是否提前停滞，以及最大评价次数是否足够。

### 5.4 `maize_sceua_convergence.csv`

```text
outputs/calibration/public_sceua_shandong/maize_sceua_convergence.csv
```

字段和用途与小麦收敛文件相同，但对应玉米模型。

## 6. 验证图件

模型时间独立验证图：

```text
outputs/figures/shandong_public_validation/public_sceua_temporal_validation.png
outputs/figures/shandong_public_validation/public_sceua_temporal_validation.svg
outputs/figures/shandong_public_validation/public_sceua_temporal_validation.pdf
outputs/figures/shandong_public_validation/public_sceua_temporal_validation.tiff
```

用途：比较统计单产与模型预测单产，并突出2024年独立测试结果。

- PNG：快速预览；
- SVG：可编辑矢量图；
- PDF：论文或报告排版；
- TIFF：600 dpi高分辨率输出。

CN_Wheat10面积对比图位于同一图件目录，但不属于SCE-UA单产模型输出：

```text
cn_wheat10_vs_city_statistics_2024.*
```

## 7. 相关说明文件

完整方法、结果和限制：

```text
docs/sceua/public_sceua_shandong_results.md
```

公开数据获取说明：

```text
docs/shandong_public_data_acquisition.md
```

公开数据源状态表：

```text
data/metadata/shandong_public_data_catalog.csv
```

## 8. 当前限制

1. 当前模型不是APSIM参数校准，SCE-UA最优参数不能写入APSIM品种文件。
2. 统计产量是市级聚合数据，不能验证像元级或地块级单产。
3. NASA POWER空间分辨率较粗，城市内部使用同一最近网格或少量网格信息。
4. 模型使用城市训练期平均单产作为基线，因此当前测试是同一批城市的跨年预测，不是新城市空间外推。
5. 公开数据缺少逐年市级播期、品种、施肥和灌溉信息，模型不能解释这些管理变化。
6. 当前结果没有LAI同化数据，符合本阶段暂不获取LAI的设定。

如果后续接入AgERA5或APSIM，建议保留相同的2018–2022训练、2023验证、2024测试划分，以便与当前公开数据基线公平比较。
