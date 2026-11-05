<script setup lang="ts">
/**
 * P4 模型与对比（FE-13 全量版）
 *
 * 四个区块 = 四个接口，各自独立取数、各自带区块级来源角标：
 *   A1 模型架构 + 训练曲线  GET /api/model/train-curve   dataset=synthetic_graph / source=reconstructed_from_anchors
 *   A2 基线对比 + 雷达 + 消融 GET /api/model/benchmark     dataset=real_panel / source=measured
 *   A3 ROC / PR / KS 曲线族  GET /api/model/roc-pr-ks      dataset=real_panel / source=reconstructed_from_anchors
 *   A4 置换对照噪声地板      GET /api/model/noise-floor    dataset=real_panel / source=measured
 *
 * 这页要回答的问题不是"模型有多好"，而是"这点效果有多少是真的"：
 *   - 行级 AUC≈0.59 与噪声地板 0.477 摆在一起看；
 *   - 企业级 AUC 0.47 直接低于抛硬币；
 *   - 图模型对比无图 MLP 只 +0.013。
 * 所以页头先给结论，图表是证据。
 *
 * 两条本页特有的纪律（比别的页更严，因为这里是"性能页"）：
 *   ① A1 是合成图、A2–A4 是真实面板：**任何跨区块的 Y 轴都不许共用**，
 *      区块标题旁必须挂各自角标（SectionBadge 读的就是各自 meta，不缓存）；
 *   ② meta.source 不是 measured 的区块，图注必须写"重建口径"，不允许说"实测结果"。
 */
import { computed } from 'vue'
import { ref } from 'vue'

import ChartPanel from '@/components/ChartPanel.vue'
import MetricBar from '@/components/MetricBar.vue'
import PageSkeleton from '@/components/PageSkeleton.vue'
import RedLineBanner from '@/components/RedLineBanner.vue'
import StatePanel from '@/components/StatePanel.vue'
import UnknownFieldTag from '@/components/UnknownFieldTag.vue'
import ArchitectureDiagram from '@/components/model/ArchitectureDiagram.vue'
import BenchmarkPanel from '@/components/model/BenchmarkPanel.vue'
import NoiseFloorBlock from '@/components/model/NoiseFloorBlock.vue'
import RocPrKsPanel from '@/components/model/RocPrKsPanel.vue'
import SectionBadge from '@/components/model/SectionBadge.vue'
import TrainCurvePanel from '@/components/model/TrainCurvePanel.vue'
import { fetchBenchmark, fetchNoiseFloor, fetchRocPrKs, fetchTrainCurve } from '@/api'
import { useAsyncSection } from '@/composables/useAsyncSection'
import type {
  Benchmark,
  NoiseFloorContract,
  RocPrKs,
  TrainCurve,
} from '@/types/contracts'
import type { ContractMeta, DatasetKind } from '@/types'
import type { ReadableError } from '@/utils/errors'
import { NOISE_FLOOR } from '@/utils/redlines'

/* ------------------------------ 四个区块各自取数 ------------------------------ */
const curveSection = useAsyncSection(fetchTrainCurve)
const benchSection = useAsyncSection(fetchBenchmark)
const rocSection = useAsyncSection(fetchRocPrKs)
const floorSection = useAsyncSection(fetchNoiseFloor)

interface Env<T> {
  data: T | null
  meta: ContractMeta
}

function pick<T>(sec: { data: unknown }): Env<T> {
  const env = sec.data as { data?: T; meta?: ContractMeta } | null
  return { data: (env?.data ?? null) as T | null, meta: (env?.meta ?? {}) as ContractMeta }
}

/** 取数失败必须能点名是哪一块——否则用户只看到"页面挂了"却不知道该找谁 */
const failedSections = computed(() => {
  const list: Array<{ id: string; label: string; error: ReadableError | null }> = [
    { id: 'A1', label: '模型架构与训练曲线', error: curveSection.error.value },
    { id: 'A2', label: '基线对比与消融', error: benchSection.error.value },
    { id: 'A3', label: 'ROC / PR / KS 曲线族', error: rocSection.error.value },
    { id: 'A4', label: '置换对照噪声地板', error: floorSection.error.value },
  ]
  return list.filter((x) => x.error !== null)
})

// 显式传 Ref，别把 Vue 组件实例整个塞进去——那样 pick 读到的是组件代理，永远拿不到 data
const curvesEnv = computed(() => pick<TrainCurve>({ data: curveSection.data.value }))
const benchEnv = computed(() => pick<Benchmark>({ data: benchSection.data.value }))
const rocEnv = computed(() => pick<RocPrKs>({ data: rocSection.data.value }))
const floorEnv = computed(() => pick<NoiseFloorContract>({ data: floorSection.data.value }))

const trainCurve = computed<TrainCurve | null>(() => {
  const d = curvesEnv.value.data
  return d && Array.isArray(d.curves) && d.architecture ? d : null
})
const benchmark = computed<Benchmark | null>(() => {
  const d = benchEnv.value.data
  return d && Array.isArray(d.levels) && d.holdout ? d : null
})
const rocPrKs = computed<RocPrKs | null>(() => {
  const d = rocEnv.value.data
  return d && Array.isArray(d.series) ? d : null
})
const noiseFloor = computed<NoiseFloorContract | null>(() => {
  const d = floorEnv.value.data
  return d && Array.isArray(d.bars) ? d : null
})

/** 每个区块的 dataset / source —— 一律取各自 meta，不跨区块复用、不缓存 */
const ds = (meta: ContractMeta): DatasetKind => (meta.dataset as DatasetKind) ?? 'real_panel'
const src = (meta: ContractMeta) => String(meta.source ?? 'unknown')

const noiseFloorValue = computed(() => {
  const v = benchEnv.value.meta.noise_floor
  return typeof v === 'number' ? v : NOISE_FLOOR
})

/** 首屏骨架：只有在「基准区块还没拿到数据」且仍有区块在加载时才显示 */
const loading = computed(
  () =>
    !benchmark.value &&
    [curveSection, benchSection, rocSection, floorSection].some((s) => s.status.value === 'loading'),
)

/* ------------------------------ 契约口径的数字 ------------------------------ */
const holdoutModels = computed(() => {
  const ORDER = ['lr', 'xgb', 'gat', 'mlp_nograph']
  return [...(benchmark.value?.holdout.models ?? [])].sort(
    (a, b) => ORDER.indexOf(a.model_id) - ORDER.indexOf(b.model_id),
  )
})
const gat = computed(() => holdoutModels.value.find((m) => m.model_id === 'gat'))
const mlp = computed(() => holdoutModels.value.find((m) => m.model_id === 'mlp_nograph'))
const graphGain = computed(() => (gat.value && mlp.value ? gat.value.metrics.auc - mlp.value.metrics.auc : null))

const rowLevel = computed(() => benchmark.value?.levels.find((l) => l.level_id === 'row'))
const companyLevel = computed(() => benchmark.value?.levels.find((l) => l.level_id === 'company'))
const rowLr = computed(() => rowLevel.value?.models.find((m) => m.model_id === 'lr'))
const companyXgb = computed(() => companyLevel.value?.models.find((m) => m.model_id === 'xgb'))
const companyLr = computed(() => companyLevel.value?.models.find((m) => m.model_id === 'lr'))

const REFS = computed(() => [
  { value: 0.5, label: '抛硬币 0.5', tone: 'neutral' as const },
  { value: noiseFloorValue.value, label: '噪声地板', tone: 'danger' as const },
])

/** 行级 vs 企业级：同一批特征，聚合方式不同 */
const levelOption = computed(() => {
  const levels = [rowLevel.value, companyLevel.value].filter(Boolean)
  return {
    animation: false,
    grid: { left: 48, right: 24, top: 32, bottom: 28 },
    tooltip: { trigger: 'axis' },
    legend: {
      top: 0,
      textStyle: { fontSize: 11, color: '#5b6673' },
      data: ['Logistic 回归', 'XGBoost'],
    },
    xAxis: {
      type: 'category',
      data: levels.map((l) => `${l!.level_label}`),
      axisLabel: { fontSize: 10, color: '#5b6673', interval: 0, width: 120, overflow: 'break' },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      min: 0.4,
      max: 0.7,
      axisLabel: { fontSize: 11, color: '#5b6673' },
      splitLine: { lineStyle: { color: '#eef1f5' } },
    },
    series: [
      {
        name: 'Logistic 回归',
        type: 'bar',
        barWidth: 18,
        data: levels.map((l) => l!.models.find((m) => m.model_id === 'lr')?.metrics_mean.auc ?? 0),
        itemStyle: { color: '#b9c0c9' },
        markLine: {
          silent: true,
          symbol: 'none',
          data: [
            {
              yAxis: noiseFloorValue.value,
              lineStyle: { color: '#b3312c', type: 'dashed' },
              label: { formatter: '地板', fontSize: 9, color: '#b3312c' },
            },
          ],
        },
      },
      {
        name: 'XGBoost',
        type: 'bar',
        barWidth: 18,
        data: levels.map((l) => l!.models.find((m) => m.model_id === 'xgb')?.metrics_mean.auc ?? 0),
        itemStyle: { color: '#2f5d8a' },
      },
    ],
  }
})

/** A1 / A3 共用同一个"当前模型"，避免两张图看的不是同一个模型 */
const curveModelId = ref('gat')

const fmt = (v: number | undefined, d = 3) => (typeof v === 'number' ? v.toFixed(d) : '—')

const status = computed(() => benchSection.status.value)
</script>

<template>
  <div class="page">
    <header class="page__head">
      <div class="page__title-row cluster">
        <h1 class="page__title">P4 模型与对比</h1>
        <span class="page__sub">4 个接口 · train-curve / benchmark / roc-pr-ks / noise-floor</span>
        <span class="page__meta">
          数据来源 <code>{{ ds(benchEnv.meta) }}</code>
          <template v-if="benchEnv.meta.n_samples"> · n={{ benchEnv.meta.n_samples }}</template>
          <span class="page__badge" :data-measured="src(benchEnv.meta) === 'measured'" data-testid="page-source-badge">
            {{ src(benchEnv.meta) === 'measured' ? '实测结果' : '占位数据' }}
          </span>
        </span>
      </div>
      <p class="page__lede">
        这一页回答的不是「模型多好」，而是「这点效果有多少是真的」。四个模型放在同一张留出集（698 条 / 75 正例）上，
        旁边摆着置换对照的噪声地板；行级与企业级分开呈现，因为把同一企业的多个月观测聚成一条之后，信号会消失。
        训练曲线单独放在第一块，并明确标注：那是在<b>合成六类实体图</b>上跑的，与真实面板不是同一个数据集。
      </p>
    </header>

    <RedLineBanner scope="P4 模型与对比" />

    <!-- 区块级失败清单：哪一块挂了、原因是什么，写清楚 -->
    <aside v-if="failedSections.length" class="page__failed" role="alert" data-testid="failed-list">
      <b>部分区块未能取数（{{ failedSections.length }} 块）：</b>
      <span v-for="f in failedSections" :key="f.id" class="page__failed-item">
        {{ f.id }} {{ f.label }} —— {{ f.error?.reason }}
      </span>
    </aside>

    <PageSkeleton v-if="loading" :kpi-count="4" :charts="[320, 320]" />

    <!-- ============================ A1 架构 + 训练曲线 ============================ -->
    <section class="page__block" aria-labelledby="a1" data-testid="section-a1">
      <header class="page__block-head">
        <span class="page__code">A1</span>
        <h2 id="a1" class="page__block-title">模型架构与训练曲线</h2>
        <span class="page__block-desc">A08 架构 · A09 loss/AUC 双轴</span>
        <SectionBadge
          :dataset="ds(curvesEnv.meta)"
          :source="src(curvesEnv.meta)"
          endpoint="GET /api/model/train-curve"
        />
      </header>

      <StatePanel
        v-if="curveSection.status.value === 'error'"
        variant="error"
        title="训练曲线未能加载"
        :reason="curveSection.error.value?.reason ?? ''"
        :next-step="curveSection.error.value?.nextStep ?? ''"
        :retry="() => void curveSection.retry()"
      />
      <template v-else-if="trainCurve">
        <ArchitectureDiagram :architecture="trainCurve.architecture" />
        <TrainCurvePanel
          v-model:model-id="curveModelId"
          :data="trainCurve"
          :dataset="ds(curvesEnv.meta)"
          :noise-floor="null"
        />
        <p class="page__note page__note--warn" data-testid="synthetic-note">
          <b>口径提示（红线四）：</b>{{ trainCurve.highlight.note }}
          合成图上 STGNN 架构验证 AUC <b class="mono">{{ trainCurve.synthetic_anchor }}</b> 只属于本区块，
          不进本页任何真实面板的坐标轴。
        </p>
      </template>
      <StatePanel
        v-else
        variant="pending"
        title="训练曲线契约未就绪"
        reason="train_curve.json 未生成或结构不符（缺少 curves / architecture）"
        next-step="在 frontend/ 下执行 python3 scripts/build_model_contracts.py"
      />
    </section>

    <!-- ============================ A2 基线对比 + 消融 ============================ -->
    <section class="page__block" aria-labelledby="a2" data-testid="section-a2">
      <header class="page__block-head">
        <span class="page__code">A2</span>
        <h2 id="a2" class="page__block-title">基线对比与消融实验</h2>
        <span class="page__block-desc">A10 分组柱 + 雷达 + 消融</span>
        <SectionBadge
          :dataset="ds(benchEnv.meta)"
          :source="src(benchEnv.meta)"
          endpoint="GET /api/model/benchmark"
        />
      </header>

      <StatePanel
        v-if="status === 'error'"
        variant="error"
        title="模型对比数据未能加载"
        :reason="benchSection.error.value?.reason ?? ''"
        :next-step="benchSection.error.value?.nextStep ?? ''"
        :retry="() => void benchSection.retry()"
      />
      <template v-else-if="benchmark">
        <!-- 结论先行 -->
        <section class="page__verdict" aria-labelledby="verdict">
          <h3 id="verdict" class="page__verdict-title">结论先行</h3>
          <ul class="page__verdict-list" data-testid="benchmark-notes">
            <li v-for="n in benchmark.notes" :key="n">{{ n }}</li>
          </ul>
          <ul v-if="benchmark.conclusion" class="page__verdict-list page__verdict-list--sub">
            <li v-for="p in benchmark.conclusion.points" :key="p">{{ p }}</li>
          </ul>
        </section>

        <!-- KPI -->
        <div class="grid-12">
          <div class="col-3 card">
            <span class="card__label">行级 AUC（LR）</span>
            <strong class="card__value">{{ fmt(rowLr?.metrics_mean.auc) }}</strong>
            <span class="card__hint">
              噪声地板 {{ fmt(noiseFloorValue) }}，差 {{ fmt((rowLr?.metrics_mean.auc ?? 0) - noiseFloorValue) }}
            </span>
          </div>
          <div class="col-3 card">
            <span class="card__label">企业级 AUC（XGB）</span>
            <strong class="card__value card__value--warn">{{ fmt(companyXgb?.metrics_mean.auc) }}</strong>
            <span class="card__hint">低于 0.5：聚合后信号消失</span>
          </div>
          <div class="col-3 card">
            <span class="card__label">图模型增益（GAT − 无图 MLP）</span>
            <strong class="card__value">{{ graphGain === null ? '—' : (graphGain >= 0 ? '+' : '') + fmt(graphGain) }}</strong>
            <span class="card__hint">同特征、同划分，只差图结构</span>
          </div>
          <div class="col-3 card">
            <span class="card__label">留出集</span>
            <strong class="card__value">{{ benchmark.holdout.n_test }}</strong>
            <span class="card__hint">正例 {{ benchmark.holdout.n_pos }} · 训练 {{ benchmark.holdout.n_train }}（按 userid 分组）</span>
          </div>
        </div>

        <BenchmarkPanel
          v-if="benchmark.grouped && benchmark.radar && benchmark.ablation"
          :benchmark="benchmark"
          :dataset="ds(benchEnv.meta)"
          :noise-floor="noiseFloorValue"
        />

        <!-- 2×2 对照（有图/无图 × 技术要素/无技术要素） -->
        <section v-if="benchmark.matrix" class="page__matrix" aria-labelledby="matrix">
          <header class="page__matrix-head">
            <h3 id="matrix">2×2 对照矩阵：图结构的增益是在什么条件下测的</h3>
            <span v-if="benchmark.matrix.incomplete" class="page__matrix-flag">
              有 {{ benchmark.matrix.cells.flat().filter((c) => c.status === 'blocked').length }} 格缺数据（P0-4 技术要素）
            </span>
          </header>
          <table class="matrix" data-testid="matrix-table">
            <thead>
              <tr>
                <th scope="col" />
                <th v-for="c in benchmark.matrix.cols" :key="c" scope="col">{{ c }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(r, ri) in benchmark.matrix.rows" :key="r">
                <th scope="row">{{ r }}</th>
                <td
                  v-for="(cell, ci) in benchmark.matrix.cells[ri]"
                  :key="ci"
                  :data-status="cell.status"
                >
                  <template v-if="cell.status === 'measured'">
                    <b class="mono">{{ cell.auc?.toFixed(4) }}</b>
                    <span class="matrix__meta">{{ cell.model_id }}</span>
                  </template>
                  <template v-else>
                    <span class="matrix__blocked">缺数据</span>
                    <span class="matrix__meta">{{ (cell.blocked_by ?? []).join(' / ') }}</span>
                  </template>
                </td>
              </tr>
            </tbody>
          </table>
          <p class="page__note">{{ benchmark.matrix.note }}</p>
        </section>

        <!-- 行级 vs 企业级 -->
        <ChartPanel
          title="行级 vs 企业级（同特征，不同聚合）"
          :dataset="ds(benchEnv.meta)"
          :n-samples="rowLevel?.n_samples"
          :noise-floor="noiseFloorValue"
          :option="levelOption"
          :height="320"
        >
          <p class="page__note">
            行级 {{ rowLevel?.n_samples }} 条 → 企业级 {{ companyLevel?.n_samples }} 条：把同一企业的多个月观测合成一条后，
            同一模型的 AUC 从 {{ fmt(rowLr?.metrics_mean.auc) }} 掉到 {{ fmt(companyLr?.metrics_mean.auc) }}，
            已经跌破 0.5 的抛硬币线。这不是模型退化，是「聚合把信号平均掉了」。
          </p>
        </ChartPanel>

        <!-- 指标明细 -->
        <section class="page__sub-block" aria-labelledby="holdout-table">
          <header class="page__block-head">
            <span class="page__code">T1</span>
            <h3 id="holdout-table" class="page__block-title">留出集指标明细</h3>
            <span class="page__block-desc">阈值 0.5 下的指标；阈值不在这里调，避免在测试集上调参</span>
          </header>
          <div class="table-scroll">
            <table class="tbl" data-testid="holdout-table">
              <thead>
                <tr>
                  <th scope="col">模型</th>
                  <th scope="col">AUC</th>
                  <th scope="col">KS</th>
                  <th scope="col">准确率</th>
                  <th scope="col">精确率</th>
                  <th scope="col">召回率</th>
                  <th scope="col">F1</th>
                  <th scope="col">最佳 F1（阈值）</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="m in holdoutModels" :key="m.model_id" :data-emph="m.model_id === 'gat'">
                  <th scope="row">{{ m.model_label }}</th>
                  <td class="mono">{{ fmt(m.metrics.auc, 4) }}</td>
                  <td class="mono">{{ fmt(m.metrics.ks, 4) }}</td>
                  <td class="mono">{{ fmt(m.metrics.accuracy) }}</td>
                  <td class="mono">{{ fmt(m.metrics.precision) }}</td>
                  <td class="mono">{{ fmt(m.metrics.recall) }}</td>
                  <td class="mono">{{ fmt(m.metrics.f1) }}</td>
                  <td class="mono">{{ fmt(m.best_f1) }}（{{ m.best_f1_threshold }}）</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p class="page__note">
            召回率 0.6–0.72 看着不低，是靠把阈值压到 0.5、正例权重放大到负例的 9.56 倍换来的；
            同时精确率只有 0.13——「抓到的多是误报」。指标要成对读，不能只挑好看的那个。
          </p>
        </section>

        <!-- 5 折交叉验证与置换对照 -->
        <section class="page__sub-block" aria-labelledby="cv">
          <header class="page__block-head">
            <span class="page__code">T2</span>
            <h3 id="cv" class="page__block-title">5 折交叉验证与置换对照</h3>
            <span class="page__block-desc">均值 ± 标准差；打乱标签那一组就是噪声地板本身</span>
          </header>
          <div class="cv-grid">
            <div v-for="lv in benchmark.levels" :key="lv.level_id" class="cv-card" :data-control="lv.is_permutation_control">
              <h4 class="cv-card__title">
                {{ lv.level_label }}
                <span v-if="lv.is_permutation_control" class="cv-card__tag">对照</span>
              </h4>
              <p class="cv-card__meta">
                n={{ lv.n_samples }} · 组 {{ lv.n_groups }} · {{ lv.n_splits }} 折 · top-{{ lv.k_features }} 特征
              </p>
              <MetricBar
                v-for="m in lv.models"
                :key="m.model_id"
                :label="m.model_label"
                :value="m.metrics_mean.auc"
                :std="m.metrics_std.auc"
                :refs="lv.is_permutation_control ? [] : REFS"
                :emphasize="lv.is_permutation_control"
              />
            </div>
          </div>
          <p class="page__note">
            误差带是 5 折标准差。行级 AUC 0.596 ± 0.040 与打乱标签 0.492 ± 0.056 的区间有明显重叠——
            这也是为什么本项目把「能否超过噪声地板」当成判断信号真伪的第一道闸，而不是只看 AUC 绝对值。
          </p>
        </section>

        <!-- 特征口径 -->
        <section class="page__sub-block" aria-labelledby="feat">
          <header class="page__block-head">
            <span class="page__code">T3</span>
            <h3 id="feat" class="page__block-title">进入模型的 top-{{ benchmark.holdout.selected_features.length }} 特征</h3>
            <span class="page__block-desc">按 |点二列相关| 在折内训练集上筛选，无跨折泄漏</span>
          </header>
          <ul class="feat-list">
            <li v-for="f in benchmark.holdout.selected_features" :key="f">
              <UnknownFieldTag :field="f" :semantic-known="false" />
            </li>
          </ul>
          <p class="page__note">
            全部是 x21–x200 的语义未知字段：字段字典未补齐前，模型层能证明「有信号」，但无法说明「信号是什么」。
          </p>
        </section>
      </template>
      <StatePanel
        v-else
        variant="pending"
        title="基准对比契约未就绪"
        reason="benchmark.json 缺失或结构不符（缺少 levels / holdout）"
        next-step="重跑 python3 scripts/build_model_contracts.py"
      />
    </section>

    <!-- ============================ A3 ROC / PR / KS ============================ -->
    <section class="page__block" aria-labelledby="a3" data-testid="section-a3">
      <header class="page__block-head">
        <span class="page__code">A3</span>
        <h2 id="a3" class="page__block-title">ROC / PR / KS 曲线族</h2>
        <span class="page__block-desc">A11 曲线族 + KS 标注</span>
        <SectionBadge
          :dataset="ds(rocEnv.meta)"
          :source="src(rocEnv.meta)"
          :updated-at="String(rocEnv.meta.generated_at ?? '')"
          endpoint="GET /api/model/roc-pr-ks"
        />
      </header>

      <StatePanel
        v-if="rocSection.status.value === 'error'"
        variant="error"
        title="曲线族未能加载"
        :reason="rocSection.error.value?.reason ?? ''"
        :next-step="rocSection.error.value?.nextStep ?? ''"
        :retry="() => void rocSection.retry()"
      />
      <RocPrKsPanel
        v-else-if="rocPrKs"
        :data="rocPrKs"
        :dataset="ds(rocEnv.meta)"
        :noise-floor="typeof rocEnv.meta.noise_floor === 'number' ? rocEnv.meta.noise_floor : noiseFloorValue"
      />
      <StatePanel
        v-else
        variant="pending"
        title="曲线族契约未就绪"
        reason="roc_pr_ks.json 未生成或缺少 series"
        next-step="重跑 python3 scripts/build_model_contracts.py"
      />
    </section>

    <!-- ============================ A4 噪声地板 ============================ -->
    <section class="page__block" aria-labelledby="a4" data-testid="section-a4">
      <header class="page__block-head">
        <span class="page__code">A4</span>
        <h2 id="a4" class="page__block-title">置换对照与噪声地板</h2>
        <span class="page__block-desc">A12 观察组 vs 对照组</span>
        <SectionBadge
          :dataset="ds(floorEnv.meta)"
          :source="src(floorEnv.meta)"
          :updated-at="String(floorEnv.meta.generated_at ?? '')"
          endpoint="GET /api/model/noise-floor"
        />
      </header>

      <StatePanel
        v-if="floorSection.status.value === 'error'"
        variant="error"
        title="噪声地板数据未能加载"
        :reason="floorSection.error.value?.reason ?? ''"
        :next-step="floorSection.error.value?.nextStep ?? ''"
        :retry="() => void floorSection.retry()"
      />
      <NoiseFloorBlock
        v-else-if="noiseFloor"
        :data="noiseFloor"
        :dataset="ds(floorEnv.meta)"
        :meta="floorEnv.meta"
      />
      <StatePanel
        v-else
        variant="pending"
        title="噪声地板契约未就绪"
        reason="noise_floor.json 未生成或缺少 bars"
        next-step="重跑 python3 scripts/build_model_contracts.py"
      />
    </section>

    <footer class="page__foot cluster">
      <span>下一步：</span>
      <RouterLink :to="{ name: 'explain' }" class="page__link">P5 可解释性</RouterLink>
      <span class="page__foot-sep">·</span>
      <span>红线：真实面板结果与合成图结果不得同图比较（A1 的曲线与 0.9149 不进 A2–A4 的任何坐标轴）。</span>
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

.page__badge {
  margin-left: var(--space-2);
  padding: 0 6px;
  border-radius: 10px;
  font-size: 11px;
  border: 1px solid var(--color-border-strong);
  color: var(--color-text-muted);
}

.page__badge[data-measured='true'] {
  border-color: var(--color-accent);
  color: var(--color-accent);
  background: var(--color-accent-soft);
}

.page__lede {
  max-width: 960px;
  font-size: 13px;
  line-height: 1.75;
  color: var(--color-text-muted);
}

.page__failed {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--risk-high-risk);
  border-left-width: 3px;
  border-radius: var(--radius-sm);
  background: var(--risk-high-risk-bg);
  font-size: 12px;
  color: var(--color-text);
}

.page__failed-item {
  color: var(--risk-high-risk);
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

.page__verdict-list {
  margin: 0;
  padding-left: 1.1em;
  font-size: 12.5px;
  line-height: 1.9;
  color: var(--color-text);
}

.page__verdict-list--sub {
  margin-top: var(--space-2);
  color: var(--color-text-muted);
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
  color: var(--color-text-subtle);
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

.page__sub-block {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-4);
  border-top: 1px dashed var(--color-border);
}

.page__block-head {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--space-3);
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

.page__block-desc {
  font-size: 12px;
  color: var(--color-text-muted);
}

.page__note {
  font-size: 12px;
  line-height: 1.8;
  color: var(--color-text-muted);
}

.page__note--warn {
  padding: var(--space-2) var(--space-3);
  border-left: 3px solid var(--risk-watch);
  background: var(--risk-watch-bg);
}

.page__matrix {
  padding: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.page__matrix-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.page__matrix-head h3 {
  font-size: 14px;
  font-weight: 600;
}

.page__matrix-flag {
  padding: 0 8px;
  border-radius: 10px;
  border: 1px solid var(--risk-watch);
  color: var(--risk-watch);
  background: var(--risk-watch-bg);
  font-size: 11px;
}

.matrix {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}

.matrix th,
.matrix td {
  padding: 10px;
  border: 1px solid var(--color-border);
  text-align: center;
}

.matrix thead th {
  font-weight: 600;
  font-size: 11.5px;
  color: var(--color-text-muted);
  background: var(--color-surface-muted);
}

.matrix th[scope='row'] {
  text-align: left;
  font-weight: 600;
  white-space: nowrap;
}

.matrix td[data-status='blocked'] {
  background: repeating-linear-gradient(
    45deg,
    var(--color-surface-muted),
    var(--color-surface-muted) 8px,
    var(--color-surface) 8px,
    var(--color-surface) 16px
  );
}

.matrix__blocked {
  color: var(--color-text-subtle);
  font-size: 12px;
}

.matrix__meta {
  display: block;
  margin-top: 2px;
  font-size: 10.5px;
  color: var(--color-text-subtle);
  font-family: var(--font-mono);
}

.tbl {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}

.tbl th,
.tbl td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--color-border);
  text-align: right;
  white-space: nowrap;
}

.tbl th[scope='col']:first-child,
.tbl th[scope='row'] {
  text-align: left;
}

.tbl thead th {
  font-weight: 600;
  color: var(--color-text-muted);
  background: var(--color-surface-muted);
}

.tbl tr[data-emph='true'] {
  background: var(--color-accent-soft);
}

.mono {
  font-family: var(--font-mono);
}

.cv-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-4);
}

@media (max-width: 1180px) {
  .cv-grid {
    grid-template-columns: 1fr;
  }
}

.cv-card {
  padding: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.cv-card[data-control='true'] {
  border-color: var(--risk-high-risk);
  background: var(--risk-high-risk-bg);
}

.cv-card__title {
  font-size: 13px;
  font-weight: 600;
}

.cv-card__tag {
  margin-left: var(--space-2);
  padding: 0 6px;
  font-size: 10px;
  border-radius: 8px;
  color: var(--risk-high-risk);
  border: 1px solid var(--risk-high-risk);
}

.cv-card__meta {
  margin: var(--space-1) 0 var(--space-3);
  font-size: 11px;
  color: var(--color-text-subtle);
}

.feat-list {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
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
