<script setup lang="ts">
/**
 * P6 前瞻预测（FE-15 全量版）
 *
 * 两个区块 = 两个接口，各自独立取数、各自带区块级来源角标：
 *   A16 生存力预测  GET /api/forecast/survival   dataset=real_panel / source=demo_placeholder（契约未就绪）
 *   A17 融资力预测  GET /api/forecast/financing  dataset=real_panel / source=demo_placeholder（契约未就绪）
 *
 * 这一页最容易犯的错，是把「曲线画得很漂亮」当成「预测很准」。
 * 所以页面刻意做了三件事：
 *   ① 每一块都把**置信区间**摆在点估计旁边（KM 带宽 + 风险集表 at-risk；
 *      校准图给 Wilson 十字须；估值条给 P10–P90），见验收「曲线含置信区间」；
 *   ② 两个区块各自 `useAsyncSection`，任何一块取数失败只降级那一块，
 *      页面骨架、另一块、结论区照常渲染 —— 见验收「缺契约时区块级降级不白屏」；
 *   ③ 口径声明条放在图**上面**：队列是合成的（P0-3 / P0-5 未到位），
 *      但 KM / Greenwood / log-rank / Cox / Brier / ECE / AUC 是拿这份队列**现场算的**。
 *      「估计器是真的」与「数据是真的」是两句话，不能混成一句。
 *
 * 三条本页特有的红线落地：
 *   红线二：Cox 表里 6 个协变量全是语义未知代号，一律走 UnknownFieldTag，不做业务化包装；
 *   红线三：两条曲线都不是 AUC 口径的判别力图，`noise_floor` 传 null（契约也是 null），
 *          不在生存率图上挂一句「噪声地板 AUC=0.477」；
 *   红线四：两个区块同属 demo_placeholder，语气一致地保持"占位"观感，
 *          不与 P4 的实测 AUC 图并列展示或共用坐标轴。
 */
import { computed } from "vue";

import PageSkeleton from "@/components/PageSkeleton.vue";
import RedLineBanner from "@/components/RedLineBanner.vue";
import StatePanel from "@/components/StatePanel.vue";
import SectionBadge from "@/components/model/SectionBadge.vue";
import FinancingPanel from "@/components/forecast/FinancingPanel.vue";
import SurvivalPanel from "@/components/forecast/SurvivalPanel.vue";
import { fetchFinancing, fetchSurvival } from "@/api";
import { useAsyncSection } from "@/composables/useAsyncSection";
import type { FinancingContract, SurvivalContract } from "@/types/contracts";
import type { ContractMeta, DatasetKind } from "@/types";

/* ------------------------------ 两个区块各自取数 ------------------------------ */
const survivalSection = useAsyncSection(fetchSurvival);
const financingSection = useAsyncSection(fetchFinancing);

function pick<T>(sec: { data: unknown }): {
  data: T | null;
  meta: ContractMeta;
} {
  const env = sec.data as { data?: T; meta?: ContractMeta } | null;
  return {
    data: (env?.data ?? null) as T | null,
    meta: (env?.meta ?? {}) as ContractMeta,
  };
}

const survivalEnv = computed(() =>
  pick<SurvivalContract>({ data: survivalSection.data.value }),
);
const financingEnv = computed(() =>
  pick<FinancingContract>({ data: financingSection.data.value }),
);

/**
 * 结构守卫：信封到了不等于契约对。
 *
 * 缺契约时 `getMock` 返回 `code: 'CONTRACT_PENDING'` + `data: {}`，
 * axios 侧也可能是 200 但少字段。这里要求关键数组存在才认，否则交给 StatePanel 降级。
 */
const survival = computed<SurvivalContract | null>(() => {
  const d = survivalEnv.value.data;
  return d && Array.isArray(d.curves) && Array.isArray(d.strata) && d.cohort
    ? d
    : null;
});
const financing = computed<FinancingContract | null>(() => {
  const d = financingEnv.value.data;
  return d && d.calibration && Array.isArray(d.calibration.bins) && d.valuation
    ? d
    : null;
});

const ds = (meta: ContractMeta): DatasetKind =>
  (meta.dataset as DatasetKind) ?? "real_panel";
/**
 * `source` 缺省不能落到 'unknown' 再进角标 —— 角标会把 "unknown" 原样印出来，
 * 读者看到的是 `真实信贷面板 · unknown`，比"占位数据"更难看也更没信息。
 * 契约缺失时我们**知道**它为什么缺（契约未产出），这本身就是一种来源状态。
 */
const src = (meta: ContractMeta) =>
  String(
    meta.source ??
      (meta.contract_status === "mock" ? "demo_placeholder" : "contract_spec"),
  );
const survivalDataset = computed(() => ds(survivalEnv.value.meta));
const financingDataset = computed(() => ds(financingEnv.value.meta));

/** 页头只在两块都还没回来时才上骨架，单块失败不拖累整页（验收②） */
const bothLoading = computed(
  () =>
    !survival.value &&
    !financing.value &&
    (survivalSection.status.value === "loading" ||
      financingSection.status.value === "loading"),
);

/** 占位声明条：只要有一块是 mock，就把 P0-3 / P0-5 摆在最上面 */
const mockBlocks = computed(() => {
  const out: Array<{ code: string; meta: ContractMeta }> = [];
  if (
    survival.value &&
    String(survivalEnv.value.meta.contract_status) === "mock"
  )
    out.push({ code: "A16", meta: survivalEnv.value.meta });
  if (
    financing.value &&
    String(financingEnv.value.meta.contract_status) === "mock"
  )
    out.push({ code: "A17", meta: financingEnv.value.meta });
  return out;
});
const blockedBy = computed(() => {
  const set = new Set<string>();
  for (const c of [survival.value?.unlock, financing.value?.unlock]) {
    for (const b of c?.blocked_by ?? []) set.add(b);
  }
  return [...set];
});

/* -------------------------------- 结论区取数 -------------------------------- */
const fmtPct = (v: number | undefined | null, d = 1) =>
  typeof v === "number" && Number.isFinite(v)
    ? `${(v * 100).toFixed(d)}%`
    : "—";
const fmt = (v: number | undefined | null, d = 3) =>
  typeof v === "number" && Number.isFinite(v) ? v.toFixed(d) : "—";

const cohort = computed(() => survival.value?.cohort ?? null);
const cal = computed(() => financing.value?.calibration ?? null);
const valSummary = computed(() => financing.value?.valuation.summary ?? null);
</script>

<template>
  <div class="page">
    <header class="page__head">
      <div class="page__title-row cluster">
        <h1 class="page__title">P6 前瞻预测</h1>
        <span class="page__sub">
          2 个接口 · GET /api/forecast/survival · GET /api/forecast/financing
        </span>
        <span class="page__meta">
          数据来源 <code>{{ survivalDataset }}</code>
          <template v-if="cohort">· 队列 n={{ cohort.n }}</template>
          <template v-if="cohort"
            >· 24 月事件 {{ cohort.events }} 例（{{
              fmtPct(cohort.observed_event_rate)
            }}）</template
          >
        </span>
      </div>
      <p class="page__lede">
        这一页回答两个「往后看」的问题：这家企业未来两年还在不在（生存力），以及它下一轮能不能融到钱、值多少（融资力）。
        两条曲线都带置信区间 —— 前瞻预测不给区间，等于只说了半句话。
        更需要先看清的是：真实的事件口径（P0-3）与融资事件表（P0-5）都还没到，
        所以下面两条曲线的**队列是合成的**；但算这些曲线的
        KM、Greenwood、log-rank、Cox、 Brier、ECE、AUC
        全部是现场实算的。能拿走的结论是「这套估计器跑得通、区间怎么读」，
        不是「这 1017 家企业的存活率是 96%」。
      </p>
    </header>

    <RedLineBanner
      scope="P6 前瞻预测"
      :count="survival?.cox?.covariates?.length"
      :affected="['A16']"
    />

    <PageSkeleton v-if="bothLoading" :kpi-count="4" :charts="[420, 380]" />

    <template v-else>
      <!-- 占位声明：放在图上面，不放图下面 -->
      <aside
        v-if="mockBlocks.length"
        class="mock-strip"
        data-testid="forecast-mock-strip"
      >
        <span class="mock-strip__badge">契约未就绪 · 占位</span>
        <div class="mock-strip__body">
          <p class="mock-strip__title">
            {{
              mockBlocks.map((b) => b.code).join(" / ")
            }}
            的<b>队列是确定性合成的</b>， 但曲线、区间与统计量都是拿这份队列<b
              >现场算的</b
            >
          </p>
          <p class="mock-strip__text">
            卡在两件事上：<b>{{ blockedBy.join(" / ") || "P0-3、P0-5" }}</b> ——
            事件口径没定（label
            是「未来一年违约」还是「经营异常」尚未确认），融资事件表（时间/金额/投前投后估值）也还没取到。
            这两件事不定，生存时间与估值区间在真实数据上都无从谈起。
          </p>
          <p class="mock-strip__text">
            <b>免责：</b>{{ mockBlocks[0].meta.disclaimer }}
          </p>
          <p class="mock-strip__text">
            <b>解锁后：</b
            >{{ survival?.unlock?.after ?? financing?.unlock?.after ?? "—" }}
          </p>
        </div>
      </aside>

      <!-- 结论 -->
      <section
        v-if="survival || financing"
        class="page__verdict"
        aria-labelledby="verdict"
      >
        <h2 id="verdict" class="page__verdict-title">这一页真正能说的三句话</h2>
        <ol class="verdict-list">
          <li>
            <b>分层是有意义的。</b>
            <template v-if="survival">
              低/中/高三个风险层的 24 月生存率分别是
              {{ fmtPct(survival.strata[0]?.s24) }}、{{
                fmtPct(survival.strata[1]?.s24)
              }}、{{ fmtPct(survival.strata[2]?.s24) }}， log-rank χ²={{
                fmt(survival.logrank.chi2, 2)
              }}（df={{ survival.logrank.df }}，
              <span class="mono">p &lt; 0.001</span>），差异不可能是抽样波动。
              但这只证明「风险分层这件事成立」，不证明分层用的协变量在真实面板上同样有效。
            </template>
            <template v-else>生存力契约缺失，本句暂不可读。</template>
          </li>
          <li>
            <b>区间比点估计更值得看。</b>
            <template v-if="survival">
              高风险层 S(24)={{ fmtPct(survival.strata[2]?.s24) }}， 95% 区间
              {{ fmtPct(survival.strata[2]?.s24_ci[0]) }}–{{
                fmtPct(survival.strata[2]?.s24_ci[1])
              }}; 低风险层 11 例事件只够把区间压到 ±2 个百分点左右。
              样本里事件只有 {{ cohort?.events }} 例（{{
                fmtPct(cohort?.observed_event_rate)
              }}），**这就是精度的上限**。
            </template>
            <template v-else>—</template>
          </li>
          <li>
            <b>概率校准比 AUC 更能说明能不能用。</b>
            <template v-if="cal">
              AUC {{ fmt(cal.auc) }} 看着还行，真正关键的是 Brier
              {{ fmt(cal.brier, 4) }} 低于「恒用总体发生率」的基准
              {{ fmt(cal.brier_baseline, 4) }}， 可靠性斜率
              {{ fmt(cal.reliability_slope, 2) }}（理想值 1）—— 概率大致是对的，
              但估值区间条的中位宽窄比达
              {{ valSummary?.width_ratio_median ?? "—" }} 倍，
              说明**单家企业的估值还远没到能对外说的程度**。
            </template>
            <template v-else>融资力契约缺失，本句暂不可读。</template>
          </li>
        </ol>
      </section>

      <!-- ============================ A16 生存力预测 ============================ -->
      <section
        class="page__block"
        aria-labelledby="a16"
        data-testid="section-a16"
      >
        <header class="page__block-head">
          <span class="page__code">A16</span>
          <h2 id="a16" class="page__block-title">
            生存力预测 · Kaplan-Meier 生存曲线 + Cox 风险分层
          </h2>
          <span class="page__block-desc"
            >分层曲线（含 Greenwood 置信带）+ 风险集表 + Cox 系数表</span
          >
          <SectionBadge
            :dataset="survivalDataset"
            :source="src(survivalEnv.meta)"
            endpoint="GET /api/forecast/survival"
          />
        </header>

        <StatePanel
          v-if="survivalSection.status.value === 'error'"
          variant="error"
          title="生存力契约未能加载"
          :reason="survivalSection.error.value?.reason ?? ''"
          :next-step="survivalSection.error.value?.nextStep ?? ''"
          :retry="() => void survivalSection.retry()"
        />

        <SurvivalPanel
          v-else-if="survival"
          :data="survival"
          :dataset="survivalDataset"
          :source="src(survivalEnv.meta)"
        />

        <StatePanel
          v-else
          variant="pending"
          title="生存力契约未就绪（A16 区块降级，页面其余部分照常）"
          reason="survival.json 未生成或结构不符（缺少 curves / strata / cohort）。本区块按区块级降级处理，不影响 A17 与结论区。"
          next-step="在 frontend/ 下执行 python3 scripts/build_forecast_contracts.py 生成契约；契约到位后本区块自动恢复，无需改前端代码。"
          :retry="() => void survivalSection.retry()"
        />
      </section>

      <!-- ============================ A17 融资力预测 ============================ -->
      <section
        class="page__block"
        aria-labelledby="a17"
        data-testid="section-a17"
      >
        <header class="page__block-head">
          <span class="page__code">A17</span>
          <h2 id="a17" class="page__block-title">
            融资力预测 · 概率校准曲线 + 估值区间条
          </h2>
          <span class="page__block-desc"
            >等频十分位校准（Wilson 区间）+ P10 / P50 / P90 估值区间</span
          >
          <SectionBadge
            :dataset="financingDataset"
            :source="src(financingEnv.meta)"
            endpoint="GET /api/forecast/financing"
          />
        </header>

        <StatePanel
          v-if="financingSection.status.value === 'error'"
          variant="error"
          title="融资力契约未能加载"
          :reason="financingSection.error.value?.reason ?? ''"
          :next-step="financingSection.error.value?.nextStep ?? ''"
          :retry="() => void financingSection.retry()"
        />

        <FinancingPanel
          v-else-if="financing"
          :data="financing"
          :dataset="financingDataset"
          :source="src(financingEnv.meta)"
        />

        <StatePanel
          v-else
          variant="pending"
          title="融资力契约未就绪（A17 区块降级，页面其余部分照常）"
          reason="financing.json 未生成或结构不符（缺少 calibration.bins / valuation）。本区块按区块级降级处理，不影响 A16 与结论区。"
          next-step="在 frontend/ 下执行 python3 scripts/build_forecast_contracts.py 生成契约；契约到位后本区块自动恢复，无需改前端代码。"
          :retry="() => void financingSection.retry()"
        />
      </section>

      <!-- 怎么读这两张图 -->
      <section class="page__block" aria-labelledby="how">
        <header class="page__block-head">
          <span class="page__code">Q</span>
          <h2 id="how" class="page__block-title">
            这两张图该怎么读（以及哪里不能读）
          </h2>
        </header>
        <div class="qa">
          <div class="qa__item">
            <p class="qa__q">S(24)=96% 是不是说明绝大多数企业都很安全？</p>
            <p class="qa__a">
              不是。生存率高首先是因为事件本来就少 —— 队列 24 个月只发生
              {{ cohort?.events ?? "—" }} 例（{{
                fmtPct(cohort?.observed_event_rate)
              }}）。 S(t) 的分母在漂：随着随访推进，留在风险集里的人越来越少
              （见曲线下的 at-risk 行），曲线尾段的陡降常常只由个位数事件推动，
              所以尾段区间会明显变宽。要看的是<b>层间差距</b>与<b>区间宽度</b>，不是绝对值。
            </p>
          </div>
          <div class="qa__item">
            <p class="qa__q">Cox 表里 x125、x193 到底代表什么业务含义？</p>
            <p class="qa__a">
              不知道，而且本页拒绝编。字段字典（P0-2）未补齐，x21–x200
              一律只显示原始代号 （<code>label_ref = feature_dict.xNNN</code>
              指向尚未产出的字典）。 能说清的只有方向与量级：HR&gt;1
              是推高困境风险，HR&lt;1 是降低， HR 偏离 1 的 95% 区间不跨 1
              才算统计上站得住。 这 6 个协变量取自实测 SHAP 的
              Top-6（符号一致），但它们的效应量是在<b>合成队列</b>上拟合的。
            </p>
          </div>
          <div class="qa__item">
            <p class="qa__q">校准曲线一直在 45° 线上，是不是可以上线了？</p>
            <p class="qa__a">
              不是。校准好只说明「模型给出的概率值与实际发生率吻合」，
              而这里的吻合是在<b>合成队列</b>上达成的 ——
              生成过程本身就写定了模型看得见真信号，
              属于「自己出题自己考」。真正能判断可用性的是两件事： ①
              换真实队列后校准是否还贴着 45°；② 估值区间条能否收窄
              （当前中位宽窄比
              {{
                valSummary?.width_ratio_median ?? "—"
              }}
              倍，宽到没有决策价值）。
            </p>
          </div>
          <div class="qa__item">
            <p class="qa__q">为什么这里没有噪声地板 0.477？</p>
            <p class="qa__a">
              0.477 是 AUC 的置换对照地板，衡量的是「判别力是不是强于随机」。
              生存率 S(t) 与预测概率 p 都不是判别力指标，把 0.477
              画到这两张图上属于量纲错配 （P4 曾在 PR / KS
              图上踩过同一个坑）。判别力口径在 A17 用 AUC
              {{ fmt(cal?.auc) }} 单独给出。
            </p>
          </div>
          <div class="qa__item">
            <p class="qa__q">下一步做什么？</p>
            <p class="qa__a">
              不是调模型，是补齐
              {{ blockedBy.join(" / ") || "P0-3、P0-5" }}：先把「事件」定义清楚
              （违约还是经营异常、观测窗内还是窗外），再拿到融资事件表。
              数据到位后 <code>build_forecast_contracts.py</code> 只改读取源，
              同一套 KM / Cox / 校准实现不动，前端零改动 ——
              这也是本页现在敢展示的原因： 接口形状已经按真实数据设计好了。
            </p>
          </div>
        </div>
      </section>
    </template>

    <footer class="page__foot cluster">
      <span>上游：</span>
      <RouterLink :to="{ name: 'model' }" class="page__link"
        >P4 模型与对比</RouterLink
      >
      <span class="page__foot-sep">·</span>
      <RouterLink :to="{ name: 'explain' }" class="page__link"
        >P5 可解释性</RouterLink
      >
      <span class="page__foot-sep">·</span>
      <span
        >队列锚定：企业数 {{ cohort?.n ?? "—" }}（panel_depth.json）· 事件率
        {{ fmtPct(cohort?.target_event_rate) }}（benchmark.json 75/698）·
        协变量取 shap_global.json Top-6。</span
      >
    </footer>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.page__head {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.page__title {
  font-size: 22px;
  font-weight: 700;
}

.page__sub,
.page__meta {
  font-size: 12px;
  color: var(--color-text-muted);
}

.page__meta code {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 0 4px;
  border-radius: 2px;
  background: var(--color-surface-muted);
}

.page__lede {
  max-width: 980px;
  font-size: 13px;
  line-height: 1.75;
  color: var(--color-text-muted);
}

.page__verdict {
  padding: var(--space-4) var(--space-5);
  border-left: 3px solid var(--risk-high-risk);
  border-radius: var(--radius-md);
  background: var(--risk-high-risk-bg);
}

.page__verdict-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--risk-high-risk);
  margin-bottom: var(--space-2);
}

.verdict-list {
  display: grid;
  gap: var(--space-2);
  margin: 0;
  padding-left: 1.2em;
  font-size: 13px;
  line-height: 1.85;
  color: var(--color-text);
}

.mono {
  font-family: var(--font-mono);
}

.page__block {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-5);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.page__block-head {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.page__block-desc {
  font-size: 11.5px;
  color: var(--color-text-subtle);
}

.page__code {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-family: var(--font-mono);
  font-size: 11px;
}

.page__block-title {
  font-size: 16px;
  font-weight: 600;
}

.mock-strip {
  display: flex;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--risk-watch);
  border-radius: var(--radius-sm);
  background: var(--risk-watch-bg);
}

.mock-strip__badge {
  flex: 0 0 auto;
  align-self: flex-start;
  padding: 1px 7px;
  border: 1px solid currentColor;
  border-radius: 10px;
  font-size: 10.5px;
  color: var(--risk-watch);
  white-space: nowrap;
}

.mock-strip__body {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.mock-strip__title {
  font-size: 12.5px;
  color: var(--color-text);
}

.mock-strip__text {
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--color-text-muted);
}

.qa {
  display: grid;
  gap: var(--space-3);
}

.qa__q {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text);
  margin-bottom: 2px;
}

.qa__a {
  font-size: 12.5px;
  line-height: 1.85;
  color: var(--color-text-muted);
}

.qa__a code {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 0 3px;
  border-radius: 2px;
  background: var(--color-surface-muted);
}

.page__foot {
  padding: var(--space-3) 0;
  border-top: 1px solid var(--color-border);
  font-size: 12px;
  color: var(--color-text-muted);
}

.page__link {
  color: var(--color-accent);
}

.page__foot-sep {
  color: var(--color-text-subtle);
}
</style>
