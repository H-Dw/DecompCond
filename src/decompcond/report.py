"""Reports and standalone SVG flowcharts derived from persisted execution."""

from __future__ import annotations
import html
from pathlib import Path
from .io import read_json, write_json, file_digest


def render_report(config):
    root = Path(config.data.output_dir)
    lines = [
        "# DecompCond 实际运行报告",
        "",
        f"配置哈希：`{config.fingerprint()}`",
        "",
        "以下数量来自已保存的执行记录；未执行阶段不填入模拟指标。",
        "",
    ]
    boxes = []
    timelines = []
    for teacher in config.data.teachers:
        directory = root / teacher.id
        lines += [f"## {teacher.target_id} / {teacher.node_id}", ""]
        if not (directory / "history.json").exists():
            lines += ["尚无已完成基线。", ""]
            continue
        history = read_json(directory / "history.json")
        condition_path = directory / "prepared" / "conditions.json"
        if condition_path.exists():
            library = read_json(condition_path)
            boxes.append(
                (
                    teacher.id,
                    f"PLIP：{len(library['atoms'])} 个原子，{len(library['groups'])} 个子条件",
                    None,
                )
            )
        records = [history["baseline"]] + [
            x for r in history["rounds"] for x in r["records"]
        ]
        final = (
            read_json(directory / "final.json")
            if (directory / "final.json").exists()
            else None
        )
        lines += [
            "| 阶段 | 策略 | 尝试 | 结构合格 | J | 合格候选 ΔpIC50 均值 |",
            "|---|---|---:|---:|---:|---:|",
        ]
        for record in records + (final.get("confirmation") or [] if final else []):
            timelines.append((teacher.id, record))
            s = record["summary"]
            affinity = s["valid_delta_pic50"]["mean"]
            lines.append(
                f"| {record['stage']} | {s['path_id']} | {s['attempts']} | {s['structure_pass_count']} | {s['J']:.4f} | {affinity if affinity is not None else 'NA'} |"
            )
        lines += [
            "",
            f"已完成搜索轮数：{len(history['rounds'])} / {config.search.rounds}。",
            "",
        ]
        boxes.append((teacher.id, "基线", history["baseline"]["summary"]["attempts"]))
        boxes.extend(
            (
                teacher.id,
                f"第 {r['round']} 轮：{len(r['records'])} 条路径",
                sum(x["summary"]["attempts"] for x in r["records"]),
            )
            for r in history["rounds"]
        )
        if final and final.get("confirmation"):
            boxes.append(
                (
                    teacher.id,
                    "独立确认",
                    sum(x["summary"]["attempts"] for x in final["confirmation"]),
                )
            )
    lines += [
        "坐标偏差在原蛋白坐标系计算。pIC50 是模型预测；独立 XYZ 重建采用原始模型元素预测，不能解释为仅凭坐标识别元素。",
        "",
    ]
    (root / "report.zh-CN.md").write_text("\n".join(lines), encoding="utf-8")
    width, height = 860, max(120, 100 * len(boxes) + 40)
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f5f7fa"/>',
    ]
    for i, (teacher, phase, count) in enumerate(boxes):
        y = 20 + i * 100
        if i and boxes[i - 1][0] == teacher:
            svg.append(
                f'<path d="M430 {y - 20} V{y}" stroke="#64748b" stroke-width="3"/>'
            )
        svg.append(
            f'<rect x="30" y="{y}" width="800" height="80" rx="12" fill="#e0f2fe" stroke="#0284c7"/>'
        )
        text = html.escape(
            f"{teacher} · {phase}"
            + (f" · 已评价 {count} 个候选" if count is not None else "")
        )
        svg.append(
            f'<text x="55" y="{y + 48}" font-family="sans-serif" font-size="20" fill="#0f172a">{text}</text>'
        )
    svg.append("</svg>")
    (root / "executed-flow.svg").write_text("\n".join(svg), encoding="utf-8")
    render_schedules(root, timelines, config.sampling.steps)
    render_candidate_index(root)
    return str(root / "report.zh-CN.md")


def render_candidate_index(root):
    """Keep every attempt; rank qualified candidates by recovery, then pIC50."""
    rows = []
    for path in sorted(root.glob("*/*/*/*/evaluation.json")):
        report = read_json(path)
        teacher, stage, strategy, seed, _ = path.relative_to(root).parts
        rows.append(
            dict(
                teacher=teacher,
                stage=stage,
                path_id=strategy,
                seed=int(seed),
                status=report["status"],
                structure_pass=report["structure_pass"],
                recovery=report["recovery"],
                pic50=report.get("pic50"),
                report_path=str(path),
                report_sha256=file_digest(path),
            )
        )
    qualified = [r for r in rows if r["structure_pass"]]
    qualified.sort(key=lambda r: (r["recovery"], r["pic50"]), reverse=True)
    # Keep confirmation separate from discovery to avoid selecting with holdout data.
    write_json(
        root / "candidates.json",
        dict(
            all_attempts=rows,
            discovery_ranking=[r for r in qualified if r["stage"] != "confirmation"],
            confirmation_ranking=[r for r in qualified if r["stage"] == "confirmation"],
        ),
    )


def render_schedules(root, records, steps):
    """Only completed, evaluated paths appear; segment geometry uses real indices."""
    width, height = 1200, max(180, 90 * len(records) + 100)
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
        '<text x="24" y="30" font-family="sans-serif" font-size="20">DecompCond · executed condition schedules</text>',
        f'<text x="24" y="55" font-family="sans-serif" font-size="14">Update range [0,{steps}); gray = no injection; metrics from raw output</text>',
    ]
    for i, (teacher, record) in enumerate(records):
        y = 80 + i * 90
        p = record["path"]
        s = record["summary"]
        label = html.escape(
            f"{teacher} / {record['stage']} / {p['id']} | J={s['J']:.3f} | valid={s['structure_pass_count']}/{s['attempts']}"
        )
        svg.append(
            f'<text x="24" y="{y}" font-family="sans-serif" font-size="13">{label}</text>'
        )
        for segment in p["segments"]:
            x = 24 + 1150 * segment["start"] / steps
            w = 1150 * (segment["end"] - segment["start"]) / steps
            active = segment["active"]
            color = "#bae6fd" if active else "#e2e8f0"
            text = html.escape(",".join(active) if active else "none")
            tip = html.escape(
                f"[{segment['start']},{segment['end']}): {','.join(active) or 'none'}"
            )
            svg.append(
                f'<g><title>{tip}</title><rect x="{x}" y="{y + 10}" width="{w}" height="38" fill="{color}" stroke="white"/>'
            )
            fit = (
                f' textLength="{max(1, w - 8)}" lengthAdjust="spacingAndGlyphs"'
                if len(text) * 6 > w - 8
                else ""
            )
            svg.append(
                f'<text x="{x + 4}" y="{y + 22}" font-family="sans-serif" font-size="9">[{segment["start"]},{segment["end"]})</text>'
            )
            svg.append(
                f'<text x="{x + 4}" y="{y + 38}" font-family="sans-serif" font-size="10"{fit}>{text}</text></g>'
            )
    svg.append("</svg>")
    (root / "executed-schedules.svg").write_text("\n".join(svg), encoding="utf-8")
