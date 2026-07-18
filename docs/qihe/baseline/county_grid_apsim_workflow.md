# 齐河县县域 APSIM smoke test（2020）

## 年份选择结论

默认 smoke test 已由 2024 改为 **2020**。2024 的问题不是流程无法运行，而是
CACD 只发布到 2023，主链必须把 2023 耕地范围当作 2024 的 `t-1` 近似；同时
本地 2024 玉米分类缺少可追溯 DOI、发布页和独立精度说明，不适合作为默认测试数据。

2020 能让耕地、小麦、小麦—玉米轮作和天气处在同一作物年，并且每个核心遥感
输入都有可直接下载、带 DOI 的公开版本。2022 虽然也有同年 CACD 和单作物图，
但没有像 2020 `ChinaCP-Wheat10m` 那样直接发布且独立验证的 10 m 小麦轮作类型图，
因此未选为默认年份。

## 数据闭合情况

| 输入 | 2020 主来源 | 用途与可信度 |
|---|---|---|
| 耕地 | CACD-v1 2020，30 m，DOI `10.5281/zenodo.16927779` | 同年耕地范围；值 1 为耕地，0 为非耕地 |
| 冬小麦 | CN_Wheat10 2020，10 m，DOI `10.6084/m9.figshare.28852220.v2` | 同年收获范围；年度产品覆盖 2018–2024 |
| 玉米 | 国家生态科学数据中心“2001–2024 年中国玉米种植分布数据集”，约 30 m，DOI `10.57760/sciencedb.08490` | 同年独立二值分类，EPSG:4326、值域 0/1；许可 CC BY-NC 4.0 |
| 小麦—玉米轮作 | ChinaCP-Wheat10m 2020，10 m，DOI `10.6084/m9.figshare.28646687.v3` | 直接使用发布的轮作类别，不再由两个异源单作物图求交；论文总体精度 92.57%，统计一致性 R²=0.96 |
| 天气 | NASA POWER 2019–2020 日值 | smoke-test 回退数据；正式研究仍建议换 AgERA5/ERA5 并进行站点验证 |
| 土壤 | HWSD v2.0 | 与年份无关；按格点中心分配，正式空间模拟应进一步做面积拆分 |
| 行政边界 | DataV 371425 接口 | 流程边界，不是测绘或地籍边界 |
| 统计验证 | 《2020 德州统计年鉴》表 6-7，第 116 页齐河县小麦、玉米数据 | 小麦 115.21 万亩、53.12 万吨、461.10 kg/亩；玉米 114.49 万亩、57.50 万吨、502.22 kg/亩；来源为德州市统计局官方年鉴入口 |

独立玉米图现已接入边际玉米面积和 APSIM 玉米单元。它与 ChinaCP-Wheat10m 轮作图
来自不同分类体系，因此不强制逐像元嵌套：轮作图面积 58,918.84 ha，其中与独立玉米图
重叠 48,118.47 ha，位于独立玉米图之外 10,800.37 ha。该差异作为跨产品一致性诊断和
质量标志保留，不通过裁剪把两个产品人为改成一致。`CCD-Maize`（DOI
`10.57760/sciencedb.08490`）仍可作为后续产品敏感性比较来源。

FTW 全球田界预测目前公开的是 2024–2025 年，不能标成 2020 同年田界。2020 主模拟
因此只使用 CACD 耕地范围和 5 km APSIM 代表格点。CACD 是 30 m 二值耕地范围图，
不是实例田块：将齐河 CACD 按 8 邻域连通后只有 515 个斑块，最大斑块约 66,276.52 ha，
占全部 CACD 耕地约 64.76%，直接矢量化会把大量相邻农田合成一个巨大多边形。

检索到的 CropLayer 2020（DOI `10.5281/zenodo.14726428`）提供全国 2 m 耕地范围，
像元精度 88.73%、格网语义正确率 96.5%，可用于高分辨率耕地边缘敏感性比较；但其
论文明确说明产品采用语义分割，不能区分单个田块，因此也不能直接替代田块实例边界。
先前“2020 作物属性 + FTW 2024 几何”的结果已从主输出撤下，仅保存在
`proxy_ftw2024/` 中作为跨年代理诊断；脚本现在要求显式传入 `--allow-temporal-proxy`
才允许生成跨年田界结果。

## 年度化目录

```text
data/raw/shandong_public/county_pilot_2020/       2020 下载包、解包文件和 manifest
data/raw/shandong_public/county_pilot_2024/       原 2024 原始数据归档
data/processed/spatial/county_pilot_2020/         2020 对齐掩膜、格点比例、QC、APSIM 单元
data/processed/spatial/county_pilot_2024/         原 2024 处理结果归档
outputs/spatial/county_pilot_2020/                2020 质量检查图
outputs/spatial/county_pilot_2024/                原 2024 图件及田块结果归档
docs/county_grid_apsim_workflow_2024.md            原 2024 方法与结果说明
```

原始下载包保留不覆盖；`download_manifest.json` 记录 URL、许可证说明、字节数、
SHA-256 和登记时间。二值/类别栅格统一采用最近邻重采样到 10 m Albers 等面积网格。

## 当前可复现命令

```powershell
python scripts/spatial_data/download_county_pilot_data.py --year 2020
tar -xf data/raw/shandong_public/county_pilot_2020/crop_masks/CN-Wheat10_2020.rar -C data/raw/shandong_public/county_pilot_2020/crop_masks
tar -xf data/raw/shandong_public/county_pilot_2020/crop_masks/wheat_maize_china_2020.zip -C data/raw/shandong_public/county_pilot_2020/crop_masks
python scripts/spatial_data/build_county_grid_pilot.py
python scripts/spatial_data/validate_county_grid_pilot.py
python scripts/apsim_inputs/build_county_simulation_units.py
```

本次实跑得到 85 个县域相交格点、耕地 102,346.26 ha、冬小麦 64,223.27 ha、
玉米 81,530.48 ha、小麦—玉米轮作 58,918.84 ha；QC 为 22/22 通过。遥感面积相对
齐河县表 6-7 播种面积的偏差分别为小麦 -16.38%、玉米 +6.82%。APSIM 索引共
247 个单元：81 个小麦、84 个玉米和 82 个轮作单元，模拟天气窗口为
2019-10-01 至 2020-12-30。

## 主要来源

- CACD-v1: https://doi.org/10.5281/zenodo.16927779
- CACD 方法论文: https://doi.org/10.5194/essd-16-2297-2024
- CN_Wheat10: https://doi.org/10.6084/m9.figshare.28852220.v2
- ChinaCP-Wheat10m: https://doi.org/10.6084/m9.figshare.28646687.v3
- CCD-Maize: https://doi.org/10.57760/sciencedb.08490
- 国家生态科学数据中心玉米数据发布页: https://www.nesdc.org.cn/sdo/detail?id=651403fd7e281774b9b5da68
- 德州市统计局年鉴入口: https://dztj.dezhou.gov.cn/n3100530/n38260319/index.html
- NASA POWER: https://power.larc.nasa.gov/
- Fields of The World: https://fieldsofthe.world/
- CropLayer 2020: https://doi.org/10.5281/zenodo.14726428
