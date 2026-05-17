"""
05 批量运行 APSIM Classic。

APSIM Classic 7.10 会从安装目录 Model/Wheat.xml、Model/Maize.xml 读取
cultivar 参数。为了让每个 sample 使用自己的参数，本脚本按顺序执行：

1. 保存当前 Model/Wheat.xml、Model/Maize.xml 的运行时副本；
2. 对每个 sample，把 apsim_runs/model_overrides/sample_xxxxxx 中的 XML
   临时复制到 APSIM Model 目录；
3. 运行 APSIM；
4. 无论成功、失败或超时，都恢复原始 XML；
5. 记录日志和 run_status，然后继续下一个 sample。

不要并行运行本脚本，除非每个进程使用独立 APSIM 安装目录。
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import time
from pathlib import Path

import pandas as pd

from sobol_common import CROP_XML_FILES, LOG_DIR, MODEL_DIR, SIM_INDEX_CSV, backup_file, ensure_dirs, setup_logging


APSIM_EXE = Path(r"F:\APSIM710-r4221\Model\Apsim.exe")
# 如果你的 APSIM 命令行程序不是 Apsim.exe，请改这里，例如：
# APSIM_EXE = Path(r"F:\APSIM710-r4221\Model\Models.exe")
# APSIM_EXE = Path(r"F:\APSIM710-r4221\Model\APSIMRun.exe")

DEFAULT_TIMEOUT_SECONDS = None  # 可改成整数，例如 600


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run APSIM Classic Sobol sample files sequentially.")
    parser.add_argument("--limit", type=int, default=None, help="只运行前 N 个待运行 sample，例如 --limit 5。")
    parser.add_argument("--start-sample", type=int, default=None, help="从指定 sample_id 开始运行。")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="单个 sample 超时时间，单位秒。")
    parser.add_argument(
        "--only-status",
        default=None,
        help="只运行 simulation_index.csv 中 run_status/status 等于该值的行，例如 created。",
    )
    parser.add_argument("--quiet", action="store_true", help="减少终端输出；详细日志仍写入日志文件。")
    return parser.parse_args()


def discover_apsim_exe() -> Path:
    if APSIM_EXE.exists():
        return APSIM_EXE
    candidates = []
    for name in ["Apsim.exe", "Models.exe", "APSIMRun.exe", "ApsimModel.exe"]:
        candidates.extend(MODEL_DIR.glob(name))
    if candidates:
        return candidates[0]
    raise FileNotFoundError(f"未找到 APSIM 可执行文件，请手动设置 APSIM_EXE。目前为: {APSIM_EXE}")


def restore_originals(originals: dict[str, Path]) -> None:
    for crop, original in originals.items():
        target = CROP_XML_FILES[crop]
        if original.exists():
            shutil.copy2(original, target)


def expected_output_paths(row: pd.Series, apsim_file: Path, sample_tag: str) -> list[tuple[Path, Path]]:
    """Return (source_in_workdir, destination) pairs for APSIM Classic outputs.

    APSIM Classic may ignore absolute <filename> paths in outputfile modules and
    write files to the current working directory. We therefore archive outputs
    immediately after each sample.
    """
    pairs: list[tuple[Path, Path]] = []
    out_files = str(row.get("output_files", "") or "")
    for item in out_files.split(";"):
        item = item.strip()
        if not item:
            continue
        dst = Path(item)
        src = apsim_file.parent / dst.name
        pairs.append((src, dst))

    # Summary file is useful for debugging and is normally written beside outputs.
    sum_src = apsim_file.parent / "Rotation Sample.sum"
    if out_files:
        first_dst = Path(out_files.split(";")[0])
        out_dir = first_dst.parent
    else:
        out_dir = apsim_file.parent / "outputs" / sample_tag
    pairs.append((sum_src, out_dir / "Rotation Sample.sum"))
    return pairs


def remove_stale_outputs(pairs: list[tuple[Path, Path]], logger, sample_tag: str) -> None:
    for src, _ in pairs:
        if src.exists():
            try:
                src.unlink()
                logger.info("%s 已删除工作目录旧输出: %s", sample_tag, src)
            except Exception as exc:
                logger.warning("%s 删除旧输出失败 %s: %s", sample_tag, src, exc)


def archive_outputs(pairs: list[tuple[Path, Path]], logger, sample_tag: str) -> list[str]:
    archived: list[str] = []
    for src, dst in pairs:
        if not src.exists():
            logger.warning("%s 未找到预期输出: %s", sample_tag, src)
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                dst.unlink()
            shutil.move(str(src), str(dst))
            archived.append(str(dst))
            logger.info("%s 已归档输出: %s -> %s", sample_tag, src, dst)
        except Exception as exc:
            logger.warning("%s 归档输出失败 %s -> %s: %s", sample_tag, src, dst, exc)
    return archived


def select_rows(sim_index: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    selected = sim_index.copy()
    if args.start_sample is not None:
        selected = selected[selected["sample_id"].astype(int) >= int(args.start_sample)]
    if args.only_status is not None:
        status_cols = [c for c in ["run_status", "status"] if c in selected.columns]
        if status_cols:
            mask = False
            for col in status_cols:
                mask = mask | (selected[col].astype(str) == args.only_status)
            selected = selected[mask]
    selected = selected.sort_values("sample_id")
    if args.limit is not None:
        selected = selected.head(int(args.limit))
    return selected


def main() -> None:
    ensure_dirs()
    logger = setup_logging("05_run_apsim_batch")
    args = parse_args()
    if args.quiet:
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                handler.setLevel(logging.WARNING)
    if not SIM_INDEX_CSV.exists():
        raise FileNotFoundError(f"请先运行 04_generate_apsim_runs.py: {SIM_INDEX_CSV}")

    exe = discover_apsim_exe()
    logger.info("使用 APSIM_EXE: %s", exe)
    logger.info("运行参数: limit=%s, start_sample=%s, timeout=%s, only_status=%s",
                args.limit, args.start_sample, args.timeout, args.only_status)

    sim_index = pd.read_csv(SIM_INDEX_CSV)
    selected = select_rows(sim_index, args)
    if selected.empty:
        logger.warning("没有符合条件的 sample 需要运行。")
        return
    logger.info("本次计划运行 sample 数: %s", len(selected))

    original_dir = LOG_DIR / "runtime_original_model_xml"
    original_dir.mkdir(parents=True, exist_ok=True)
    originals: dict[str, Path] = {}
    for crop, path in CROP_XML_FILES.items():
        if path.exists():
            original = original_dir / path.name
            shutil.copy2(path, original)
            originals[crop] = original
            backup_file(path, "runtime_model_xml")
            logger.info("已保存运行时原始 XML: %s -> %s", path, original)
        else:
            logger.warning("APSIM Model XML 不存在: %s", path)

    statuses: list[tuple[int, str]] = []
    try:
        for _, row in selected.iterrows():
            sid = int(row["sample_id"])
            tag = f"sample_{sid:06d}"
            apsim_file = Path(row["apsim_file"])
            override_dir = Path(str(row.get("model_override_dir", "")))
            log_file = LOG_DIR / f"{tag}.log"
            output_pairs = expected_output_paths(row, apsim_file, tag)

            if not apsim_file.exists():
                statuses.append((sid, "failed_missing_apsim"))
                logger.warning("%s 缺少 APSIM 文件: %s", tag, apsim_file)
                continue
            if not override_dir.exists():
                statuses.append((sid, "failed_missing_override"))
                logger.warning("%s 缺少 model override: %s", tag, override_dir)
                continue

            try:
                restore_originals(originals)
                remove_stale_outputs(output_pairs, logger, tag)
                for override in override_dir.glob("*.xml"):
                    target = MODEL_DIR / override.name
                    shutil.copy2(override, target)
                    logger.info("%s 临时替换: %s -> %s", tag, override, target)

                cmd = [str(exe), str(apsim_file)]
                start = time.time()
                with open(log_file, "w", encoding="utf-8", errors="ignore") as lf:
                    lf.write("COMMAND: " + " ".join(cmd) + "\n")
                    lf.write("WORKDIR: " + str(apsim_file.parent) + "\n\n")
                    proc = subprocess.run(
                        cmd,
                        cwd=str(apsim_file.parent),
                        stdout=lf,
                        stderr=subprocess.STDOUT,
                        text=True,
                        timeout=args.timeout,
                    )
                elapsed = time.time() - start
                status = "finished" if proc.returncode == 0 else f"failed_returncode_{proc.returncode}"
                archived = archive_outputs(output_pairs, logger, tag)
                if proc.returncode == 0 and not archived:
                    status = "failed_no_outputs"
                statuses.append((sid, status))
                logger.info("%s: %s, 用时 %.1f s, 日志 %s", tag, status, elapsed, log_file)
            except subprocess.TimeoutExpired:
                statuses.append((sid, "failed_timeout"))
                logger.exception("%s 运行超时", tag)
            except Exception as exc:
                statuses.append((sid, "failed_exception"))
                logger.exception("%s 运行失败: %s", tag, exc)
            finally:
                restore_originals(originals)
                logger.info("%s 已恢复 baseline crop XML。", tag)
    finally:
        restore_originals(originals)
        logger.info("脚本结束前已再次恢复 baseline crop XML。")

    if statuses:
        status_map = dict(statuses)
        sim_index["run_status"] = sim_index["sample_id"].map(status_map).fillna(
            sim_index["run_status"] if "run_status" in sim_index.columns else "not_run"
        )
        sim_index.to_csv(SIM_INDEX_CSV, index=False, encoding="utf-8-sig")
        logger.info("批量运行完成，已更新: %s", SIM_INDEX_CSV)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger = setup_logging("05_run_apsim_batch")
        logger.exception("脚本失败: %s", exc)
        raise
