<script setup lang="ts">
/**
 * A17 融资力预测（FE-15）
 *
 * 两张图回答两个不同问题：
 *   ① 概率校准曲线 —— 模型说"三成会融资"，实际是不是三成？横轴是预测、纵轴是观测，
 *      45° 才是准。十字须是 Wilson 95% 区间，不是装饰：每箱只有约 100 家、事件 20 上下，
 *      不给区间就会把抽样噪声当成"模型偏了"。
 *   ② 估值区间条 —— P10/P50/P90 的横向区间，比"预测值 8650 万"这种单点结论诚实得多。
 *      条越长说明模型越不敢下结论；同一个模型对不同阶段的宽度并不一样。
 *
 * 这一块**不出现任何字段代号**：融资模型的特征在契约里没有逐字段展开（只有汇总指标），
 * 所以不需要 RedLineBanner 覆盖到 A17；语义未知的说明只挂在 A16 的 Cox 表上。
 */
import { computed } from "vue";

import ChartPanel from "@/components/ChartPanel.vue";
import type { DatasetKind } from "@/types";
import type {
  CalibrationBin,
  FinancingContract,
  ValuationBar,
} from "@/types/contracts";
import { calibrationPoints, intervalWidth } from "@/utils/forecast";

const props = defineProps<{
  data: FinancingContract;
  dataset: DatasetKind;
  source: string;
}>();

const cal = computed(() => props.data.calibration);
const val = computed(() => props.data.valuation);

const pct = (v: number, d = 1) => `${(v * 100).toFixed(d)}%`;
const wan = (v: number) => `${v.toLocaleString("zh-CN")}`;

/** 校准图两个轴同量程，45° 才成立；上限取最大预测概率上取整到 0.1 */
const axisMax = computed(() => {
  const m = Math.max(
    ...cal.value.bins.map((b) => b.p_mean),
    cal.value.prevalence,
  );
  return Math.min(1, Math.ceil((m + 0.05) * 10) / 10);
});

const calOption = computed(() => {
  const pts = calibrationPoints(cal.value.bins);
  return {
    animation: false,
    grid: { left: 58, right: 26, top: 26, bottom: 44 },
    tooltip: {
      trigger: "item",
      formatter: (p: { dataIndex: number }) => {
        const b: CalibrationBin | undefined = cal.value.bins[p.dataIndex];
        if (!b) return "";
        return [
          `<b>第 ${b.bin} 箱</b>（n=${b.n}）`,
          `预测均值 ${(b.p_mean * 100).toFixed(1)}%`,
          `实际发生 ${(b.obs * 100).toFixed(1)}%（95%CI ${(b.obs_lo * 100).toFixed(1)}–${(b.obs_hi * 100).toFixed(1)}）`,
        ].join("<br/>");
      },
    },
    xAxis: {
      type: "value",
      min: 0,
      max: axisMax.value,
      name: "预测概率",
      nameLocation: "middle",
      nameGap: 26,
      nameTextStyle: { fontSize: 10, color: "#5b6673" },
      axisLabel: {
        formatter: (v: number) => `${Math.round(v * 100)}%`,
        fontSize: 10,
        color: "#5b6673",
      },
      splitLine: { lineStyle: { color: "#eef1f5" } },
    },
    yAxis: {
      type: "value",
      min: 0,
      max: axisMax.value,
      name: "实际发生率",
      nameTextStyle: { fontSize: 10, color: "#5b6673" },
      axisLabel: {
        formatter: (v: number) => `${Math.round(v * 100)}%`,
        fontSize: 10,
        color: "#5b6673",
      },
      splitLine: { lineStyle: { color: "#eef1f5" } },
    },
    series: [
      {
        // 完美校准参考线：45°
        type: "line",
        name: "完美校准",
        silent: true,
        showSymbol: false,
        lineStyle: { type: "dashed", color: "#b9c0c9", width: 1 },
        data: [
          [0, 0],
          [axisMax.value, axisMax.value],
        ],
        z: 1,
      },
      {
        // Wilson 区间须：custom renderItem 画竖线 + 上下帽
        type: "custom",
        name: "95%CI",
        silent: true,
        z: 2,
        data: pts,
        renderItem: (
          _params: unknown,
          api: { coord: (p: number[]) => number[] },
        ) => {
          const idx = (_params as { dataIndex: number }).dataIndex;
          const [x, , lo, hi] = pts[idx];
          const top = api.coord([x, hi]);
          const bottom = api.coord([x, lo]);
          const cap = 4;
          return {
            type: "group",
            children: [
              {
                type: "line",
                shape: { x1: top[0], y1: top[1], x2: bottom[0], y2: bottom[1] },
                style: { stroke: "#2f5d8a", lineWidth: 1, opacity: 0.55 },
              },
              {
                type: "line",
                shape: {
                  x1: top[0] - cap,
                  y1: top[1],
                  x2: top[0] + cap,
                  y2: top[1],
                },
                style: { stroke: "#2f5d8a", lineWidth: 1, opacity: 0.55 },
              },
              {
                type: "line",
                shape: {
                  x1: bottom[0] - cap,
                  y1: bottom[1],
                  x2: bottom[0] + cap,
                  y2: bottom[1],
                },
                style: { stroke: "#2f5d8a", lineWidth: 1, opacity: 0.55 },
              },
            ],
          };
        },
      },
      {
        type: "line",
        name: "观测发生率",
        showSymbol: true,
        symbolSize: 5,
        lineStyle: { color: "#b3312c", width: 1.6 },
        itemStyle: { color: "#b3312c" },
        data: pts.map((p) => [p[0], p[1]]),
        z: 3,
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { type: "dotted", color: "#8a939e", width: 1 },
          label: {
            formatter: `总体 ${pct(cal.value.prevalence)}`,
            fontSize: 10,
            color: "#5b6673",
            position: "insideEndTop",
          },
          data: [{ yAxis: cal.value.prevalence }],
        },
      },
    ],
  };
});

/* ------------------------------ 估值区间条 ------------------------------ */
/** 阶段色：本地有序渐变（浅 = 越早期，深 = 越后期），仅用于区分阶段，不是业务配色 */
const STAGE_COLOR: Record<string, string> = {
  s0: "#b9c0c9",
  s1: "#7d94ad",
  s2: "#4a6d92",
  s3: "#2f5d8a",
};
const stageColor = (id: string) => STAGE_COLOR[id] ?? "#7d94ad";

const bars = computed<ValuationBar[]>(() => val.value.bars ?? []);
const valOption = computed(() => {
  const rows = bars.value;
  const maxP90 = Math.max(...rows.map((r) => r.p90), 1000);
  return {
    animation: false,
    grid: { left: 132, right: 64, top: 22, bottom: 40 },
    tooltip: {
      trigger: "item",
      formatter: (p: { dataIndex: number }) => {
        const b = rows[p.dataIndex];
        if (!b) return "";
        return [
          `<b>${b.entity}</b> · ${b.stage_label}`,
          `P10 ${wan(b.p10)} / P50 ${wan(b.p50)} / P90 ${wan(b.p90)} ${val.value.unit}`,
          `区间宽度 ${b.width_ratio.toFixed(2)}×（P90/P10）`,
          `下一轮融资概率 ${pct(b.p_next)}`,
        ].join("<br/>");
      },
    },
    xAxis: {
      type: "value",
      min: 0,
      max: Math.ceil((maxP90 * 1.08) / 500) * 500,
      name: `投前估值（${val.value.unit}）`,
      nameLocation: "middle",
      nameGap: 26,
      nameTextStyle: { fontSize: 10, color: "#5b6673" },
      axisLabel: { fontSize: 10, color: "#5b6673" },
      splitLine: { lineStyle: { color: "#eef1f5" } },
    },
    yAxis: {
      type: "category",
      inverse: true,
      data: rows.map((b) => `${b.entity} · ${b.stage_label}`),
      axisLabel: { fontSize: 10.5, color: "#1f2933" },
      axisTick: { show: false },
    },
    series: [
      {
        type: "custom",
        name: "估值区间",
        data: rows.map((b) => [b.p10, b.p50, b.p90]),
        renderItem: (
          params: { dataIndex: number },
          api: {
            value: (i: number) => number;
            coord: (p: number[]) => number[];
            size: (p: number[]) => number[];
          },
        ) => {
          const row = rows[params.dataIndex];
          const y = api.coord([0, params.dataIndex])[1];
          const x10 = api.coord([api.value(0), params.dataIndex])[0];
          const x50 = api.coord([api.value(1), params.dataIndex])[0];
          const x90 = api.coord([api.value(2), params.dataIndex])[0];
          const h = Math.max(9, api.size([0, 1])[1] * 0.42);
          const color = stageColor(row.stage);
          return {
            type: "group",
            children: [
              {
                type: "rect",
                shape: {
                  x: x10,
                  y: y - h / 2,
                  width: Math.max(2, x90 - x10),
                  height: h,
                  r: 2,
                },
                style: { fill: color, opacity: 0.34 },
              },
              // 两端刻度：P10 / P90 是"区间"的两个端点，不是误差棒，所以画成实线端帽
              {
                type: "line",
                shape: { x1: x10, y1: y - h / 2, x2: x10, y2: y + h / 2 },
                style: { stroke: color, lineWidth: 1.4 },
              },
              {
                type: "line",
                shape: { x1: x90, y1: y - h / 2, x2: x90, y2: y + h / 2 },
                style: { stroke: color, lineWidth: 1.4 },
              },
              {
                type: "line",
                shape: {
                  x1: x50,
                  y1: y - h / 2 - 2,
                  x2: x50,
                  y2: y + h / 2 + 2,
                },
                style: { stroke: "#1f2933", lineWidth: 2 },
              },
              {
                type: "text",
                style: {
                  x: x90 + 6,
                  y,
                  text: wan(row.p50),
                  fill: "#3d4852",
                  fontSize: 10,
                  fontFamily: "monospace",
                  textVerticalAlign: "middle",
                },
              },
            ],
          };
        },
      },
    ],
  };
});

const totalEvents = computed(() => cal.value.events);
const brierSkill = computed(
  () => 1 - cal.value.brier / (cal.value.brier_baseline || 1),
);
</script>

<template>
  <div class="financing stack">
    <!-- 校准指标 -->
    <div class="grid-12 metrics">
      <div class="col-3 metric">
        <span class="metric__label">Brier 分数</span>
        <strong class="metric__value">{{ cal.brier.toFixed(3) }}</strong>
        <span class="metric__hint"
          >基准 {{ cal.brier_baseline.toFixed(3) }}（恒用总体率），技能分
          {{ (brierSkill * 100).toFixed(1) }}%</span
        >
      </div>
      <div class="col-3 metric">
        <span class="metric__label">期望校准误差 ECE</span>
        <strong class="metric__value">{{ cal.ece.toFixed(3) }}</strong>
        <span class="metric__hint"
          >十分位加权平均 |观测−预测|，每箱约
          {{ Math.round(cal.n / cal.bins.length) }} 家</span
        >
      </div>
      <div class="col-3 metric">
        <span class="metric__label">可靠性斜率</span>
        <strong class="metric__value">{{
          cal.reliability_slope.toFixed(2)
        }}</strong>
        <span class="metric__hint"
          >1.00 = 完全校准；&gt;1 偏自信、&lt;1 偏保守</span
        >
      </div>
      <div class="metric col-3">
        <span class="metric__label">判别力 AUC</span>
        <strong class="metric__value">{{ cal.auc.toFixed(3) }}</strong>
        <span class="metric__hint"
          >事件 {{ totalEvents }} / {{ cal.n }}（{{
            pct(cal.prevalence)
          }}）</span
        >
      </div>
    </div>

    <div class="grid-12">
      <div id="a17-calibration" class="col-7">
        <ChartPanel
          title="概率校准曲线（预测 vs 实际）"
          :dataset="dataset"
          :n-samples="cal.n"
          :noise-floor="null"
          :option="calOption"
          :height="380"
        >
          <p class="note" data-testid="calibration-derivation">
            45° 虚线是「说多少就是多少」。红线连接各箱观测值，竖须为 Wilson 95%
            区间—— 每箱约
            {{
              Math.round(cal.n / cal.bins.length)
            }}
            家、事件二十上下，没有须的话，
            上下两三家的抖动会被读成"模型偏了"。图中水平点线是总体发生率
            {{
              pct(cal.prevalence)
            }}：低于它的预测段说明模型把这家企业看得比平均更差。
          </p>
        </ChartPanel>
      </div>

      <div class="col-5">
        <section class="bins" aria-labelledby="bins-title">
          <header class="bins__head">
            <h3 id="bins-title" class="bins__title">分箱明细</h3>
            <span class="bins__hint">按预测概率等频十分位</span>
          </header>
          <div class="table-scroll">
            <table class="tbl" data-testid="calibration-bins">
              <thead>
                <tr>
                  <th scope="col">#</th>
                  <th scope="col">n</th>
                  <th scope="col">预测</th>
                  <th scope="col">观测</th>
                  <th scope="col">95% CI</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="b in cal.bins" :key="b.bin">
                  <td class="mono">{{ b.bin }}</td>
                  <td class="mono">{{ b.n }}</td>
                  <td class="mono">{{ pct(b.p_mean) }}</td>
                  <td class="mono">{{ pct(b.obs) }}</td>
                  <td class="mono subtle">
                    {{ pct(b.obs_lo) }}–{{ pct(b.obs_hi) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>

    <!-- 估值区间 -->
    <section
      id="a17-valuation"
      class="valuation"
      aria-labelledby="valuation-title"
      data-testid="valuation-block"
    >
      <header class="valuation__head">
        <h3 id="valuation-title" class="valuation__title">
          下一轮估值区间（P10 / P50 / P90）
        </h3>
        <span class="valuation__hint">
          {{ val.horizon }} · {{ val.target }} · 单位 {{ val.unit }} · 竖线 =
          P50
        </span>
      </header>

      <ChartPanel
        title="估值区间条（按 P50 降序）"
        :dataset="dataset"
        :n-samples="val.bars.length"
        :noise-floor="null"
        :option="valOption"
        :height="330"
      >
        <p class="note">
          条的长度就是不确定性：P90/P10 中位数
          {{ val.summary.width_ratio_median.toFixed(1) }}×——
          同一家企业，模型给出的估值上下能差这么多。只报 P50
          等于把这条全部藏起来。 企业标识是匿名编号（P0-1
          映射键未到位），阶段标签沿用 A15 的占位口径。
        </p>
      </ChartPanel>

      <div class="table-scroll">
        <table class="tbl tbl--val" data-testid="valuation-table">
          <thead>
            <tr>
              <th scope="col">编号</th>
              <th scope="col">阶段</th>
              <th scope="col">P10</th>
              <th scope="col">P50</th>
              <th scope="col">P90</th>
              <th scope="col">P90/P10</th>
              <th scope="col">下一轮概率</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="b in bars" :key="b.entity">
              <th scope="row" class="mono">{{ b.entity }}</th>
              <td>{{ b.stage_label }}</td>
              <td class="mono">{{ wan(b.p10) }}</td>
              <td class="mono strong">{{ wan(b.p50) }}</td>
              <td class="mono">{{ wan(b.p90) }}</td>
              <td class="mono">{{ intervalWidth(b).toFixed(2) }}×</td>
              <td class="mono">{{ pct(b.p_next) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.metrics {
  gap: var(--space-3);
}

.metric {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface-muted);
}

.metric__label {
  font-size: 11px;
  color: var(--color-text-muted);
}

.metric__value {
  font-family: var(--font-mono);
  font-size: 19px;
  font-weight: 700;
  line-height: 1.35;
}

.metric__hint {
  font-size: 10.5px;
  line-height: 1.6;
  color: var(--color-text-subtle);
}

.note {
  margin-top: var(--space-2);
  font-size: 11.5px;
  line-height: 1.75;
  color: var(--color-text-muted);
}

.bins {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  height: 100%;
  padding: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.bins__head {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
}

.bins__title {
  font-size: 14px;
  font-weight: 600;
}

.bins__hint {
  margin-left: auto;
  font-size: 11px;
  color: var(--color-text-subtle);
}

.tbl {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.tbl th,
.tbl td {
  padding: 5px 7px;
  border-bottom: 1px solid var(--color-border);
  text-align: right;
  white-space: nowrap;
}

.tbl th[scope="row"],
.tbl th[scope="col"]:first-child {
  text-align: left;
}

.tbl thead th {
  font-weight: 600;
  color: var(--color-text-muted);
  background: var(--color-surface-muted);
}

.tbl--val td:nth-child(2) {
  text-align: left;
  color: var(--color-text-muted);
}

.mono {
  font-family: var(--font-mono);
}

.subtle {
  font-size: 11px;
  color: var(--color-text-subtle);
}

.strong {
  font-weight: 700;
}

.table-scroll {
  overflow-x: auto;
}

.valuation {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.valuation__head {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.valuation__title {
  font-size: 13.5px;
  font-weight: 600;
}

.valuation__hint {
  font-size: 11px;
  color: var(--color-text-subtle);
}
</style>
