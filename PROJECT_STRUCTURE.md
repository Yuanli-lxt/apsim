# process_bio 项目结构

这个项目主要用于测试 HDSW/HWSD 派生土壤参数在 APSIM 中的使用效果，并用独立观测数据验证模拟结果是否仍然可用。

## 当前顶层目录和文件角色

| 路径 | 作用 |
|---|---|
| `scripts/run_process_bio_search.py` | 主搜索脚本，用于 APSIM 参数调优，以及 HDSW 水分-产量搜索模式。 |
| `scripts/hwsd_to_apsimsoil.py` | 将 HWSD/HDSW 来源数据转换为 APSIM 可用的土壤/profile 输出。 |
| `scripts/apply_hwsd_to_apsim.py` | 将生成的土壤参数写入 APSIM 文件。 |
| `scripts/compare_apsim_prediction_observation.py` | 对比 APSIM 预测值和验证观测值，并输出图件。 |
| `scripts/diagnose_hdsw_water_yield_matrix.py` | 运行或诊断 HDSW 水分-产量矩阵案例。 |
| `scripts/local_*_output_sobol.py` | 围绕 `output_sobol/best` 的本地补充搜索脚本。 |
| `independent_validation_observations_p02_maize_p01_wheat.csv` | 搜索脚本使用的独立验证观测数据。 |
| `modified_from_truth.apsim` | 当前脚本使用的主要 APSIM 模板文件。 |
| `p0-1-24-25.met` | 气象输入文件。 |
| `hdsw/` | HDSW/HWSD 派生土壤输入工作区。 |
| `assimilation/` | 从旧 `mission` 目录筛选迁移的 LAI 同化脚本、配置、源数据和关键测试结果。 |
| `sobol/` | Sobol 敏感性分析流程；由于内部脚本使用绝对路径，暂时保留在根目录。 |
| `figures/` | 预测值与观测值对比检查生成的图件。 |
| `results/` | 已归档整理的生成结果目录，以及结果索引。 |

## 结果目录

体量较大的生成结果已经移动到 `results/`。为了兼容旧脚本，原来的根目录名称保留为 junction：

- `output`
- `output_hdsw`
- `output_hdsw_sobol_water_yield`
- `output_sobol`

目录对应关系见 `results/README.md` 和 `results/results_manifest.json`。

## 建议的下一步整理

1. 将仍然写死绝对路径的脚本改成参数化路径。
2. 将 `scripts/` 中可复用的逻辑进一步整理成一个小型 Python 包。
3. 在保留每轮运行的 `best/`、manifest、summary 和索引文件后，再决定是否压缩旧迭代目录。
