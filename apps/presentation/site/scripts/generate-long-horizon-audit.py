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
    # 1. Four-stage monotonic step-down cascade:
    #    All (100%) -> Progressive -> Progressive + Compaction-Cycle Structured Delta -> First Read
    orig_b = LESSON["native_projection"]["original_bytes"]
    prog_b = LESSON["cycle_codec"]["current_full_bytes"]
    pdelta_b = LESSON["cycle_codec"]["delta_bytes"]
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

    cascade_vals = [100.0, 100 * prog_b / orig_b, 100 * pdelta_b / orig_b, 100 * first_body_lesson / orig_b]
    cascade_labels = (
        [
            "1. All · 完整上下文",
            "2. Progressive · 渐进折叠",
            "3. Prog + 周期内结构化 Delta",
            "4. Prog + Delta First Read",
        ]
        if zh else
        [
            "1. All · Full source",
            "2. Progressive · Fold tail",
            "3. Prog + In-cycle Delta",
            "4. Prog + Delta First read",
        ]
    )
    cascade_notes = (
        [
            f"100.0% ({orig_b:,} B)",
            f"{cascade_vals[1]:.1f}% ({prog_b:,} B · 按需折叠长尾)",
            f"{cascade_vals[2]:.1f}% ({pdelta_b:,} B · 较 Prog 再减 −61.0%)",
            f"{cascade_vals[3]:.1f}% ({first_body_lesson:,} B 正文 / {100*first_cli_lesson/orig_b:.1f}% CLI)",
        ]
        if zh else
        [
            f"100.0% ({orig_b:,} B)",
            f"{cascade_vals[1]:.1f}% ({prog_b:,} B · fold long tail)",
            f"{cascade_vals[2]:.1f}% ({pdelta_b:,} B · −61.0% vs Prog)",
            f"{cascade_vals[3]:.1f}% ({first_body_lesson:,} B body / {100*first_cli_lesson/orig_b:.1f}% CLI)",
        ]
    )
    left.barh(range(4), cascade_vals, color=[SLATE, BLUE, TEAL, PURPLE], height=.50)
    left.set_yticks(range(4), cascade_labels, fontsize=10.2)
    left.invert_yaxis()
    left.set_xlim(0, 168)
    left.set_xlabel("占完整原始捕获字节 (%)" if zh else "Share of complete source bytes (%)", fontsize=9.8)
    left.set_title(
        "A. 同源流水线四阶叠加削减（固定基准）" if zh else "A. Same-source 4-stage cascade",
        loc="left", pad=14, fontsize=11.5, fontweight="bold",
    )
    for idx, (val, note) in enumerate(zip(cascade_vals, cascade_notes)):
        weight = "bold" if idx == 2 else "normal"
        color = "#0f766e" if idx == 2 else "#0f172a"
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
        "从 100% 逐层削减：All → Progressive → 压缩周期内结构化 Delta → First Read"
        if zh else
        "Step-Down from 100%: All → Progressive → Compaction-Cycle Structured Delta → First Read",
        fontsize=15.0, fontweight="bold", y=.955,
    )
    sub = (
        "渐进选读仅按需折叠历史长尾；核心机制是在有效 Compaction 周期内精确复用不变结构子树，并在压缩或失配时原子恢复完整基线。"
        if zh else
        "Progressive lookup folds long-tail history; the core mechanism reuses verified subtrees within a compaction epoch and resets on compaction."
    )
    fig.text(.5, .87, sub, ha="center", fontsize=10.2, color="#475569")
    footer_cascade = (
        "A：同源固定流水线（measurements.json，同一 57,014 B 分母）。B1：ANN 18h 配对轨迹在选中包（3.09 MB）上叠加周期内结构化 Delta 的进一步削减。\n"
        "B2：八题 213 次唤醒（Progressive）与 112 次同周期续跑（Prog+Delta 首读）各自相对原分母的比例；跨队列分母分开列出，不做混乘。"
        if zh else
        "A: exact same-source pipeline (measurements.json, single 57,014 B denominator). B1: ANN 18h paired trajectory stacking compaction-cycle Delta on selected packets.\n"
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
