<script setup lang="ts">
/**
 * P5 可解释性（FE-14 全量版）
 *
 * 两个区块 = 两个接口，各自独立取数、各自带区块级来源角标：
 *   A13 全局特征贡献  GET /api/explain/global        dataset=real_panel / source=measured（蜂群点位为 reconstructed_from_anchors）
 *   A15 阶段-因子-权重 GET /api/explain/stage-factor  dataset=real_panel / source=demo_placeholder（契约未就绪）
 *
 * 这一页要传达的核心事实：模型排出来的重要字段**全部是语义未知的代号**，
 * 所以页面刻意不写"哪些财务指标最重要"这类结论，而是把「解释比例」摆出来：
 * 语义未知字段占了 |SHAP| 的 100%，这不是算法没做好，是字段字典（P0-2）没到。
 *
 * 两条本页特有的纪律：
 *   ① 条形图 / 明细表的数字是**实测**；蜂群图的逐样本点位是**锚点重建**。
 *      两者同屏并列，各自标注，不许只挂一颗"实测"角标了事（红线四）。
 *   ② A15 整块是 mock：阶段与权重占位、字段排序与 mean|SHAP| 实测。
 *      页面把「占位」做成视觉标记（黄色 chip + 边框），不让它和实测数字同观感。
 */
import { computed, ref } from 'vue'

import ChartPanel from '@/components/ChartPanel.vue'
import PageSkeleton from '@/components/PageSkeleton.vue'
import RedLineBanner from '@/components/RedLineBanner.vue'
import StatePanel from '@/components/StatePanel.vue'
import UnknownFieldTag from '@/components/UnknownFieldTag.vue'
import BeeswarmPanel from '@/components/explain/BeeswarmPanel.vue'
import SectionBadge from '@/components/model/SectionBadge.vue'
import StageFactorPanel from '@/components/explain/StageFactorPanel.vue'
import { fetchShapGlobal, fetchStageFactor } from '@/api'
import { useAsyncSection } from '@/composables/useAsyncSection'
import type {
  ShapGlobal,
  ShapBeeswarmFeature,
  ShapFeatureRow,
  StageFactorWeightContract,
} from '@/types/contracts'
import type { ContractMeta, DatasetKind } from '@/types'
import { NOISE_FLOOR } from '@/utils/redlines'

/* ------------------------------ 两个区块各自取数 ------------------------------ */
const shapSection = useAsyncSection(fetchShapGlobal)
const stageSection = useAsyncSection(fetchStageFactor)

function pick<T>(sec: { data: unknown }): { data: T | null; meta: ContractMeta } {
  const env = sec.data as { data?: T; meta?: ContractMeta } | null
  return { data: (env?.data ?? null) as T | null, meta: (env?.meta ?? {}) as ContractMeta }
}

const shapEnv = computed(() => pick<ShapGlobal>({ data: shapSection.data.value }))
const stageEnv = computed(() => pick<StageFactorWeightContract>({ data: stageSection.data.value }))

const payload = computed<ShapGlobal | null>(() => {
  const d = shapEnv.value.data
  return d && Array.isArray(d.models) ? d : null
})
const stageContract = computed<StageFactorWeightContract | null>(() => {
  const d = stageEnv.value.data
  return d && Array.isArray(d.stages) && Array.isArray(d.factors) ? d : null
})

const ds = (meta: ContractMeta): DatasetKind => (meta.dataset as DatasetKind) ?? 'real_panel'
const src = (meta: ContractMeta) => String(meta.source ?? 'unknown')
const shapDataset = computed(() => ds(shapEnv.value.meta))
const stageDataset = computed(() => ds(stageEnv.value.meta))
const noiseFloor = computed(() => {
  const v = shapEnv.value.meta.noise_floor
  return typeof v === 'number' ? v : NOISE_FLOOR
})

const loading = computed(() => !payload.value && shapSection.status.value === 'loading')

/* --------------------------------- 模型切换 --------------------------------- */
const activeId = ref<'xgb' | 'lr'>('xgb')
const active = computed(() => payload.value?.models.find((m) => m.model_id === activeId.value) ?? null)
const modelLabel = (id: string) => payload.value?.models.find((m) => m.model_id === id)?.model_label ?? id

/* --------------------------------- 排序切换 --------------------------------- */
/**
 * 排序口径（FE-14 新增交互）：
 *   importance — 按实测 mean|SHAP| 降序（默认，契约 rank 的顺序）
 *   direction  — 风险因子在前（+1 → −1），组内再按贡献降序
 *   code       — 按字段代号数字升序（x21 → x200），查"某个字段排第几"时用
 *
 * 三者都只改**观看顺序**，不改契约里的 rank —— 表格里的 # 恒为实测排名，
 * 否则切个排序就会让人以为排名变了。
 */
type SortMode = 'importance' | 'direction' | 'code'
const sortMode = ref<SortMode>('importance')

const SORT_MODES: Array<{ id: SortMode; label: string; hint: string }> = [
  { id: 'importance', label: '按贡献降序', hint: '实测 mean|SHAP|' },
  { id: 'direction', label: '风险因子优先', hint: '方向 +1 在前' },
  { id: 'code', label: '按字段代号', hint: 'x21 → x200' },
]

const codeNum = (f: string) => Number(String(f).replace(/[^\d]/g, '')) || 0

function sortRows<T extends { rank: number; feature: string; direction: number }>(
  rows: T[],
  mode: SortMode,
): T[] {
  const out = [...rows]
  if (mode === 'direction') out.sort((a, b) => b.direction - a.direction || a.rank - b.rank)
  else if (mode === 'code') out.sort((a, b) => codeNum(a.feature) - codeNum(b.feature) || a.rank - b.rank)
  else out.sort((a, b) => a.rank - b.rank)
  return out
}

const sortedTop = computed<ShapFeatureRow[]>(() => sortRows(active.value?.top ?? [], sortMode.value))
const barRows = computed(() => sortedTop.value.slice(0, 15))

/** 蜂群图只覆盖 Top-15（契约给的），排序方式跟条形图保持一致，否则两张图对不上行 */
const beeswarmRows = computed<ShapBeeswarmFeature[]>(() => {
  const list = active.value?.beeswarm ?? []
  const order = new Map(sortedTop.value.map((r, i) => [r.feature, i]))
  return [...list].sort((a, b) => (order.get(a.feature) ?? 99) - (order.get(b.feature) ?? 99))
})

const overlap = computed(() => {
  const models = payload.value?.models ?? []
  if (models.length < 2) return null
  const a = new Set(models[0].top.slice(0, 10).map((r) => r.feature))
  const b = models[1].top.slice(0, 10).map((r) => r.feature)
  const same = b.filter((f) => a.has(f))
  return { same, n: same.length }
})

/** 解释集规模：从 explain_set 文案里取，取不到就显示 — */
const explainStats = computed(() => {
  const m = /\((\d+)\s*samples?,\s*(\d+)\s*positives?\)/i.exec(payload.value?.explain_set ?? '')
  return m ? { n: Number(m[1]), pos: Number(m[2]) } : null
})

const fmtPct = (v: number) => `${(v * 100).toFixed(1)}%`

/* --------------------------------- A13 条形图 --------------------------------- */
const barOption = computed(() => {
  const rows = [...barRows.value].reverse()
  return {
    animation: false,
    grid: { left: 88, right: 74, top: 12, bottom: 30 },
    tooltip: {
      trigger: 'item',
      formatter: (p: { dataIndex: number }) => {
        const r = rows[p.dataIndex]
        return [
          `<b>${r.feature}</b>${r.semantic_known ? '' : '（语义未知）'}`,
          `mean|SHAP| ${r.mean_abs_shap.toFixed(4)}（占全部 ${(r.share * 100).toFixed(2)}%）`,
          r.direction > 0 ? '风险因子：值越大越推高违约概率' : '保护因子：值越大越降低违约概率',
          `实测排名 #${r.rank}`,
        ].join('<br/>')
      },
    },
    xAxis: {
      type: 'value',
      name: 'mean|SHAP|',
      nameTextStyle: { fontSize: 10, color: '#5b6673' },
      axisLabel: { fontSize: 10, color: '#5b6673' },
      splitLine: { lineStyle: { color: '#eef1f5' } },
    },
    yAxis: {
      type: 'category',
      data: rows.map((r) => r.feature),
      axisLabel: { fontSize: 10, fontFamily: 'monospace', color: '#1f2933' },
      axisTick: { show: false },
    },
    series: [
      {
        type: 'bar',
        barWidth: 12,
        label: {
          show: true,
          position: 'right',
          fontSize: 9,
          color: '#5b6673',
          formatter: (p: { dataIndex: number }) => `${(rows[p.dataIndex].share * 100).toFixed(1)}%`,
        },
        data: rows.map((r) => ({
          value: r.mean_abs_shap,
          itemStyle: {
            color: r.direction > 0 ? '#b3312c' : '#2f5d8a',
            opacity: r.semantic_known ? 0.95 : 0.66,
          },
        })),
      },
    ],
  }
})
</script>

<template>
  <div class="page">
    <header class="page__head">
      <div class="page__title-row cluster">
        <h1 class="page__title">P5 可解释性</h1>
        <span class="page__sub">2 个接口 · GET /api/explain/global · GET /api/explain/stage-factor</span>
        <span class="page__meta">
          数据来源 <code>{{ shapDataset }}</code>
          <template v-if="explainStats">· 解释集 n={{ explainStats.n }}（正例 {{ explainStats.pos }}）</template>
        </span>
      </div>
      <p class="page__lede">
        全局解释回答「模型靠什么做判断」，阶段图谱回答「不同阶段看的东西是不是不一样」。
        本页把两个模型的 top-20 贡献与 Top-15 贡献分布摆出来，再用阶段图谱演示申报书承诺的动态决策口径；
        但更重要的是右上角那个数字——语义已知字段的解释占比。它现在是 0。
      </p>
    </header>

    <RedLineBanner
      scope="P5 可解释性"
      :count="payload?.n_features_used"
      :affected="['A13', 'A15']"
    />

    <PageSkeleton v-if="loading" :kpi-count="4" :charts="[420, 460]" />

    <StatePanel
      v-else-if="shapSection.status.value === 'error'"
      variant="error"
      title="全局解释数据未能加载"
      :reason="shapSection.error.value?.reason ?? ''"
      :next-step="shapSection.error.value?.nextStep ?? ''"
      :retry="() => void shapSection.retry()"
    />

    <template v-else-if="payload">
      <!-- 结论 -->
      <section class="page__verdict" aria-labelledby="verdict">
        <h2 id="verdict" class="page__verdict-title">这一页真正想说的一句话</h2>
        <p class="page__verdict-text">{{ payload.conclusion }}</p>
      </section>

      <!-- KPI -->
      <div class="grid-12">
        <div class="col-3 card">
          <span class="card__label">语义未知字段解释占比</span>
          <strong class="card__value card__value--warn">{{ fmtPct(active?.unknown_field_share ?? 0) }}</strong>
          <span class="card__hint">x21–x200 之外一个都没有 —— 不是模型挑不出来，是字段字典没到</span>
        </div>
        <div class="col-3 card">
          <span class="card__label">Top-20 累计解释占比</span>
          <strong class="card__value">{{ fmtPct(active?.top20_share ?? 0) }}</strong>
          <span class="card__hint">{{ active?.n_features }} 个特征里，前 20 个就解释了绝大部分</span>
        </div>
        <div class="col-3 card">
          <span class="card__label">两模型 Top-10 重合</span>
          <strong class="card__value">{{ overlap ? `${overlap.n} / 10` : '—' }}</strong>
          <span class="card__hint">{{ overlap?.same.join('、') || '—' }}</span>
        </div>
        <div class="col-3 card">
          <span class="card__label">解释集</span>
          <strong class="card__value">{{ explainStats?.n ?? '—' }}</strong>
          <span class="card__hint">
            留出集正例 {{ explainStats?.pos ?? '—' }} · 噪声地板 {{ noiseFloor }}
          </span>
        </div>
      </div>

      <!-- ============================ A13 全局特征贡献 ============================ -->
      <section class="page__block" aria-labelledby="a13" data-testid="section-a13">
        <header class="page__block-head">
          <span class="page__code">A13</span>
          <h2 id="a13" class="page__block-title">全局特征贡献排序</h2>
          <span class="page__block-desc">条形图（实测）+ 蜂群图（锚点重建）</span>
          <SectionBadge
            :dataset="shapDataset"
            :source="src(shapEnv.meta)"
            endpoint="GET /api/explain/global"
          />
        </header>

        <!-- 模型 + 排序切换 -->
        <div class="toolbar">
          <div class="page__switch" role="group" aria-label="选择解释的模型">
            <button
              v-for="m in payload.models"
              :key="m.model_id"
              type="button"
              data-testid="explain-model-chip"
              :aria-pressed="activeId === m.model_id"
              :class="{ 'is-active': activeId === m.model_id }"
              @click="activeId = m.model_id as 'xgb' | 'lr'"
            >
              {{ m.model_label }}
            </button>
          </div>

          <div class="sort" role="group" aria-label="选择排序方式">
            <span class="sort__label">排序</span>
            <button
              v-for="s in SORT_MODES"
              :key="s.id"
              type="button"
              class="sort__btn"
              :class="{ 'is-active': sortMode === s.id }"
              :aria-pressed="sortMode === s.id"
              :title="s.hint"
              :data-testid="`sort-${s.id}`"
              @click="sortMode = s.id"
            >
              {{ s.label }}
            </button>
          </div>

          <span class="toolbar__hint">
            解释集：{{ payload.explain_set }} · 排序只换观看顺序，表格 # 恒为实测排名
          </span>
        </div>

        <div class="grid-12">
          <div id="a13-bar" class="col-7">
            <!-- noise-floor 显式传 null：0.477 是 **AUC** 的地板，SHAP 值是贡献度不是
                 判别力，两者不同量纲。以前这里传了 0.477，图上就挂出一句
                 「噪声地板 AUC = 0.477，低于此值即与随机无异」——放在贡献条形图下面
                 是彻底的误导（FE-13 在 PR / KS 图上修过同一个坑）。 -->
            <ChartPanel
              :title="`${modelLabel(activeId)} · 全局特征贡献 Top-15（mean|SHAP|）`"
              :dataset="shapDataset"
              :n-samples="shapEnv.meta.n_samples"
              :noise-floor="null"
              :option="barOption"
              :height="420"
            >
              <ul class="legend" aria-label="颜色图例">
                <li><span class="legend__dot legend__dot--risk" />风险因子（值越大越推高违约概率）</li>
                <li><span class="legend__dot legend__dot--protect" />保护因子（值越大越降低违约概率）</li>
                <li><span class="legend__dot legend__dot--faint" />浅色 = 语义未知字段</li>
              </ul>
              <p class="page__note">
                所有柱条都是深浅不一的"未知"：颜色只区分方向，代号本身不含业务含义。
                这就是「模型可解释但业务不可读」的具体样子。
              </p>
            </ChartPanel>
          </div>

          <div class="col-5">
            <section id="a13-toplist" class="table-wrap" aria-labelledby="toplist">
              <header class="table-wrap__head">
                <h3 id="toplist" class="table-wrap__title">Top-20 明细</h3>
                <span class="table-wrap__hint">按当前排序</span>
              </header>
              <div class="table-scroll table-scroll--tall">
                <table class="tbl">
                  <thead>
                    <tr>
                      <th scope="col">#</th>
                      <th scope="col">字段</th>
                      <th scope="col">mean|SHAP|</th>
                      <th scope="col">占比</th>
                      <th scope="col">方向</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="r in sortedTop" :key="r.feature">
                      <td class="mono">{{ r.rank }}</td>
                      <th scope="row"><UnknownFieldTag :field="r.feature" :semantic-known="r.semantic_known" /></th>
                      <td class="mono">{{ r.mean_abs_shap.toFixed(4) }}</td>
                      <td class="mono">{{ (r.share * 100).toFixed(2) }}%</td>
                      <td>
                        <span class="dir" :data-dir="r.direction">
                          {{ r.direction > 0 ? '风险 +' : '保护 −' }}
                        </span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        </div>

        <!-- 蜂群图：条形图回答不了「贡献集中还是铺开」 -->
        <div v-if="active?.beeswarm?.length && active.beeswarm_meta" class="grid-12">
          <div id="a13-beeswarm" class="col-12">
            <BeeswarmPanel
              :features="beeswarmRows"
              :meta="active.beeswarm_meta"
              :dataset="shapDataset"
              :model-label="modelLabel(activeId)"
            />
          </div>
        </div>
        <StatePanel
          v-else
          variant="pending"
          compact
          title="蜂群图点位未随契约提供"
          reason="shap_global.json 缺少 models[].beeswarm / beeswarm_meta"
          next-step="在 frontend/ 下执行 python3 scripts/build_explain_contracts.py 重新生成契约"
        />
      </section>

      <!-- ============================ A15 阶段-因子-权重 ============================ -->
      <section class="page__block" aria-labelledby="a15" data-testid="section-a15">
        <header class="page__block-head">
          <span class="page__code">A15</span>
          <h2 id="a15" class="page__block-title">阶段-因子-权重动态决策图谱</h2>
          <span class="page__block-desc">100% 堆叠面积 + 阶段切换 + 字段明细</span>
          <SectionBadge
            :dataset="stageDataset"
            :source="src(stageEnv.meta)"
            endpoint="GET /api/explain/stage-factor"
          />
        </header>

        <StatePanel
          v-if="stageSection.status.value === 'error'"
          variant="error"
          title="阶段-因子契约未能加载"
          :reason="stageSection.error.value?.reason ?? ''"
          :next-step="stageSection.error.value?.nextStep ?? ''"
          :retry="() => void stageSection.retry()"
        />

        <template v-else-if="stageContract">
          <!-- mock 声明放在图上面，不放在图下面 -->
          <aside class="mock-strip" data-testid="stage-mock-strip">
            <span class="mock-strip__badge">契约未就绪 · 占位</span>
            <div class="mock-strip__body">
              <p class="mock-strip__title">
                本区块的<b>阶段划分与权重是占位值</b>，字段排序与 mean|SHAP| 是实测值
              </p>
              <p class="mock-strip__text">{{ stageEnv.meta.disclaimer }}</p>
              <p class="mock-strip__text">
                <b>解锁条件：</b>{{ stageContract.unlock.requires.join('；') }}。
                {{ stageContract.unlock.after }}
              </p>
            </div>
          </aside>

          <StageFactorPanel
            :data="stageContract"
            :dataset="stageDataset"
            :source="src(stageEnv.meta)"
          />
        </template>

        <StatePanel
          v-else
          variant="pending"
          title="阶段-因子契约未就绪"
          reason="stage_factor_weight.json 未生成或结构不符（缺少 stages / factors / weights）"
          next-step="在 frontend/ 下执行 python3 scripts/build_explain_contracts.py"
        />
      </section>

      <!-- 为什么是 0 -->
      <section class="page__block" aria-labelledby="why">
        <header class="page__block-head">
          <span class="page__code">Q</span>
          <h2 id="why" class="page__block-title">为什么解释占比是 0，而不是"模型没学到东西"</h2>
        </header>
        <div class="qa">
          <div class="qa__item">
            <p class="qa__q">模型确实学到了东西吗？</p>
            <p class="qa__a">
              学到了，但很弱：留出集 AUC 0.586（LR）/ 0.576（XGB），置换对照地板 0.477。
              比地板高 0.1 左右，说明存在微弱信号，但不足以支撑放贷决策。
            </p>
          </div>
          <div class="qa__item">
            <p class="qa__q">那为什么一个业务字段都排不进前 20？</p>
            <p class="qa__a">
              面板里 x1–x20 这 20 个「语义相对清楚」的字段，在当前特征筛选后贡献接近噪声；
              真正被模型抓住的全在 x21–x200。字段字典补齐前，没人能说出 x125 代表什么。
            </p>
          </div>
          <div class="qa__item">
            <p class="qa__q">阶段图谱的权重能直接拿去用吗？</p>
            <p class="qa__a">
              不能。阶段划分与权重是占位口径（P0-2 字段字典 + P0-5 融资事件未到位），
              它证明的是「这套图谱跑得通、换阶段数据会跟着变」，不是「哪个阶段该看重什么」。
              能引用的只有字段排序与 mean|SHAP|——那是实测的。
            </p>
          </div>
          <div class="qa__item">
            <p class="qa__q">下一步做什么？</p>
            <p class="qa__a">
              不是调模型，是补字段字典（P0-2）与技术要素/成长轨迹数据（P0-4、P0-5）。
              数据到位后，本页结构不变，柱条上的代号会变成「发明专利授权率」「下一轮融资额」这类可读名，
              阶段图谱的维度归属也不再是拼出来的。
            </p>
          </div>
        </div>
      </section>
    </template>

    <footer class="page__foot cluster">
      <span>下一步：</span>
      <RouterLink :to="{ name: 'model' }" class="page__link">P4 模型与对比</RouterLink>
      <span class="page__foot-sep">·</span>
      <RouterLink :to="{ name: 'data' }" class="page__link">P2 数据与特征</RouterLink>
      <span class="page__foot-sep">·</span>
      <span>局部归因（单企业边扰动 / GNNExplainer）与单企业瀑布图待 FE-16 落地。</span>
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
  margin-bottom: var(--space-1);
}

.page__verdict-text {
  font-size: 13px;
  line-height: 1.85;
  color: var(--color-text);
}

.card {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.card__label {
  font-size: 12px;
  color: var(--color-text-muted);
}

.card__value {
  font-family: var(--font-mono);
  font-size: 24px;
  font-weight: 700;
}

.card__value--warn {
  color: var(--risk-high-risk);
}

.card__hint {
  font-size: 11px;
  line-height: 1.6;
  color: var(--color-text-subtle);
}

.toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.page__switch {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.page__switch button {
  min-height: 32px;
  padding: 0 var(--space-3);
  border: 1px solid var(--color-border-strong);
  border-radius: 16px;
  background: var(--color-surface);
  font-size: 12px;
  color: var(--color-text-muted);
  cursor: pointer;
}

.page__switch button.is-active {
  border-color: var(--color-accent);
  color: var(--color-accent);
  background: var(--color-accent-soft);
}

.sort {
  display: flex;
  align-items: center;
  gap: 4px;
}

.sort__label {
  font-size: 11px;
  color: var(--color-text-subtle);
  margin-right: 2px;
}

.sort__btn {
  min-height: 30px;
  padding: 0 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  font-size: 11.5px;
  color: var(--color-text-muted);
  cursor: pointer;
}

.sort__btn.is-active {
  border-color: var(--color-accent);
  color: var(--color-accent);
  background: var(--color-accent-soft);
}

.toolbar__hint {
  margin-left: auto;
  font-size: 11px;
  color: var(--color-text-subtle);
}

.legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-4);
  margin: var(--space-2) 0 0;
  padding: 0;
  list-style: none;
  font-size: 11px;
  color: var(--color-text-muted);
}

.legend__dot {
  display: inline-block;
  width: 9px;
  height: 9px;
  margin-right: 5px;
  border-radius: 2px;
  vertical-align: middle;
}

.legend__dot--risk {
  background: #b3312c;
}

.legend__dot--protect {
  background: #2f5d8a;
}

.legend__dot--faint {
  background: #b9c0c9;
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

.page__note {
  font-size: 12px;
  line-height: 1.8;
  color: var(--color-text-muted);
}

.table-wrap {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  height: 100%;
  padding: var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.table-wrap__head {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
}

.table-wrap__title {
  font-size: 14px;
  font-weight: 600;
}

.table-wrap__hint {
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
  padding: 6px 8px;
  border-bottom: 1px solid var(--color-border);
  text-align: right;
  white-space: nowrap;
}

.tbl th[scope='col']:nth-child(2),
.tbl th[scope='row'] {
  text-align: left;
}

.tbl thead th {
  position: sticky;
  top: 0;
  font-weight: 600;
  color: var(--color-text-muted);
  background: var(--color-surface-muted);
}

.table-scroll {
  overflow: auto;
}

.table-scroll--tall {
  /* 实测 20 行 × 33px + 表头 = 692px。Top-20 是本页的核心证据，不该被截成
     「默认只露 18 行、剩下靠滚动」——截图归档时那 2 行会被当成数据缺失。 */
  max-height: 700px;
}

.mono {
  font-family: var(--font-mono);
}

.dir {
  font-size: 11px;
  padding: 0 5px;
  border-radius: 8px;
  border: 1px solid currentColor;
}

.dir[data-dir='1'] {
  color: var(--risk-high-risk);
}

.dir[data-dir='-1'] {
  color: var(--color-accent);
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
