# WRF 与 APSIM 未来结合方案

## 当前本地状态

本轮确认项目已有 APSIM `.met` 气象输入：

- `data/weather/apsim_met/p0-1-24-25.met`

用户已确认该 `.met` 的原始来源为实测真实气象数据。

用户确认 WRF/ERA5/GFS 文件在仓库外另有存放位置。本仓库内暂未迁入以下文件或流程证据：

- WRF 输出：`wrfout_d0*`
- WPS 中间文件：`met_em*`
- ERA5/GFS 原始或中间文件
- WRF/WPS 运行日志
- WRF 输出变量提取脚本
- WRF 转 APSIM `.met` 脚本
- APSIM 使用 WRF `.met` 文件运行的示例结果
- WRF 气象驱动下的 APSIM 产量预测与实测产量对比

因此当前判断：

- ERA5/GFS 边界数据准备：仓库外另有存放位置，仓库内未接入
- WPS 预处理：未发现证据
- WRF 模拟：未发现证据
- wrfout 输出：未发现证据
- WRF 变量提取：未发现证据
- APSIM `.met` 生成：已有实测气象来源 `.met` 文件，但未发现 WRF 生成链条
- APSIM 作物模型运行：已完成，但基于现有实测气象 `.met`，不是已证实的 WRF `.met`
- 产量预测评估：已完成，但不是已证实的 WRF 驱动评估

## 未来目标流程

```text
ERA5 / GFS / 其他边界数据
        ->
WPS 预处理
        ->
WRF 模拟
        ->
wrfout 输出
        ->
提取目标站点或目标区域气象变量
        ->
单位转换、时间聚合、质量检查
        ->
生成 APSIM .met 文件
        ->
APSIM 作物模型运行
        ->
产量预测结果
        ->
与实测产量/生物量/土壤水分/物候对比评估
```

## WRF 输出需要提取的变量

APSIM 当前 `.met` 文件需要以下日尺度字段：

- `year`
- `day`
- `radn`
- `maxt`
- `mint`
- `rain`

因此 WRF 接入至少需要从 `wrfout` 推导：

| APSIM 字段 | 含义 | WRF 侧候选来源 | 处理要求 |
|---|---|---|---|
| `year` | 年份 | WRF 时间坐标 | 转为本地日期所属年份 |
| `day` | 年积日 | WRF 时间坐标 | 转为 day-of-year |
| `radn` | 日太阳辐射 | 短波辐射相关变量 | 聚合到日总量，单位转为 MJ/m2/day |
| `maxt` | 日最高气温 | 2 m 气温相关变量 | 逐日最大值，单位转为 deg C |
| `mint` | 日最低气温 | 2 m 气温相关变量 | 逐日最小值，单位转为 deg C |
| `rain` | 日降水量 | 累积降水相关变量 | 累积量差分或日累计，单位 mm/day |

可选增强字段需要先确认 APSIM 模型是否读取：

- wind
- vapor pressure / relative humidity
- CO2
- snow
- evaporation related variables

当前 `data/weather/apsim_met/p0-1-24-25.met` 只显示 `radn maxt mint rain`，所以第一版 WRF 接入应先满足这四个核心气象字段。

## 单位转换逻辑

建议在 WRF 转 `.met` 脚本中显式记录每个变量的单位转换：

- 温度：K -> deg C，公式为 `degC = K - 273.15`。
- 降水：累积降水变量 -> 日降水量 mm/day，需要按时间差分或按日重采样；差分后应检查负值和重置点。
- 辐射：W/m2 或 J/m2 累积量 -> MJ/m2/day。若是瞬时通量，需要按时间积分；若是累积量，需要做相邻时次差分或日累计。
- 时间：UTC 或模式本地时间 -> APSIM 所需日期。必须记录时区处理。

注意：以上是未来脚本设计要求。本地仓库尚无实现，不能写作已完成。

## 时间尺度处理

APSIM 当前 `.met` 是日尺度输入，因此 WRF 小时级或更高频输出需要聚合到日尺度：

- `maxt`：每日所有时次气温最大值。
- `mint`：每日所有时次气温最小值。
- `rain`：每日降水累计。
- `radn`：每日太阳辐射总量。

建议输出质量检查表：

- 每日时次数是否完整。
- 是否存在缺测日。
- 是否存在负降水。
- 是否存在极端异常温度。
- WRF 起止日期是否覆盖 APSIM 模拟期。

## 空间尺度处理

需要根据研究对象选择一种方式：

1. 站点最近格点：适合单站点 APSIM 模拟，流程简单。
2. 双线性插值到站点经纬度：适合站点不在格点中心时使用。
3. 区域平均：适合区域代表性 APSIM 模拟。
4. 多站点批量提取：适合多个 plot/site 同时运行。

当前本地 `.met` 文件头中有：

- latitude = 37.94
- longitude = 118.53

因此第一版 WRF 接入可先以该站点为目标坐标，生成与 `data/weather/apsim_met/p0-1-24-25.met` 同结构的 `.met`。

## APSIM `.met` 生成方案

第一版建议输出：

```text
[weather.met.weather]
latitude = <lat> (dec deg)
longitude = <lon> (dec deg)
tav = <annual average temperature> (oC)
amp = <annual amplitude> (oC)
year   day   radn   maxt   mint   rain
 ()    ()   (MJ/m2) (oC)   (oC)   (mm)
...
```

建议生成文件命名：

```text
data/weather/apsim_met/wrf_<domain>_<site>_<start>_<end>.met
```

同时生成旁路元数据：

```text
data/weather/apsim_met/wrf_<domain>_<site>_<start>_<end>.metadata.json
```

元数据至少记录：

- WRF 文件路径
- WRF domain
- 站点经纬度
- 提取方法
- 时间范围
- 时区
- 变量映射
- 单位转换
- 缺测和异常检查结果

## WRF 气象质量验证

建议分三层验证：

1. 文件结构验证：`wrfout` 时间、空间、变量存在性。
2. 气象物理合理性验证：温度范围、降水非负、辐射非负、日尺度连续性。
3. 与参考气象对比：以当前实测气象来源 `data/weather/apsim_met/p0-1-24-25.met` 或其原始观测文件为基准，比较日尺度 `maxt/mint/rain/radn` 的偏差。

建议指标：

- bias
- MAE
- RMSE
- R2 或相关系数
- 缺测率
- 极端值数量

## APSIM 产量预测效果验证

WRF `.met` 生成后，不能只看 APSIM 是否跑完，还需要与当前已有评估链条对接：

```text
WRF .met
  -> APSIM run
  -> prediction_vs_truth.csv
  -> metrics.json
  -> figures/*.png
```

建议对比三组：

- 当前基线：`data/weather/apsim_met/p0-1-24-25.met`
- WRF 最近格点 `.met`
- WRF 插值或区域平均 `.met`

验证指标：

- 产量误差：绝对误差、相对误差、RMSE/MAE。
- 生物量误差。
- 土壤水分误差。
- 物候误差天数。
- 与当前 best 的综合评分差异。

## 可复现 pipeline 建议

建议新增脚本和配置：

```text
configs/weather/wrf_to_apsim_met.example.json
scripts/weather/extract_wrf_to_daily_weather.py
scripts/weather/write_apsim_met.py
scripts/weather/validate_weather_met.py
scripts/apsim_run/run_with_met.py
scripts/evaluation/compare_weather_driver_runs.py
```

建议 pipeline：

```text
1. 读取 wrf_to_apsim_met 配置
2. 扫描 wrfout 文件并检查变量
3. 提取目标站点/区域
4. 聚合到日尺度
5. 做单位转换和质量控制
6. 写 APSIM .met 和 metadata.json
7. 复制或修改 APSIM 模板引用新 .met
8. 运行 APSIM
9. 复用现有 prediction_vs_truth 和 metrics 评估
10. 生成 weather-driver 对比报告
```

## 后续开发步骤

1. 建立 `data/weather/` 和 `scripts/weather/`，先放配置模板和空 pipeline 文档。
2. 确认 WRF 输出文件真实存放位置、domain、时间范围和变量名。
3. 写一个只处理单站点、单 domain、单季节的 WRF -> `.met` 最小脚本。
4. 用当前 `.met` 的经纬度和字段结构做格式对齐。
5. 运行 APSIM 小样本，确认模型可以读取 WRF `.met`。
6. 接入现有 `prediction_vs_truth.csv` 和 `metrics.json` 评价逻辑。
7. 再扩展到多站点、多年份或区域平均。
