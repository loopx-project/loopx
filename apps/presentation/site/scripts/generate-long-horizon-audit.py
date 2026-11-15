"""Render the published Blog aggregates; run with `uv run --with matplotlib`.

The JSON preserves published cells and uncertainty, not private raw trajectories.
Matplotlib is an optional authoring dependency, never a website runtime dependency.
"""
from pathlib import Path
import json

import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt


BLOG = Path(__file__).resolve().parents[1] / "public/blog"
DATA = json.loads((BLOG / "data/long-horizon-audit.json").read_text())
LESSON = json.loads((BLOG / "context-delivery/measurements.json").read_text())
mpl.rcParams.update({"svg.fonttype": "none", "svg.hashsalt": "loopx-blog-audit",
    "font.family": "sans-serif", "font.sans-serif": ["PingFang SC", "Noto Sans CJK SC", "DejaVu Sans"],
    "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
    "axes.spines.bottom": False, "axes.labelcolor": "#334155", "text.color": "#0f172a"})
SLATE, BLUE, TEAL, EMERALD, INDIGO, PURPLE, GRAY = (
    "#334155", "#2563eb", "#0f766e", "#059669", "#4f46e5", "#7c3aed", "#64748b"
)


def save(fig, relative):
    path = BLOG / relative
    fig.savefig(path, format="svg", facecolor="white", metadata={"Date": None})
    path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n")
    plt.close(fig)


for language in ("en", "zh"):
    zh = language == "zh"
    # 1. Independent archived tutorial comparisons, not one measured cascade.
    # Each numerator is paired with its own recorded baseline. In particular,
    # a manual --select response is not a native --initial-read measurement.
    orig_b = LESSON["native_projection"]["original_bytes"]
    required_b = LESSON["native_projection"]["required_bytes"]
    prog_b = LESSON["cycle_codec"]["current_full_bytes"]
    pdelta_b = LESSON["cycle_codec"]["delta_bytes"]
    manual_source_b = next(r for r in LESSON["reader_rows"] if r["case"] == "complete_capture")["body_bytes"]
    batch_row = next(r for r in LESSON["reader_rows"] if r["case"] == "one_batched_select")
    first_body_lesson = batch_row["body_bytes"]
    first_cli_lesson = batch_row["output_bytes"]

    fig = plt.figure(figsize=(14.4, 6.9))
    gs = fig.add_gridspec(
        2, 2, width_ratios=[1.05, 1.15], height_ratios=[1.0, 1.0],
        left=.18, right=.97, bottom=.20, top=.78, wspace=.48, hspace=.58,
    )
    left = fig.add_subplot(gs[:, 0])
    right_top = fig.add_subplot(gs[0, 1])
    right_bot = fig.add_subplot(gs[1, 1])

    cascade_vals = [100 * required_b / orig_b, 100 * pdelta_b / prog_b,
                    100 * first_body_lesson / manual_source_b]
    cascade_labels = (
        [
            "Progressive 必要视图 / 原捕获",
            "周期内 Delta / 本轮完整交付",
            "手工批量选读 / 教程捕获",
        ]
        if zh else
        [
            "Progressive view / original capture",
            "In-cycle Delta / full delivery",
            "Manual selection / tutorial capture",
        ]
    )
    cascade_notes = [
        f"{cascade_vals[0]:.1f}% ({required_b:,} / {orig_b:,} B)",
        f"{cascade_vals[1]:.1f}% ({pdelta_b:,} / {prog_b:,} B)",
        f"{cascade_vals[2]:.1f}% ({first_body_lesson:,} / {manual_source_b:,} B · {100*first_cli_lesson/manual_source_b:.1f}% CLI)",
    ]
    left.barh(range(3), cascade_vals, color=[BLUE, TEAL, PURPLE], height=.50)
    left.set_yticks(range(3), cascade_labels, fontsize=10.2)
    left.invert_yaxis()
    left.set_xlim(0, 168)
    left.set_xlabel("占各行自身对照基线的字节 (%)" if zh else "Share of each row's own baseline bytes (%)", fontsize=9.8)
    left.set_title(
        "A. 归档教程的三组独立对照" if zh else "A. Three independent archived tutorial comparisons",
        loc="left", pad=14, fontsize=11.5, fontweight="bold",
    )
    for idx, (val, note) in enumerate(zip(cascade_vals, cascade_notes)):
        weight = "bold" if idx == 1 else "normal"
        color = "#0f766e" if idx == 1 else "#0f172a"
        left.text(val + 2.2, idx, note, va="center", fontsize=9.6, fontweight=weight, color=color)

    # Panel B1: Real 18h ANN paired audit — stacking Compaction-Cycle Structured Delta on top of Progressive
    ann_prog_b = 3089841
    ann_all46_b = 1898889
    ann_vals = [100.0, 100 * ann_all46_b / ann_prog_b, 100.0 - 74.25]
    ann_labels = (
        [
            "Progressive 选中包 (46 轮)",
            "+ 周期内 Delta (全 46 轮)",
            "+ 周期内 Delta (24 增量轮)",
        ]
        if zh else
        [
            "Progressive selected (46)",
            "+ In-cycle Delta (all 46)",
            "+ In-cycle Delta (24 delta)",
        ]
    )
    ann_notes = (
        [
            "100.0% (3.09 MB 选中基线)",
            f"{ann_vals[1]:.1f}% (1.90 MB · 再减 −38.54%)",
            f"{ann_vals[2]:.2f}% (同周期再减 −74.25%)",
        ]
        if zh else
        [
            "100.0% (3.09 MB selected base)",
            f"{ann_vals[1]:.1f}% (1.90 MB · −38.54%)",
            f"{ann_vals[2]:.2f}% (in-cycle −74.25%)",
        ]
    )
    right_top.barh(range(3), ann_vals, color=[BLUE, TEAL, EMERALD], height=.52)
    right_top.set_yticks(range(3), ann_labels, fontsize=9.8)
    right_top.invert_yaxis()
    right_top.set_xlim(0, 188)
    right_top.set_xlabel("占 Progressive 选中字节 (%)" if zh else "Share of Progressive selected bytes (%)", fontsize=9.5)
    right_top.set_title(
        "B1. 18h 实测核心增量：叠加压缩周期内结构化 Delta（ANN）"
        if zh else
        "B1. 18h core layer: stacking compaction-cycle Delta (ANN)",
        loc="left", pad=12, fontsize=11.0, fontweight="bold",
    )
    for idx, (val, note) in enumerate(zip(ann_vals, ann_notes)):
        weight = "bold" if idx >= 1 else "normal"
        color = "#0f766e" if idx >= 1 else "#0f172a"
        right_top.text(val + 2.4, idx, note, va="center", fontsize=9.4, fontweight=weight, color=color)

    # Panel B2: 8-task 18h aggregate step-down (explicit cohorts)
    rows = DATA["context"]["tasks"]
    prog_213 = 100 * sum(r["progressive_bytes"] for r in rows) / sum(r["full_bytes"] for r in rows)
    denom_112 = DATA["context"]["delta_full_bytes_reported_approx"]
    first_cli_112 = 100 * sum(r["first_cli_bytes"] for r in rows) / denom_112
    first_body_112 = 100 * sum(r["first_body_bytes"] for r in rows) / denom_112
    real_vals = [100.0, prog_213, first_body_112]
    real_labels = (
        [
            "All · 完整决策包",
            "Progressive · 213 次",
            "Prog+Delta 首读 · 112 次",
        ]
        if zh else
        [
            "All · Full packets",
            "Progressive · 213 wakes",
            "Prog+Delta First read · 112",
        ]
    )
    real_notes = (
        [
            "100.0% (22.41 MB / 续跑 ≈12.53 MB)",
            f"{prog_213:.1f}% (7.29 MB · 净减 −67.5%)",
            f"≈ {first_body_112:.1f}% 正文 / ≈ {first_cli_112:.1f}% CLI (−97% / −95%)",
        ]
        if zh else
        [
            "100.0% (22.41 MB / in-cycle ≈12.53 MB)",
            f"{prog_213:.1f}% (7.29 MB · −67.5%)",
            f"≈ {first_body_112:.1f}% body / ≈ {first_cli_112:.1f}% CLI (−97% / −95%)",
        ]
    )
    right_bot.barh(range(3), real_vals, color=[SLATE, BLUE, PURPLE], height=.52)
    right_bot.set_yticks(range(3), real_labels, fontsize=9.8)
    right_bot.invert_yaxis()
    right_bot.set_xlim(0, 188)
    right_bot.set_xlabel("占对应唤醒组 Full 字节 (%)" if zh else "Share of cohort Full bytes (%)", fontsize=9.5)
    right_bot.set_title(
        "B2. 八题 18h 队列汇总：从 100% 到叠加首读入口"
        if zh else
        "B2. 8-task 18h cohorts: 100% Full to Prog+Delta entry",
        loc="left", pad=12, fontsize=11.0, fontweight="bold",
    )
    for idx, (val, note) in enumerate(zip(real_vals, real_notes)):
        right_bot.text(val + 2.4, idx, note, va="center", fontsize=9.4)

    for axis in (left, right_top, right_bot):
        axis.set_axisbelow(True)
        axis.grid(axis="x", color="#e2e8f0", linewidth=.7)
        axis.tick_params(axis="both", length=0)
        axis.set_xticks([0, 25, 50, 75, 100])

    fig.suptitle(
        "分层上下文：独立教程对照与 18h 周期内差分观测"
        if zh else
        "Layered context: separate tutorial comparisons and 18h in-cycle Delta observations",
        fontsize=15.0, fontweight="bold", y=.955,
    )
    sub = (
        "渐进视图保留必要事实；周期内结构化 Delta 复用已验证子树。压缩或失配恢复完整基线，不保证任意输入逐级变小。"
        if zh else
        "Progressive views retain necessary facts; in-cycle Delta reuses verified subtrees. Compaction or mismatch restores the complete baseline."
    )
    fig.text(.5, .87, sub, ha="center", fontsize=10.2, color="#475569")
    footer_cascade = (
        "A：measurements.json 的独立归档对照，各行分母不同；674 B 为三个 --select 的手工选读，未测同源四阶段的 --initial-read。\n"
        "B2：八题 213 次唤醒（Progressive）与 112 次同周期续跑（Prog+Delta 首读）各自相对原分母的比例；跨队列分母分开列出，不做混乘。"
        if zh else
        "A: independent archived comparisons in measurements.json; 674 B is a manual three-path selection, not a measured stacked --initial-read.\n"
        "B2: 8-task shares over 213 wakes (Progressive) and 112 in-cycle continuations (Prog+Delta first-read); each cohort keeps its own denominator."
    )
    fig.text(.03, .03, footer_cascade, fontsize=9.4, color="#475569", linespacing=1.5)
    save(fig, f"images/context-delivery/context-cascade-{language}.svg")

    # 2. Eight-task detailed byte ratio audit (with 100% Full baseline bar & explicit single-layer vs stacked labels)
    rows = DATA["context"]["tasks"]
    fig, axes = plt.subplots(1, 2, figsize=(14.0, 6.8), gridspec_kw={"width_ratios": [1.38, 1.08]})
    fig.subplots_adjust(left=.15, right=.96, bottom=.23, top=.80, wspace=.50)
    left, right = axes
    series = [
        ("Progressive · 全部 213 次" if zh else "Progressive · all 213 wakes", [100*r["progressive_bytes"]/r["full_bytes"] for r in rows], BLUE),
        ("单路无损 Delta 文件 · 112 次" if zh else "Lossless Delta file · 112 wakes", [r["delta_share_reported"] for r in rows], TEAL),
        ("Prog+Delta 首读正文 · 112 次" if zh else "Prog+Delta First body · 112 wakes", [r["first_body_share_reported"] for r in rows], PURPLE),
    ]
    for index, (label, values, color) in enumerate(series):
        positions = [i + (index-1)*.23 for i in range(len(rows))]
        left.barh(positions, values, height=.20, label=label, color=color)
    left.set_yticks(range(len(rows)), [r["task"] for r in rows], fontsize=10)
    left.invert_yaxis()
    left.set_xlim(0, 55)
    left.set_xlabel("占同组 Full 字节 (%)" if zh else "Share of Full bytes within each cohort (%)")
    left.set_title(
        "A. 逐题占比 · 单层文件载荷与叠加首读" if zh else "A. Per-task shares · single-layer vs stacked",
        loc="left", pad=16, fontsize=11.5, fontweight="bold",
    )

    prog_share = 100 * sum(r["progressive_bytes"] for r in rows) / sum(r["full_bytes"] for r in rows)
    denominator = DATA["context"]["delta_full_bytes_reported_approx"]
    delta_share = 100 * sum(r["delta_bytes"] for r in rows) / denominator
    first_cli_share = 100 * sum(r["first_cli_bytes"] for r in rows) / denominator
    first_body_share = 100 * sum(r["first_body_bytes"] for r in rows) / denominator

    totals = [100.0, delta_share, prog_share, first_cli_share, first_body_share]
    labels = (
        [
            "All (Full) · 213 / 112",
            "单路 Delta 文件 · 112",
            "Progressive 视图 · 213",
            "Prog+Delta 首读 CLI · 112",
            "Prog+Delta 首读正文 · 112",
        ]
        if zh else
        [
            "All (Full) · 213 / 112",
            "Delta file only · 112",
            "Progressive view · 213",
            "Prog+Delta First CLI · 112",
            "Prog+Delta First body · 112",
        ]
    )
    right.barh(range(5), totals, color=[SLATE, TEAL, BLUE, GRAY, PURPLE], height=.54)
    right.set_yticks(range(5), labels, fontsize=10)
    right.invert_yaxis()
    right.set_xlim(0, 128)
    right.set_xticks([0, 25, 50, 75, 100])
    approx_flags = [False, True, False, True, True]
    for index, (value, approx) in enumerate(zip(totals, approx_flags)):
        right.text(value + 2.0, index, f"{'≈ ' if approx else ''}{value:.1f}%", va="center", fontsize=10.5)
    right.set_title(
        "B. 从 100% 对照单层载荷与叠加首读" if zh else "B. From 100%: single-layer vs stacked entry",
        loc="left", pad=16, fontsize=11.5, fontweight="bold",
    )
    for axis in axes:
        axis.set_axisbelow(True)
        axis.grid(axis="x", color="#e2e8f0", linewidth=.7)
        axis.tick_params(axis="both", length=0)
    fig.suptitle(
        "八题上下文字节审计：区分单层载荷（Progressive / 无损 Delta）与叠加首读"
        if zh else
        "Eight-task context byte audit: single-layer payloads vs stacked Prog+Delta entry",
        fontsize=15.2, fontweight="bold", y=.955,
    )
    fig.legend(*left.get_legend_handles_labels(), loc="lower center", ncol=3, frameon=False, bbox_to_anchor=(.50, .115), fontsize=9.8)
    footer = (
        "All：22,408,690 B（213 次）/ 约 12.53 MB（112 次续跑）。归档表中「单路 Delta 文件」（4,488,335 B ≈ 35.8%）是未叠加渐进裁剪的完整无损差分文件，\n"
        "与 Progressive（7,285,951 B = 32.5%）同属单层裁剪；两者叠加后的 Prog+Delta 首读入口降至 ≈ 3.0%（正文）/ ≈ 5.0%（含 CLI 封装）。"
        if zh else
        "All: 22,408,690 B (213 wakes) / ≈ 12.53 MB (112 continuations). Published 'Delta file only' (4,488,335 B ≈ 35.8%) is the unprojected lossless delta file,\n"
        "parallel to Progressive (7,285,951 B = 32.5%); stacking both in Prog+Delta first-read drops the entry payload to ≈ 3.0% (body) / ≈ 5.0% (CLI)."
    )
    fig.text(.03, .03, footer, fontsize=9.6, color="#475569", linespacing=1.55)
    save(fig, f"images/context-delivery/multitask-byte-ratios-{language}.svg")

    stride = DATA["stride"]
    rows = stride["transitions"] + [dict(action="Latest Context ON", total=17, improved=12, flat=5, regressed=0, unreconciled=0)]
    fig, (left, right) = plt.subplots(1, 2, figsize=(13.8, 8), gridspec_kw={"width_ratios": [1.35, 1]})
    fig.subplots_adjust(left=.16,right=.97,bottom=.16,top=.76,wspace=.35)
    origin = [0.]*len(rows)
    for field, color, label in [("improved",TEAL,"Improved"),("flat",BLUE,"Flat"),
                                ("regressed","#dc2626","Reported regression"),("unreconciled","#d97706","Unreconciled")]:
        values = [100*r[field]/r["total"] for r in rows]
        left.barh(range(4),values,left=origin,color=color,label=label,height=.52)
        for index, value in enumerate(values):
            if value >= 8:
                left.text(origin[index]+value/2,index,f"{value:.1f}%",ha="center",va="center",color="white",fontsize=11)
        origin = [old+value for old,value in zip(origin,values)]
    left.set_yticks(range(4), [f"{r['action']} · {r['total']}\n{r['improved']} / {r['flat']} / {r['regressed']} / {r['unreconciled']}" for r in rows], fontsize=10)
    left.invert_yaxis()
    left.set_xlim(0,100)
    left.set_xlabel("已报告转移的占比 (%)" if zh else "Share of reported transitions (%)")
    left.set_title("A. 分数结果；未对账项单列" if zh else "A. Score outcomes · unknowns stay visible", loc="left", pad=18)
    left.grid(axis="x",color="#e2e8f0",linewidth=.7)
    left.set_axisbelow(True)
    left.tick_params(length=0)
    right.axis("off")
    right.set_title("B. Yardstick：保留逐案例分母" if zh else "B. Yardstick · case-specific evidence",loc="left",pad=18)
    cards = (["时间混杂\n表面一致 20/21；仅猜较晚提交也有 21/21。", "同父候选反转\n两个候选的方向均预测错：0/2。", "负例与超时边界\n64 对新种子挡住一个负候选；\n超时修复是案例，不能外推通用通过率。"] if zh else
        ["Time confounding\nApparent agreement: 20/21.\nLater-submission baseline: 21/21.", "Same-parent reversal\nBoth candidate directions reversed: 0/2.", "Negative gates and timeout cliffs\nA 64-pair fresh gate rejected one candidate.\nAn observed timeout repair is a case,\nnot a general success rate."])
    for index, text in enumerate(cards):
        right.text(.02,.96-index*.31,text,transform=right.transAxes,va="top",fontsize=11,
                   linespacing=1.7,bbox={"boxstyle":"round,pad=.7","facecolor":"#f8fafc","edgecolor":"#cbd5e1"})
    fig.suptitle("Stride 与 Yardstick：分开动作选择、分数转移和案例" if zh else "Stride and Yardstick: action choices, scored transitions and cases",fontsize=17,y=.96)
    v1 = "v1 只表示动作选择：37 次复盘，grow 4 / hold 0 / shrink 33 (89.2%)。" if zh else "v1 is action selection only: 37 reviews, grow 4 / hold 0 / shrink 33 (89.2%)."
    fig.text(.16,.84,v1,fontsize=11,color="#475569")
    fig.legend(*left.get_legend_handles_labels(),loc="lower center",bbox_to_anchor=(.51,.085),ncol=4,frameon=False,fontsize=10)
    footer = ("v2：372 条记录、249 个已报告转移；公开分类未对账 7 项，不补造成持平或退化。最近：25 次复盘、17 个有分数转移。\n行标签顺序：改善／持平／已报告退化／未对账。公开聚合复算，不证明因果收益。" if zh else
        "v2: 372 records, 249 reported transitions; 7 outcomes unreconciled from published counts. Latest: 25 reviews, 17 scored transitions.\nRow counts: improved / flat / reported regression / unreconciled. Published aggregates do not establish causal benefit.")
    fig.text(.03,.025,footer,fontsize=10,color="#475569",linespacing=1.6)
    save(fig, f"images/edgebench-feedback-and-memory/stride-yardstick-closed-loop-{language}.svg")

    # 4. EdgeBench Controlled Two-Arm Explore + Stride: context-on vs context-off score comparison (10 tasks)
    two_arm = DATA["stride_context_on_off_comparison"]
    arm_tasks = {row["task"]: row for row in two_arm["tasks"]}
    ordered_norm_names = ["ANN", "FFmpeg", "VLIW", "Grid", "Ad", "Rust", "OpenTTD", "Codec", "Portfolio"]
    norm_rows = [arm_tasks[name] for name in ordered_norm_names]

    fig = plt.figure(figsize=(14.6, 7.8))
    gs = fig.add_gridspec(
        1, 2, width_ratios=[1.25, 1.05],
        left=.18, right=.98, bottom=.175, top=.805, wspace=.11,
    )
    left = fig.add_subplot(gs[0, 0])
    right = fig.add_subplot(gs[0, 1])

    y_pos = list(range(len(norm_rows)))
    on_vals = [r["context_on_norm"] for r in norm_rows]
    off_vals = [r["context_off_norm"] for r in norm_rows]
    off_bar_color = "#94a3b8"

    left.barh([y - .18 for y in y_pos], on_vals, height=.32,
              color=TEAL, label=("Explore + Stride + context-on" if not zh else "Explore + Stride + context-on（开启上下文交付）"))
    left.barh([y + .18 for y in y_pos], off_vals, height=.32,
              color=off_bar_color, label=("Explore + Stride + context-off" if not zh else "Explore + Stride + context-off（关闭上下文交付）"))

    tag_map_zh = {
        "ANN": "ANN (QPS↑ · 历史7臂Stride)",
        "FFmpeg": "FFmpeg (原生↑ · OFF早停)",
        "VLIW": "VLIW (周期↓ · OFF 14h45m)",
        "Grid": "Grid (总分↓ · 等时长 ~18h)",
        "Ad": "Ad (总分↑ · OFF 8h47m)",
        "Rust": "Rust (原生↑ · 等时长 ~18h)",
        "OpenTTD": "OpenTTD (原生↑ · 均<18h)",
        "Codec": "Codec (原生↑ · OFF 12h38m)",
        "Portfolio": "Portfolio (分↑ · 等时长 18h)",
    }
    tag_map_en = {
        "ANN": "ANN (QPS↑ · 7-arm Stride)",
        "FFmpeg": "FFmpeg (score↑ · OFF early stop)",
        "VLIW": "VLIW (cycles↓ · OFF 14h45m)",
        "Grid": "Grid (cost↓ · ~18h matched)",
        "Ad": "Ad (score↑ · OFF 8h47m)",
        "Rust": "Rust (score↑ · ~18h matched)",
        "OpenTTD": "OpenTTD (score↑ · both <18h)",
        "Codec": "Codec (score↑ · OFF 12h38m)",
        "Portfolio": "Portfolio (score↑ · 18h matched)",
    }
    left.set_yticks(y_pos, [(tag_map_zh if zh else tag_map_en)[r["task"]] for r in norm_rows], fontsize=9.7)
    left.invert_yaxis()
    left.set_xlim(0, 134)
    left.set_xticks([0, 25, 50, 75, 100])
    left.set_xlabel("EdgeBench 百分制归一分 (/100，越高越优；含 ↓ 指标已方向归一化)" if zh else "EdgeBench normalized score (/100, higher is better)", fontsize=9.5)
    left.set_title(
        "A. 固定 Stride 下 context-on vs context-off 百分制对照" if zh else "A. Fixed Stride: context-on vs context-off (/100)",
        loc="left", pad=14, fontsize=11.0, fontweight="bold",
    )
    left.set_axisbelow(True)
    left.grid(axis="x", color="#e2e8f0", linewidth=.7)
    left.tick_params(axis="both", length=0)

    for idx, r in enumerate(norm_rows):
        diff = r["context_on_norm"] - r["context_off_norm"]
        anchor = max(r["context_on_norm"], r["context_off_norm"])
        if r["task"] == "Rust":
            txt = f"{r['context_on_norm']:.2f} vs {r['context_off_norm']:.2f} (+0.0044)"
            col = TEAL
            fw = "bold"
        elif diff > 0:
            txt = f"+{diff:.2f} ({r['context_on_norm']:.2f} vs {r['context_off_norm']:.2f})"
            col = TEAL
            fw = "bold"
        else:
            txt = f"{diff:.2f} ({r['context_on_norm']:.2f} vs {r['context_off_norm']:.2f})"
            col = "#b45309"
            fw = "bold"
        left.text(anchor + 2.2, idx, txt, va="center", fontsize=8.8, fontweight=fw, color=col)

    right.axis("off")
    right.set_title(
        "B. 原生关键差值（含 Borden）与反例边界" if zh else "B. Native score deltas (Borden) & counterexamples",
        loc="left", pad=14, fontsize=11.0, fontweight="bold",
    )
    borden = arm_tasks["Borden"]
    cards_two_arm = (
        [
            (
                "第 10 题（无 /100 标度）：Borden 等时长 ~18h\n"
                f"• context-on（18h02m）：{borden['context_on_native']}（17 次有效）\n"
                f"• context-off（17h59m）：{borden['context_off_native']}（17 次有效）\n"
                "→ +35 分（+77.8%，同构等时长显著拉升）。",
                "#f0fdfa", "#99f6e4",
            ),
            (
                "关键原生分数增幅（固定 Explore + Stride）\n"
                "• ANN (QPS ↑)：7,159 vs 3,746（+91.1%, +33.51 百分分）\n"
                "• FFmpeg (原生 ↑)：2.7086 vs 1.9929（+35.9%, OFF 早停）\n"
                "• VLIW (周期 ↓)：1,042 vs 1,097（−55 周期 / −5.0%）\n"
                "• Grid (代价 ↓)：2,197 vs 2,311（−114 / −4.9%, ~18h）",
                "#f8fafc", "#cbd5e1",
            ),
            (
                "10 题总盘：ON 7 项占先 vs OFF 3 项领先\n"
                "• 等时长 ~18h（4 题）：3 胜（Borden/Grid/Rust 微先），\n"
                "  1 反例（Portfolio 13.62 vs 39.44）\n"
                "• 非等时长或历史对照（6 题）：ON 领先 4 题，\n"
                "  OFF 领先 2 题（Codec 70.79 / OpenTTD 33.09M）",
                "#fffbeb", "#fcd34d",
            ),
        ]
        if zh else
        [
            (
                "Task #10 (no /100 scale): Borden (~18h matched)\n"
                f"• context-on (18h02m): {borden['context_on_native']} (17 valid)\n"
                f"• context-off (17h59m): {borden['context_off_native']} (17 valid)\n"
                "→ +35 pts (+77.8%, duration-matched gain).",
                "#f0fdfa", "#99f6e4",
            ),
            (
                "Key native deltas under fixed Explore + Stride\n"
                "• ANN (QPS ↑): 7,159 vs 3,746 (+91.1%, +33.51 pts)\n"
                "• FFmpeg (score ↑): 2.7086 vs 1.9929 (+35.9%, OFF early stop)\n"
                "• VLIW (cycles ↓): 1,042 vs 1,097 (−55 cycles / −5.0%)\n"
                "• Grid (cost ↓): 2,197 vs 2,311 (−114 / −4.9%, ~18h)",
                "#f8fafc", "#cbd5e1",
            ),
            (
                "10-task summary: 7 ON ahead/edge vs 3 OFF ahead\n"
                "• Matched ~18h (4): 3 ON wins (Borden/Grid/Rust),\n"
                "  1 counterexample (Portfolio 13.62 vs 39.44)\n"
                "• Historical / <18h (6): 4 ON ahead, 2 OFF ahead\n"
                "  (Codec 68.89 vs 70.79; OpenTTD 30.27M vs 33.09M)",
                "#fffbeb", "#fcd34d",
            ),
        ]
    )
    y_starts = [.98, .63, .28]
    for y_box, (txt, face, edge) in zip(y_starts, cards_two_arm):
        right.text(.01, y_box, txt, transform=right.transAxes, va="top", fontsize=9.1,
                   linespacing=1.50, bbox={"boxstyle": "round,pad=.60", "facecolor": face, "edgecolor": edge})

    fig.suptitle(
        "EdgeBench 十题同构效果对照：固定 Explore + Stride 下的 Context ON vs OFF"
        if zh else
        "EdgeBench 10-task controlled comparison: Context ON vs OFF under Explore + Stride",
        fontsize=14.6, fontweight="bold", y=.955,
    )
    sub_two_arm = (
        "固定 Explore + Stride 框架（context-on 开启 Progressive 与周期内差分）。10 题中 context-on 7 题占先，完整保留 3 项反例与真实时长差。"
        if zh else
        "Holding Explore + Stride constant (context-on adds progressive reads + cycle deltas). Ahead on 7/10 tasks, retaining 3 counterexamples and runtime gaps."
    )
    fig.text(.5, .88, sub_two_arm, ha="center", fontsize=9.9, color="#475569")
    fig.legend(*left.get_legend_handles_labels(), loc="lower center", ncol=2, frameon=False, bbox_to_anchor=(.45, .078), fontsize=9.6)
    footer_two_arm = (
        "口径说明：截取自 EdgeBench 同构两臂实测（2026-11-12 22:40 CST 快照）。Borden 无百分制换算故在右栏 B 单列原生分（80 vs 45）。\n"
        "Grid、Borden、Rust、Portfolio 四题双臂均已跑满约 18h（15m 容差内）；ANN 取自历史七臂对应 Stride 配置；其余 5 题存在至少一臂未满 18h。本图仅检验开关的组内方向性效果。"
        if zh else
        "Basis: EdgeBench controlled two-arm snapshot at 2026-11-12 22:40 CST. Borden has no /100 scale and appears in native points in Panel B (80 vs 45).\n"
        "Grid, Borden, Rust, and Portfolio are ~18h duration-matched (within 15m); ANN uses the matching Stride pair from the 7-arm study; remaining 5 pairs have <18h runs. Checks within-harness direction only."
    )
    fig.text(.03, .018, footer_two_arm, fontsize=8.9, color="#475569", linespacing=1.45)
    save(fig, f"images/context-delivery/stride-context-on-vs-off-{language}.svg")
