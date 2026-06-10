# process_bio 结果索引

这个目录用于集中存放原来散落在项目根目录中的大型生成结果目录。原来的根目录名称已经保留为 Windows junction，因此已有脚本和硬编码路径仍然可以继续工作。

## 目录布局

| 目录 | 原始入口 | 含义 | 建议优先查看的文件 |
|---|---|---|---|
| `legacy_bio_search_20260506_output/` | `output/` | 早期 bio-first 迭代搜索结果。 | `best/summary_zh.md`, `best/metrics.json`, `best/prediction_vs_truth.csv` |
| `hdsw_single_validation_20260513_output_hdsw/` | `output_hdsw/` | HDSW/HWSD 土壤替换后的单独验证结果。 | `soil_parameter_comparison.txt`, `output/best/summary_zh.md`, `output/best/metrics.json` |
| `hdsw_water_yield_search_20260519_output_hdsw_sobol_water_yield/` | `output_hdsw_sobol_water_yield/` | HDSW 水分-产量搜索和诊断矩阵结果。 | `final_hdsw_water_yield_report.md`, `iteration_index.csv`, `best/summary_zh.md` |
| `local_sobol_guided_search_20260519_output_sobol/` | `output_sobol/` | 本地 Sobol 引导搜索、脚本备份和 fraction-fine 实验结果。 | `best/summary_zh.md`, `best/metrics.json`, `fraction_fine_20260519_173532/` |

## 项目根目录中的兼容入口

以下根目录入口现在是 junction：

- `output` -> `results/legacy_bio_search_20260506_output`
- `output_hdsw` -> `results/hdsw_single_validation_20260513_output_hdsw`
- `output_hdsw_sobol_water_yield` -> `results/hdsw_water_yield_search_20260519_output_hdsw_sobol_water_yield`
- `output_sobol` -> `results/local_sobol_guided_search_20260519_output_sobol`

只要脚本里还存在硬编码路径，就建议保留这些 junction。等脚本完成路径参数化、旧路径兼容不再需要后，可以删除这些 junction；删除 junction 不会删除 `results/` 下真正归档的结果目录。

## 暂未移动的目录

`sobol/` 仍然保留在项目根目录，因为 `sobol/scripts/` 下有多个脚本使用指向该位置的绝对路径。如果不先同步修改这些脚本，直接移动会破坏现有 Sobol 流程。

`hdsw/` 也仍然保留在项目根目录，因为它更像输入/派生土壤 profile 工作区，而不是搜索输出归档。

