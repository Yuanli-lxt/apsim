# APSIM 7.10 Classic + EnKF + LAI 同化

这个版本可以直接从 Python 调用你的 `APSIM.exe`，并用 LAI 真值做 **顺序同化**。

## 这个实现的思路

这份脚本采用的是 **参数增强（parameter augmentation）EnKF**：

- 观测：LAI 真值
- 模型：APSIM 7.10 Classic
- 同化对象：一组会影响 LAI 的参数集合，而不是强行直接改 APSIM 运行中的内部 LAI 状态

这样做的好处是：

- 对 APSIM Classic 更稳定
- 不需要改 APSIM 源码
- 可以直接在你现在的 Classic 版本上跑起来
- 后面也容易扩展到 Biomass / SoilWater / Yield

## 文件说明

- `apsim_enkf_lai.py`：主脚本
- `apsim_enkf_config_template.json`：配置模板
- `lai_obs_template.csv`：LAI 观测模板

## 你需要改的 4 个地方

### 1）准备 LAI 观测文件

把 `lai_obs_template.csv` 改成你自己的观测：

```csv
date,lai,std
2024-01-15,0.35,0.08
2024-01-25,0.82,0.10
```

其中：
- `date`：观测日期
- `lai`：LAI 真值
- `std`：该次观测误差标准差

### 2）检查 APSIM 输出列名

你的 `.out` 文件里 LAI 列名不一定就是 `lai`，也可能是：
- `LAI`
- `lai`
- `wheat.lai`
- 其它名字

然后把配置里的：

```json
"date_col": "date",
"lai_col": "lai"
```

改成你自己的输出列名。

### 3）确定要同化的参数

默认给了 3 个常见参数因子：
- `sla_factor`
- `rue_factor`
- `tt_factor`

它们会通过 XML 搜索以下关键字：
- `sla`
- `rue`
- `tt`

但你自己的 `.apsim` 文件里，真实参数名字可能不一样。

可以先用下面这个小命令扫描：

```python
from apsim_enkf_lai import scan_numeric_xml_nodes

df = scan_numeric_xml_nodes(r"F:\APSIM710-r4221\yuan\test.apsim", keyword="sla")
print(df.head(50))
```

如果搜不到，就换关键词，比如：
- `leaf`
- `rue`
- `tt`
- `phyll`
- `extinct`

### 4）改配置文件里的路径

你现在的路径已经可以直接填：

```json
"template_apsim": "F:\\APSIM710-r4221\\yuan\\test.apsim",
"apsim_exe": "F:\\APSIM710-r4221\\Model\\APSIM.exe"
```

## 运行方法

```bash
python apsim_enkf_lai.py apsim_enkf_config_template.json
```

运行后会生成：

- `enkf_workspace/assimilation_history.csv`
- `enkf_workspace/final_posterior_mean/`

其中 `assimilation_history.csv` 会保存每次同化的：
- 观测 LAI
- 先验 LAI 均值
- 先验 LAI 标准差
- 每个参数的后验均值 / 标准差

## 特别重要

这套代码是 **APSIM Classic 上最稳妥的“一步到位”版本**，但它不是“直接把 APSIM 内部 LAI 状态硬改掉”的那种强同化。

如果你后面想做更严格的：

- 直接状态更新
- 多变量同化（LAI + Biomass + SoilWater）
- 网格级并行同化
- 面级批量运行

更适合继续升级到：

- ApsimX / APSIM NextGen
- 或者 APSIM 源码级改造

不过对你现在这个 APSIM 7.10 Classic，这个版本已经足够作为一个完整起点。
