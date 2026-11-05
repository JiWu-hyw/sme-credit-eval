<script setup lang="ts">
/**
 * A16 生存力预测（FE-15）
 *
 * 三块内容、一个状态机：
 *   ① KM 生存曲线 —— 3 个风险层 × 25 个月，每层带 Greenwood 95% 置信带；
 *   ② 风险集表 —— 惯例的 at-risk 行，别让人以为曲线到 24 月还有 339 人在随访；
 *   ③ Cox 系数表 —— β / HR / 95%CI / z / p，外加一条对数尺度的森林条。
 *
 * 这一块的阅读门槛在于「S(t) 高不等于安全」：
 * 生存率高是因为 24 个月里事件少（109/1017，10.7%），不是因为模型判断企业都很好。
 * 所以摘要卡里同时给 S(24) 与它的置信区间 —— 单看 0.77 会高估精度。
 *
 * 分层切换只改**看哪几条**，不重算任何数字：曲线、CI、风险集都来自契约。
 * 若切分层就重算，图上的带子会和契约里的 s24_ci 对不上，那才是真的错。
 */
import { computed, ref } from "vue";

import ChartPanel from "@/components/ChartPanel.vue";
import UnknownFieldTag from "@/components/UnknownFieldTag.vue";
import type { DatasetKind } from "@/types";
import type {
  CoxCovariate,
  SurvivalContract,
  SurvivalStratum,
} from "@/types/contracts";
import {
  atRiskAt,
  bandPolygon,
  formatP,
  hrPercent,
  logSpan,
  survivalAt,
} from "@/utils/forecast";

const props = defineProps<{
  data: SurvivalContract;
  dataset: DatasetKind;
  source: string;
}>();

type StratumKey = "all" | string;
const active = ref<StratumKey>("all");
const showBand = ref(true);

const strata = computed<SurvivalStratum[]>(() => props.data.strata ?? []);
const curveOf = (id: string) =>
  props.data.curves.find((c) => c.stratum_id === id);

const visibleStrata = computed(() =>
  active.value === "all"
    ? strata.value
    : strata.value.filter((s) => s.stratum_id === active.value),
);

/** 当前选中层的 12/24 月读数；选「全部」时给整体的加权（按层人数）估计 */
const headline = computed(() => {
  if (active.value !== "all") {
    const s = strata.value.find((x) => x.stratum_id === active.value);
    return s
      ? {
          label: s.stratum_label,
          s12: s.s12,
          ci12: s.s12_ci,
          s24: s.s24,
          ci24: s.s24_ci,
        }
      : null;
  }
  const total = strata.value.reduce((n, s) => n + s.n, 0) || 1;
  const s12 = strata.value.reduce((v, s) => v + s.s12 * s.n, 0) / total;
  const s24 = strata.value.reduce((v, s) => v + s.s24 * s.n, 0) / total;
  const lo12 = strata.value.reduce((v, s) => v + s.s12_ci[0] * s.n, 0) / total;
  const hi12 = strata.value.reduce((v, s) => v + s.s12_ci[1] * s.n, 0) / total;
  const lo24 = strata.value.reduce((v, s) => v + s.s24_ci[0] * s.n, 0) / total;
  const hi24 = strata.value.reduce((v, s) => v + s.s24_ci[1] * s.n, 0) / total;
  return {
    label: "全部（按人数加权）",
    s12,
    ci12: [lo12, hi12] as [number, number],
    s24,
    ci24: [lo24, hi24] as [number, number],
  };
});

const atRiskTicks = [0, 6, 12, 18, 24];

/**
 * 纵轴下界：按数据自适应，而不是死守 0–100%。
 *
 * 这里踩过一次：1017 例里只有 109 例事件，三条曲线全部挤在 77%–100% 之间，
 * 纵轴 0–100% 时置信带只有几个像素高，"曲线含置信区间"这条验收在视觉上等于没交付。
 * 但也不能无脑贴到数据底部 —— S(t) 的绝对水位本身是信息（77% 就是 77%），
 * 所以留 5% 余量后**向下取整到 10%**，下限不低于 0.5：
 *   高风险层最低 0.75 → 轴下限 0.70，带子立刻有形状；
 *   若将来事件率上去了、曲线压到 0.5 以下，轴自然回到 0–100%，不会出现"截断夸大差异"。
 */
const yMin = computed(() => {
  const lows = visibleStrata.value
    .map((s) => curveOf(s.stratum_id))
    .filter((c): c is NonNullable<typeof c> => !!c)
    .flatMap((c) => c.points.map((p) => p[2]));
  const floor = props.data.cohort?.observed_event_rate
    ? Math.min(1 - props.data.cohort.observed_event_rate, 0.98)
    : 1;
  const lowest = Math.min(...(lows.length ? lows : [floor]));
  const padded = lowest - 0.05;
  return Math.max(0, Math.min(0.5, Math.floor(padded * 10) / 10));
});

/** 轴刻度间隔：轴跨度大就 20%，小就 5%，避免出现 5 条挤在一起的网格线 */
const yInterval = computed(() =>
  1 - yMin.value > 0.45 ? 0.2 : 1 - yMin.value > 0.25 ? 0.1 : 0.05,
);
const atRiskRows = computed(() =>
  strata.value.map((s) => {
    const curve = curveOf(s.stratum_id);
    return {
      id: s.stratum_id,
      label: s.stratum_label,
      color: s.color,
      events: s.events,
      n: s.n,
      cells: atRiskTicks.map((t) => (curve ? atRiskAt(curve.points, t) : 0)),
    };
  }),
);

const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

/* --------------------------------- KM 曲线 --------------------------------- */
const kmOption = computed(() => {
  const bands = visibleStrata.value
    .map((s) => {
      const curve = curveOf(s.stratum_id);
      if (!curve) return null;
      const isActive = active.value === s.stratum_id;
      return {
        type: "custom",
        name: `${s.stratum_label} 95%CI`,
        silent: true,
        z: 1,
        data: [0],
        renderItem: (
          _params: unknown,
          api: { coord: (p: number[]) => number[] },
        ) => {
          const pts = bandPolygon(curve.points);
          return {
            type: "polygon",
            shape: { points: pts.map((p) => api.coord([p[0], p[1]])) },
            style: {
              fill: s.color,
              opacity: isActive ? 0.24 : active.value === "all" ? 0.1 : 0.05,
            },
          };
        },
      };
    })
    .filter(Boolean);

  const lines = visibleStrata.value
    .map((s) => {
      const curve = curveOf(s.stratum_id);
      if (!curve) return null;
      return {
        type: "line",
        name: s.stratum_label,
        step: "end",
        showSymbol: false,
        smooth: false,
        z: 3,
        lineStyle: {
          width:
            active.value === "all" || active.value === s.stratum_id ? 2 : 1,
          color: s.color,
          opacity:
            active.value === "all" || active.value === s.stratum_id ? 1 : 0.35,
        },
        itemStyle: { color: s.color },
        data: curve.points.map((p) => [p[0], p[1]]),
        emphasis: { focus: "series" },
        markLine:
          s.stratum_id === visibleStrata.value[0]?.stratum_id
            ? {
                silent: true,
                symbol: "none",
                lineStyle: { type: "dashed", color: "#b9c0c9", width: 1 },
                label: {
                  formatter: (p: { value: number }) => `${p.value} 月`,
                  fontSize: 10,
                  color: "#5b6673",
                },
                data: [
                  { xAxis: 12 },
                  { xAxis: props.data.cohort.horizon_months },
                ],
              }
            : undefined,
      };
    })
    .filter(Boolean);

  return {
    animation: false,
    grid: { left: 56, right: 118, top: 28, bottom: 44 },
    legend: {
      right: 8,
      top: 6,
      orient: "vertical",
      itemWidth: 10,
      itemHeight: 8,
      textStyle: { fontSize: 11, color: "#3d4852" },
      data: visibleStrata.value.map((s) => s.stratum_label),
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", lineStyle: { color: "#c9d0d8" } },
      formatter: (
        params: Array<{
          seriesName: string;
          data: number[];
          axisValue: number;
        }>,
      ) => {
        const t = params[0]?.axisValue;
        const rows = visibleStrata.value
          .map((s) => {
            const curve = curveOf(s.stratum_id);
            if (!curve) return "";
            const v = survivalAt(curve.points, t);
            if (!v) return "";
            return `<div style="margin-top:2px"><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${s.color};margin-right:5px"></span>${s.stratum_label}：${(v.s * 100).toFixed(1)}%（95%CI ${(v.lo * 100).toFixed(1)}–${(v.hi * 100).toFixed(1)}）</div>`;
          })
          .join("");
        return `<b>第 ${t} 个月</b>${rows}<div style="margin-top:4px;color:#8a939e">S(t) 为乘积极限估计，区间用 Greenwood 方差</div>`;
      },
    },
    xAxis: {
      type: "value",
      min: 0,
      max: props.data.cohort.horizon_months,
      interval: 3,
      name: props.data.axis.x.label,
      nameLocation: "middle",
      nameGap: 26,
      nameTextStyle: { fontSize: 10, color: "#5b6673" },
      axisLabel: { fontSize: 10, color: "#5b6673" },
      splitLine: { lineStyle: { color: "#eef1f5" } },
    },
    yAxis: {
      type: "value",
      min: yMin.value,
      max: 1,
      interval: yInterval.value,
      name: props.data.axis.y.label,
      nameTextStyle: { fontSize: 10, color: "#5b6673" },
      axisLabel: {
        formatter: (v: number) => `${Math.round(v * 100)}%`,
        fontSize: 10,
        color: "#5b6673",
      },
      splitLine: { lineStyle: { color: "#eef1f5" } },
    },
    series: [...(showBand.value ? bands : []), ...lines],
  };
});

/*
 * 置信带用 `api.coord` 取像素坐标，**不能**自己按 1000×420 硬算：
 * 卡片是 12 列栅格里的 8 列，宽度随视口变，硬算出来的多边形在 1280 屏上会整体右移。
 * 这条踩过一次，带子比曲线偏了半个网格。
 */

/* -------------------------------- Cox 森林条 -------------------------------- */
const coxRows = computed<CoxCovariate[]>(() => props.data.cox.covariates ?? []);
const hrSpan = computed(() => {
  const rows = coxRows.value;
  if (!rows.length) return { min: -0.1, max: 0.1 };
  return logSpan(
    Math.min(...rows.map((r) => r.hr_lo)),
    Math.max(...rows.map((r) => r.hr_hi)),
  );
});
const refPercent = computed(() => hrPercent(1, hrSpan.value));

function barStyle(r: CoxCovariate) {
  const lo = hrPercent(r.hr_lo, hrSpan.value);
  const hi = hrPercent(r.hr_hi, hrSpan.value);
  const mid = hrPercent(r.hr, hrSpan.value);
  return {
    left: `${Math.min(lo, hi)}%`,
    width: `${Math.max(Math.abs(hi - lo), 0.8)}%`,
    dot: `${mid}%`,
    tone: r.hr >= 1 ? "risk" : "protect",
  };
}

const chipLabel = (s: SurvivalStratum) => `${s.stratum_label} · n=${s.n}`;
</script>

<template>
  <div class="survival stack">
    <!-- 分层切换 -->
    <div class="toolbar">
      <div class="toolbar__group" role="group" aria-label="选择风险分层">
        <span class="toolbar__label">分层</span>
        <button
          type="button"
          class="chip"
          :class="{ 'is-active': active === 'all' }"
          :aria-pressed="active === 'all'"
          data-testid="survival-stratum-chip-all"
          @click="active = 'all'"
        >
          全部分层
        </button>
        <button
          v-for="s in strata"
          :key="s.stratum_id"
          type="button"
          class="chip chip--stratum"
          :class="{ 'is-active': active === s.stratum_id }"
          :aria-pressed="active === s.stratum_id"
          :data-stratum="s.stratum_id"
          :data-testid="`survival-stratum-chip-${s.stratum_id}`"
          @click="active = s.stratum_id"
        >
          <span
            class="chip__dot"
            :style="{ background: s.color }"
            aria-hidden="true"
          />
          {{ chipLabel(s) }}
        </button>
      </div>

      <label class="toggle">
        <input
          v-model="showBand"
          type="checkbox"
          data-testid="survival-band-toggle"
        />
        <span>显示 95% 置信区间</span>
      </label>

      <span class="toolbar__hint">
        曲线：乘积极限（KM）· 区间：Greenwood · 分层：Cox 线性预测子三分位
      </span>
    </div>

    <div class="grid-12">
      <div id="a16-km" class="col-8">
        <!-- noise-floor 显式 null：0.477 是 AUC 地板，生存率不是判别力指标 -->
        <ChartPanel
          :title="`Kaplan-Meier 生存曲线（${active === 'all' ? '3 层并列' : '单层聚焦'}）`"
          :dataset="dataset"
          :n-samples="data.cohort.n"
          :noise-floor="null"
          :option="kmOption"
          :height="420"
        >
          <p class="note" data-testid="survival-derivation">
            曲线按<code>月</code>取阶梯点（S(t) 只降不升）。置信带为 Greenwood
            方差下的 S(t)±1.96SE，未做 log-log 变换——样本量 1000 上下、生存率
            0.77–0.98 时两者差在小数点后第三位， 选简单的那种，读的人可以拿 β 与
            SE 自己复算。
            <template v-if="active === 'all'"
              >当前 3 层带子同时铺开，聚焦某一层会加粗该层的带。</template
            >
          </p>
        </ChartPanel>
      </div>

      <div class="col-4">
        <section
          class="summary"
          data-testid="survival-summary"
          aria-labelledby="surv-summary-title"
        >
          <header class="summary__head">
            <h3 id="surv-summary-title" class="summary__title">分层摘要</h3>
            <span class="summary__hint"
              >{{ data.cohort.horizon_months }} 个月随访</span
            >
          </header>

          <div v-if="headline" class="summary__headline">
            <span class="summary__label">{{ headline.label }}</span>
            <div class="summary__pair">
              <div>
                <span class="summary__k">S(12)</span>
                <strong class="summary__v">{{ pct(headline.s12) }}</strong>
                <span class="summary__ci" data-testid="survival-ci"
                  >95%CI {{ pct(headline.ci12[0]) }}–{{
                    pct(headline.ci12[1])
                  }}</span
                >
              </div>
              <div>
                <span class="summary__k">S(24)</span>
                <strong class="summary__v">{{ pct(headline.s24) }}</strong>
                <span class="summary__ci" data-testid="survival-ci"
                  >95%CI {{ pct(headline.ci24[0]) }}–{{
                    pct(headline.ci24[1])
                  }}</span
                >
              </div>
            </div>
            <p class="summary__foot">
              括号内是 95% 置信区间；区间宽度就是「这个数字有多不确定」。
            </p>
          </div>

          <table class="tbl tbl--compact">
            <thead>
              <tr>
                <th scope="col">分层</th>
                <th scope="col">n</th>
                <th scope="col">事件</th>
                <th scope="col">中位</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in strata" :key="s.stratum_id">
                <th scope="row">
                  <span
                    class="dot"
                    :style="{ background: s.color }"
                    aria-hidden="true"
                  />
                  {{ s.stratum_label }}
                </th>
                <td class="mono">{{ s.n }}</td>
                <td class="mono">{{ s.events }}</td>
                <td class="mono">
                  {{
                    s.median_months === null ? "未达" : `${s.median_months} 月`
                  }}
                </td>
              </tr>
            </tbody>
          </table>

          <p class="summary__note">
            中位生存时间为「未达」= 24 个月内 S(t) 始终高于 50%，不是缺数据。
            <br />
            分层差异 log-rank χ²={{ data.logrank.chi2.toFixed(1) }}（df={{
              data.logrank.df
            }}）， <b>{{ formatP(data.logrank.p) }}</b
            >——三层的差别不像抽样波动。
          </p>
        </section>
      </div>
    </div>

    <!-- 风险集表：KM 的标准配件，缺了会被误读成"到 24 月还有全部人在随访" -->
    <section class="at-risk" aria-labelledby="at-risk-title">
      <header class="at-risk__head">
        <h3 id="at-risk-title" class="at-risk__title">风险集人数（at risk）</h3>
        <span class="at-risk__hint"
          >每列是该月仍在随访的企业数；尾部人数下降 =
          曲线右端的可信度在下降</span
        >
      </header>
      <table class="tbl tbl--risk" data-testid="survival-at-risk">
        <thead>
          <tr>
            <th scope="col">分层</th>
            <th v-for="t in atRiskTicks" :key="t" scope="col">{{ t }} 月</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in atRiskRows" :key="r.id">
            <th scope="row">
              <span
                class="dot"
                :style="{ background: r.color }"
                aria-hidden="true"
              />
              {{ r.label }}
            </th>
            <td v-for="(c, i) in r.cells" :key="i" class="mono">{{ c }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- Cox 系数表 -->
    <section class="cox" aria-labelledby="cox-title">
      <header class="cox__head">
        <h3 id="cox-title" class="cox__title">Cox 比例风险系数</h3>
        <span class="cox__hint">
          n={{ data.cox.n }} · 事件 {{ data.cox.events }} · 并列处理
          {{ data.cox.ties }} · Harrell C
          {{ data.cox.concordance.toFixed(3) }}
        </span>
      </header>

      <div class="table-scroll">
        <table class="tbl tbl--cox" data-testid="survival-cox">
          <thead>
            <tr>
              <th scope="col">字段</th>
              <th scope="col">β</th>
              <th scope="col">HR</th>
              <th scope="col">95% CI</th>
              <th scope="col">z</th>
              <th scope="col">p</th>
              <th scope="col">风险比方向（对数尺度，竖线 = 1.0）</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in coxRows" :key="r.feature" :data-feature="r.feature">
              <th scope="row">
                <UnknownFieldTag
                  :field="r.feature"
                  :semantic-known="r.semantic_known"
                />
              </th>
              <td class="mono">
                {{ r.coef > 0 ? "+" : "" }}{{ r.coef.toFixed(3) }}
              </td>
              <td class="mono">{{ r.hr.toFixed(2) }}</td>
              <td class="mono">
                {{ r.hr_lo.toFixed(2) }}–{{ r.hr_hi.toFixed(2) }}
              </td>
              <td class="mono">{{ r.z.toFixed(2) }}</td>
              <td class="mono" :class="{ 'is-sig': r.p < 0.05 }">
                {{ formatP(r.p) }}
              </td>
              <td>
                <div
                  class="forest"
                  role="img"
                  :aria-label="`${r.feature} HR ${r.hr.toFixed(2)}`"
                >
                  <span
                    class="forest__ref"
                    :style="{ left: `${refPercent}%` }"
                    aria-hidden="true"
                  />
                  <span
                    class="forest__ci"
                    :data-tone="barStyle(r).tone"
                    :style="{
                      left: barStyle(r).left,
                      width: barStyle(r).width,
                    }"
                    aria-hidden="true"
                  />
                  <span
                    class="forest__dot"
                    :style="{ left: barStyle(r).dot }"
                    aria-hidden="true"
                  />
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <p class="cox__note">
        HR 是「该字段每升高 1 个标准差，困境风险变成原来的几倍」。CI 跨过 1.0
        的字段（当前
        {{
          coxRows.filter((r) => r.hr_lo <= 1 && r.hr_hi >= 1).length
        }}
        个）在统计上说不清方向—— 事件只有 {{ data.cox.events }} 例，SE 普遍在
        0.10 上下，弱效应本来就没法识别。 这不是模型 bug，是样本量的事；把 p
        值如实印出来，比调阈值让它"显著"更负责。
      </p>
    </section>
  </div>
</template>

<style scoped>
.stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.toolbar__group {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.toolbar__label {
  font-size: 11px;
  color: var(--color-text-subtle);
}

.toolbar__hint {
  margin-left: auto;
  font-size: 11px;
  color: var(--color-text-subtle);
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 32px;
  padding: 0 var(--space-3);
  border: 1px solid var(--color-border-strong);
  border-radius: 16px;
  background: var(--color-surface);
  font-size: 12px;
  color: var(--color-text-muted);
  cursor: pointer;
}

.chip.is-active {
  border-color: var(--color-accent);
  color: var(--color-accent);
  background: var(--color-accent-soft);
}

.chip__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: var(--color-text-muted);
  cursor: pointer;
}

.toggle input {
  width: 14px;
  height: 14px;
  accent-color: var(--color-accent);
}

.note {
  margin-top: var(--space-2);
  font-size: 11.5px;
  line-height: 1.75;
  color: var(--color-text-muted);
}

.note code,
.summary__note code {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 0 3px;
  border-radius: 2px;
  background: var(--color-surface-muted);
}

.summary {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  height: 100%;
  padding: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.summary__head {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
}

.summary__title {
  font-size: 14px;
  font-weight: 600;
}

.summary__hint {
  margin-left: auto;
  font-size: 11px;
  color: var(--color-text-subtle);
}

.summary__headline {
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface-muted);
}

.summary__label {
  font-size: 11.5px;
  color: var(--color-text-muted);
}

.summary__pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-2);
  margin-top: 4px;
}

.summary__k {
  display: block;
  font-size: 10.5px;
  color: var(--color-text-subtle);
}

.summary__v {
  display: block;
  font-family: var(--font-mono);
  font-size: 20px;
  font-weight: 700;
  line-height: 1.3;
}

.summary__ci {
  display: block;
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--color-text-subtle);
}

.summary__foot,
.summary__note {
  margin-top: 4px;
  font-size: 11px;
  line-height: 1.7;
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

.tbl th[scope="col"]:first-child,
.tbl th[scope="row"] {
  text-align: left;
}

.tbl thead th {
  font-weight: 600;
  color: var(--color-text-muted);
  background: var(--color-surface-muted);
}

.tbl--compact {
  font-size: 11.5px;
}

.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  margin-right: 5px;
  border-radius: 50%;
  vertical-align: middle;
}

.mono {
  font-family: var(--font-mono);
}

.at-risk {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.at-risk__head {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
}

.at-risk__title,
.cox__title {
  font-size: 13.5px;
  font-weight: 600;
}

.at-risk__hint,
.cox__hint {
  font-size: 11px;
  color: var(--color-text-subtle);
}

.cox__hint {
  margin-left: auto;
  font-family: var(--font-mono);
}

.tbl--risk td,
.tbl--risk th[scope="col"] {
  text-align: center;
}

.tbl--risk th[scope="row"] {
  text-align: left;
}

.cox {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.cox__head {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.table-scroll {
  overflow-x: auto;
}

.tbl--cox th[scope="col"]:last-child {
  text-align: left;
}

.tbl--cox td:last-child {
  width: 42%;
}

.is-sig {
  color: var(--risk-high-risk);
  font-weight: 600;
}

.forest {
  position: relative;
  height: 16px;
  margin: 0 6px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--color-border) 40%, transparent);
}

.forest__ref {
  position: absolute;
  top: -2px;
  bottom: -2px;
  width: 1px;
  background: var(--color-text-subtle);
}

.forest__ci {
  position: absolute;
  top: 6px;
  height: 4px;
  border-radius: 2px;
  background: var(--color-accent);
}

.forest__ci[data-tone="risk"] {
  background: var(--risk-high-risk);
}

.forest__dot {
  position: absolute;
  top: 4px;
  width: 8px;
  height: 8px;
  margin-left: -4px;
  border-radius: 50%;
  background: var(--color-surface);
  border: 2px solid var(--color-text);
}

.cox__note {
  font-size: 11.5px;
  line-height: 1.8;
  color: var(--color-text-muted);
}
</style>
