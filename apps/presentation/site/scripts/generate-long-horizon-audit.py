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
mpl.rcParams.update({"svg.fonttype": "none", "svg.hashsalt": "loopx-blog-audit",
    "font.family": "sans-serif", "font.sans-serif": ["PingFang SC", "Noto Sans CJK SC", "DejaVu Sans"],
    "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
    "axes.spines.bottom": False, "axes.labelcolor": "#334155", "text.color": "#0f172a"})
BLUE, TEAL, PURPLE, GRAY = "#2563eb", "#0f766e", "#7c3aed", "#94a3b8"


def save(fig, relative):
    path = BLOG / relative
    fig.savefig(path, format="svg", facecolor="white", metadata={"Date": None})
    path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n")
    plt.close(fig)


for language in ("en", "zh"):
    zh = language == "zh"
    rows = DATA["context"]["tasks"]
    fig, axes = plt.subplots(1, 2, figsize=(13.8, 6.8), gridspec_kw={"width_ratios": [1.45, 1]})
    fig.subplots_adjust(left=.17, right=.97, bottom=.22, top=.80, wspace=.48)
    left, right = axes
    series = [("Progressive · all wakes", [100*r["progressive_bytes"]/r["full_bytes"] for r in rows], BLUE),
              ("Delta · in-cycle", [r["delta_share_reported"] for r in rows], TEAL),
              ("First-read body · in-cycle", [r["first_body_share_reported"] for r in rows], PURPLE)]
    for index, (label, values, color) in enumerate(series):
        positions = [i + (index-1)*.23 for i in range(len(rows))]
        left.barh(positions, values, height=.20, label=label, color=color)
    left.set_yticks(range(len(rows)), [r["task"] for r in rows], fontsize=10)
    left.invert_yaxis()
    left.set_xlim(0, 55)
    left.set_xlabel("占同组 Full 字节 (%)" if zh else "Share of Full bytes within each cohort (%)")
    left.set_title("A. 逐题占比 · 分开标明唤醒组" if zh else "A. Task shares · cohorts stay explicit", loc="left", pad=18)
    totals = [100*sum(r["progressive_bytes"] for r in rows)/sum(r["full_bytes"] for r in rows)]
    denominator = DATA["context"]["delta_full_bytes_reported_approx"]
    totals += [100*sum(r[key] for r in rows)/denominator
               for key in ("delta_bytes", "first_body_bytes", "first_cli_bytes")]
    labels = ["Progressive · 213", "Delta · 112", "First body · 112", "First CLI · 112"]
    right.barh(range(4), totals, color=[BLUE, TEAL, PURPLE, GRAY], height=.52)
    right.set_yticks(range(4), labels, fontsize=10)
    right.invert_yaxis()
    right.set_xlim(0, 50)
    for index, value in enumerate(totals):
        right.text(value+1, index, f"{'≈ ' if index else ''}{value:.1f}%", va="center", fontsize=12)
    right.set_title("B. 各自分母的加权总计" if zh else "B. Weighted totals · separate denominators", loc="left", pad=18)
    for axis in axes:
        axis.set_axisbelow(True)
        axis.grid(axis="x", color="#e2e8f0", linewidth=.7)
        axis.tick_params(axis="both", length=0)
    fig.suptitle("八题上下文字节审计：先区分 213 次与 112 次唤醒" if zh else "Eight-task context byte audit: 213 wakes and 112 in-cycle wakes", fontsize=17, y=.96)
    fig.legend(*left.get_legend_handles_labels(), loc="lower center", ncol=3, frameon=False, bbox_to_anchor=(.53,.11), fontsize=10)
    footer = ("Progressive：7,285,951 / 22,408,690 B，覆盖 213 次唤醒。Delta／首读：仅覆盖 112 次，Full 分母原报告约 12.53 MB。\n聚合复算；原始逐 wake 分母未公开。不可将右图解释为覆盖 213 次的累积减量。" if zh else
        "Progressive: 7,285,951 / 22,408,690 B across 213 wakes. Delta/first-read: 112 wakes; reported Full denominator ≈ 12.53 MB.\nAggregate recalculation; exact per-wake denominators unavailable. The right panel is not a cumulative reduction across 213 wakes.")
    fig.text(.03,.035,footer,fontsize=10,color="#475569",linespacing=1.6)
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
