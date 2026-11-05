<script setup lang="ts">
/**
 * 单企业深度诊断 P7（FE-16 全量版）
 *
 * 六个区块 = 两个接口，各自独立取数、各自带区块级来源角标：
 *   档案头部    GET /api/enterprise/{userid}          dataset=real_panel / source=contract_spec
 *   信用评分   同上（同一份契约的 prior / risk_bands）
 *   特征雷达   同上（radar）
 *   SHAP 瀑布  GET /api/explain/local/{userid}        dataset=real_panel / source=reconstructed_from_anchors
 *   局部图谱   同上（neighbours，来自合成图）
 *   时序热力   同上（series）
 *
 * ## 这一页要传达的核心事实
 *
 * 「单企业诊断」听起来是最有商业价值的一页，也是**最容易造假**的一页：给它编一个
 * 「杭州××科技有限公司」，配一个 78.5 分和一排漂亮的瀑布柱，没有任何人能当场看出问题。
 *
 * 所以这一页做了三件事，缺一件都不能交付：
 *   ① **匿名态优先**：映射键（P0-1）没到，就只显示 `u0001` 这种编号，并在档案头部说明
 *      「映射键到位后由后端填企业名，页面零改动」—— 匿名态与实名态是同一套结构；
 *   ② **每个区块的角标读各自契约的 meta**，不许一页挂一颗角标了事：
 *      档案是 `契约规格`、局部归因是 `锚点重建`、邻居来自 `合成图`，三种口径三种语气；
 *   ③ **三条链路自洽**：瀑布逐段累加 = 总分（`waterfallSegments` 断言）、
 *      热力逐月累加 = 特征总贡献（`heatCells` 断言）、雷达缺维度 = 缺口而不是 0。
 *      这三条任何一条破了，图就不再解释同一个东西。
 *
 * 两条本页特有的纪律（其余四条红线照旧）：
 *   · 红线二：所有字段一律 `xNNN`，业务名走 `label_ref`，一个业务名都不许出现；
 *   · 红线三：雷达 / 瀑布 / 热力都不是 AUC 口径，`noiseFloor` 显式传 `null`；
 *     唯一的 AUC 只以文字出现在「这个分数能说明什么」里，且必须同句给出噪声地板 0.477。
 */
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import PageSkeleton from '@/components/PageSkeleton.vue'
import RedLineBanner from '@/components/RedLineBanner.vue'
import StatePanel from '@/components/StatePanel.vue'
import UnknownFieldTag from '@/components/UnknownFieldTag.vue'
import SectionBadge from '@/components/model/SectionBadge.vue'
import EnterpriseHeader from '@/components/enterprise/EnterpriseHeader.vue'
import ScoreGauge from '@/components/enterprise/ScoreGauge.vue'
import RadarPanel from '@/components/enterprise/RadarPanel.vue'
import SHAPWaterfall from '@/components/enterprise/SHAPWaterfall.vue'
import LocalGraphPanel from '@/components/enterprise/LocalGraphPanel.vue'
import TemporalHeat from '@/components/enterprise/TemporalHeat.vue'
import { fetchEnterpriseDetail, fetchLocalExplain } from '@/api'
import { useAsyncSection } from '@/composables/useAsyncSection'
import type {
  EnterpriseDetailContract,
  EnterpriseRow,
  LocalExplainContract,
  LocalExplainEntry,
} from '@/types/contracts'
import type { ContractMeta, DatasetKind } from '@/types'
import { localVerdict } from '@/utils/enterprise'

const route = useRoute()
const router = useRouter()

/**
 * 当前企业：路由参数 → 契约里的第一个。
 *
 * 路由 `userid` 声明为可选（FE-00 的路由表），无参时落到队列第一家，
 * **不改写 URL** —— 改写会让人以为「你选的就是 u0001」，
 * 实际是「你没选，我们给了一个默认」。地址栏里本来就没有的东西，不要替他填上。
 */
const currentUserid = computed(() => {
  const raw = route.params.userid
  return typeof raw === 'string' && raw.trim() ? raw.trim() : ''
})

let mounted = false

const detailSection = useAsyncSection<MemoryEnvelope<EnterpriseDetailContract>>(
  () => fetchEnterpriseDetail('*'),
)
const localSection = useAsyncSection<MemoryEnvelope<LocalExplainContract>>(
  () => fetchLocalExplain('*'),
)

/**
 * 取数随路由变化重取。
 *
 * ⚠️ 这里不能用 `useAsyncSection` 的 `immediate` 一次性拉全部企业：
 * 真实后端 `/api/enterprise/{userid}` 一次只返回一家，切换企业必须重新请求。
 * 目前契约是一份「匿名队列」，页面在客户端按 userid 从 enterprises 里挑；
 * 换成真实后端时，只要把下面两个 loader 改成带参数的版本，其余代码不动。
 */
watch(
  () => currentUserid.value,
  () => {
    // 首次挂载时 `useAsyncSection` 已经在 immediate 阶段发过请求，这里再 refresh 一次
    // 会撞上「onScopeDispose 不算过期、但请求 id 已递增」的竞态（表现为永久停在未就绪）。
    // 所以只在**用户真的切了企业**之后才重新取数。
    if (!mounted) {
      mounted = true
      return
    }
    void detailSection.refresh()
    void localSection.refresh()
  },
)

/**
 * 内存态取数（当前契约阶段）。
 *
 * 真实后端 `/api/enterprise/{userid}` 一次只返回**一家**；现在契约是一份 12 家匿名企业的
 * 观测集，所以页面把整份契约当「本地库」，按 userid 在客户端挑。
 * 换成真实后端时只需把上面两个 loader 改成带 `userid` 参数的版本，下面所有 pick 逻辑不变 ——
 * 这也是为什么这里刻意**不**把「从数组里挑一家」的逻辑写进 api 层：
 * api 层一旦掺进挑选逻辑，接真实后端时就得再拆一次。
 */
type MemoryEnvelope<T> = { data?: T; meta?: ContractMeta }

function pick<T>(sec: { data: unknown }): { data: T | null; meta: ContractMeta } {
  const env = sec.data as { data?: T; meta?: ContractMeta } | null
  return { data: (env?.data ?? null) as T | null, meta: (env?.meta ?? {}) as ContractMeta }
}

const detailEnv = computed(() => pick<EnterpriseDetailContract>({ data: detailSection.data.value }))
const localEnv = computed(() => pick<LocalExplainContract>({ data: localSection.data.value }))

/** 结构守卫：信封合法 ≠ 契约正确（缺 enterprises / entries 一律当没到） */
const detail = computed<EnterpriseDetailContract | null>(() => {
  const d = detailEnv.value.data
  const ok =
    d &&
    Array.isArray(d.enterprises) &&
    d.enterprises.length > 0 &&
    Array.isArray(d.radar) &&
    d.risk_bands &&
    Array.isArray(d.risk_bands.cuts)
  return ok ? d : null
})

const localData = computed<LocalExplainContract | null>(() => {
  const d = localEnv.value.data
  return d && Array.isArray(d.entries) ? d : null
})

const ds = (meta: ContractMeta): DatasetKind => (meta.dataset as DatasetKind) ?? 'real_panel'
const src = (meta: ContractMeta) =>
  String(meta.source ?? (meta.contract_status === 'mock' ? 'contract_spec' : 'measured'))

const detailDataset = computed(() => ds(detailEnv.value.meta))
const localDataset = computed(() => ds(localEnv.value.meta))

const enterprises = computed<EnterpriseRow[]>(() => detail.value?.enterprises ?? [])

const enterprise = computed<EnterpriseRow | null>(() => {
  const list = enterprises.value
  if (!list.length) return null
  const want = currentUserid.value
  return list.find((e) => e.userid === want) ?? list[0]
})

/** 请求的 userid 不在队列里：如实说明，不悄悄换成第一家 */
const notFound = computed(
  () => currentUserid.value !== '' && !enterprises.value.some((e) => e.userid === currentUserid.value),
)

const entry = computed<LocalExplainEntry | null>(() => {
  const list = localData.value?.entries ?? []
  if (!list.length || !enterprise.value) return null
  return list.find((e) => e.userid === enterprise.value!.userid) ?? null
})

/** A15 的阶段口径（只是阶段名，数值不进本页） */
const STAGE_LABELS: Record<string, string> = {
  s0: '种子期',
  s1: '初创期',
  s2: '成长期',
  s3: '扩张期',
}

/** 真实面板的企业级中位观测（来自 detail.meta.anchors，取不到给 2） */
const medianMonths = computed(() => {
  const v = detail.value?.anchors?.median_months
  return typeof v === 'number' ? v : 2
})

const verdict = computed(() => (entry.value ? localVerdict(entry.value.features) : null))

const loading = computed(
  () => !detail.value && (detailSection.status.value === 'loading' || detailSection.status.value === 'idle'),
)

const localBlocked = computed(() => {
  const st = String(localData.value?.status ?? '')
  return !entry.value && ['LABEL_PENDING', 'label_pending'].includes(st)
})

function selectEnterprise(userid: string) {
  void router.push({ name: 'enterprise', params: { userid } })
}
</script>

<template>
  <div class="page">
    <header class="page__head">
      <div class="page__title-row cluster">
        <h1 class="page__title">P7 单企业深度诊断</h1>
        <span class="page__sub">2 个接口 · GET /api/enterprise/{userid} · GET /api/explain/local/{userid}</span>
        <span v-if="enterprise" class="page__meta">当前 <code>{{ enterprise.userid }}</code></span>
      </div>
      <p class="page__lede">
        这一页回答「这家企业为什么是这个分数」：档案 → 分数与档位 → 三类维度 → 逐特征归因 →
        它在图里连了谁 → 逐月变化。六块看的是同一个对象，任何一块的口径不对，整页就自相矛盾。
      </p>
    </header>

    <RedLineBanner scope="P7 单企业深度诊断" :affected="['A18', 'A19', 'A20']" />
    <PageSkeleton v-if="loading" :kpi-count="3" :charts="[320, 320]" />

    <StatePanel
      v-else-if="detailSection.status.value === 'error'"
      variant="error"
      title="企业档案未能加载"
      :reason="detailSection.error.value?.reason ?? ''"
      :next-step="detailSection.error.value?.nextStep ?? ''"
      :retry="() => void detailSection.retry()"
    />

    <!-- 阻塞态：后端在 P0-1 / P0-3 未到位时的真实返回（LABEL_PENDING），不是「暂无数据」 -->
    <StatePanel
      v-else-if="!detail || !enterprise"
      variant="pending"
      title="单企业档案尚未解锁"
      :reason="
        detail
          ? '接口返回的 enterprises 为空。'
          : (detailEnv.meta.contract_status === 'label_pending'
              ? '后端返回 LABEL_PENDING：userid→企业映射键（P0-1）与 label 口径（P0-3）未到位。'
              : 'enterprise_detail.json 未生成或结构不符（缺少 enterprises / radar / risk_bands.cuts）。')
      "
      next-step="补齐 P0-1 映射键与 P0-3 标签口径后重试；契约形状见 frontend/docs/FE-16-交付说明.md §3。"
      :retry="() => void detailSection.retry()"
      retry-text="重新取数"
    />

    <template v-else>
      <StatePanel
        v-if="notFound"
        variant="empty"
        compact
        :title="`队列里没有 ${currentUserid}`"
        reason="当前契约是 12 家匿名企业的观测集，请求的 userid 不在其中。"
        next-step="用右上角的选择器切换，或直接访问 /enterprise/u0001。下面是队列第一家（不改写地址栏）。"
      />

      <!-- ============================ 区块一：档案头部 ============================ -->
      <EnterpriseHeader
        :enterprise="enterprise"
        :peers="enterprises"
        :dataset="detailDataset"
        :source="src(detailEnv.meta)"
        :stage-label="STAGE_LABELS[enterprise.stage_id]"
        @select="selectEnterprise"
      />

      <!-- ============================ 区块二：评分仪表盘 ============================ -->
      <section class="page__block" aria-labelledby="a18" data-testid="p7-section-score">
        <header class="page__block-head">
          <span class="page__code">A18</span>
          <h2 id="a18" class="page__block-title">信用评分与档位</h2>
          <span class="page__block-desc">0–100 + 五档色带 + PD（相对口径）</span>
          <SectionBadge
            :dataset="detailDataset"
            :source="src(detailEnv.meta)"
            endpoint="GET /api/enterprise/{userid}"
          />
        </header>

        <ScoreGauge
          :enterprise="enterprise"
          :cuts="detail.risk_bands.cuts"
          :dataset="detailDataset"
          :prior="detail.prior"
          :n-samples="detailEnv.meta.n_samples"
        />

        <aside v-if="verdict" class="page__verdict" data-testid="p7-verdict">
          <span class="page__verdict-title">一句话读法</span>
          <p class="page__verdict-body">
            本企业的 {{ entry!.features.length }} 个主要字段里，正向贡献合计
            <b class="is-up">+{{ verdict.push.toFixed(4) }}</b>（log-odds），负向合计
            <b class="is-down">{{ verdict.pull.toFixed(4) }}</b>，净效应
            <b>{{ verdict.net >= 0 ? '+' : '' }}{{ verdict.net.toFixed(4) }}</b>
            <template v-if="verdict.net >= 0">，即被<b>推高</b>违约可能性；</template>
            <template v-else>，即被<b>压低</b>违约可能性；</template>
            单点影响最大的是
            <UnknownFieldTag v-if="verdict.topUp" :field="verdict.topUp" />
            <span v-else>—</span>，最大的反向项是
            <UnknownFieldTag v-if="verdict.topDown" :field="verdict.topDown" />
            <span v-else>—</span>。
          </p>
        </aside>
      </section>

      <!-- ============================ 区块三：特征雷达 ============================ -->
      <section class="page__block" aria-labelledby="a19" data-testid="p7-section-radar">
        <header class="page__block-head">
          <span class="page__code">A19</span>
          <h2 id="a19" class="page__block-title">三类维度特征雷达</h2>
          <span class="page__block-desc">技术要素 / 成长轨迹 / 融合特征 · 各 4 项 · 同行基线</span>
          <SectionBadge
            :dataset="detailDataset"
            :source="src(detailEnv.meta)"
            endpoint="GET /api/enterprise/{userid}"
          />
        </header>
        <RadarPanel :radar="detail.radar" :dataset="detailDataset" :n-samples="detailEnv.meta.n_samples" />
      </section>

      <!-- ============================ 区块四：局部归因 ============================ -->
      <section class="page__block" aria-labelledby="a20" data-testid="p7-section-local">
        <header class="page__block-head">
          <span class="page__code">A20</span>
          <h2 id="a20" class="page__block-title">逐特征归因（局部 SHAP）</h2>
          <span class="page__block-desc">瀑布图 + 明细表</span>
          <SectionBadge
            :dataset="localDataset"
            :source="src(localEnv.meta)"
            endpoint="GET /api/explain/local/{userid}"
          />
        </header>

        <template v-if="entry">
          <p class="page__note" data-testid="p7-local-method">
            {{ localData?.status_note }}
            <span class="page__note-dim">
              一致性自校验：mean|φ| 与实测 mean|SHAP| 秩相关 ρ =
              {{ entry.consistency.global_rho?.toFixed(3) ?? '—' }}，方向与全局一致率
              {{ (entry.consistency.sign_agree * 100).toFixed(0) }}%。
            </span>
          </p>

          <SHAPWaterfall
            :entry="entry"
            :dataset="localDataset"
            :n-samples="localEnv.meta.n_samples"
          />
        </template>

        <StatePanel
          v-else-if="localSection.status.value === 'error'"
          variant="error"
          title="局部归因未能加载"
          :reason="localSection.error.value?.reason ?? ''"
          :next-step="localSection.error.value?.nextStep ?? ''"
          :retry="() => void localSection.retry()"
        />

        <StatePanel
          v-else-if="localBlocked"
          variant="pending"
          title="该企业的局部归因暂不产出（LABEL_PENDING）"
          reason="标签口径（P0-3）未定，违约概率这个分母还没有定义，逐特征归因算出来也是无意义的。"
          next-step="P0-3 确认后由后端直出 /api/explain/local/{userid}；页面零改动。"
        />

        <StatePanel
          v-else
          variant="pending"
          title="契约里没有这家企业的局部归因"
          reason="shap_local.json 的 entries 中没有当前 userid（契约与企业队列不同步）。"
          next-step="重跑 python3 scripts/build_enterprise_contracts.py --all，或核对前端传入的 userid。"
        />
      </section>

      <!-- ============================ 区块五：局部图谱 ============================ -->
      <section class="page__block" aria-labelledby="a14g" data-testid="p7-section-graph">
        <header class="page__block-head">
          <span class="page__code">A14</span>
          <h2 id="a14g" class="page__block-title">局部子图高亮</h2>
          <span class="page__block-desc">一跳邻居 + 关系类型</span>
          <SectionBadge
            dataset="synthetic_graph"
            source="synthetic_graph"
            endpoint="GET /api/explain/local/{userid} · neighbours"
          />
        </header>

        <LocalGraphPanel
          v-if="entry"
          :entry="entry"
          :enterprise-name="enterprise.display_name"
          dataset="synthetic_graph"
          :n-samples="detailEnv.meta.n_samples"
        />
        <StatePanel
          v-else
          variant="pending"
          compact
          title="局部子图随局部归因一同产出"
          reason="neighbours 字段由 /api/explain/local/{userid} 返回，上一条阻塞未解除。"
          next-step="先解决 A20 的阻塞（P0-3 或契约缺 entry），本区块自动出现。"
        />
      </section>

      <!-- ============================ 区块六：时序热力 ============================ -->
      <section class="page__block" aria-labelledby="a14h" data-testid="p7-section-heat">
        <header class="page__block-head">
          <span class="page__code">A14</span>
          <h2 id="a14h" class="page__block-title">时序贡献热力图</h2>
          <span class="page__block-desc">Top 4 特征 × 观测月</span>
          <SectionBadge
            :dataset="localDataset"
            :source="src(localEnv.meta)"
            endpoint="GET /api/explain/local/{userid} · series"
          />
        </header>

        <TemporalHeat
          v-if="entry"
          :entry="entry"
          :dataset="localDataset"
          :n-samples="localEnv.meta.n_samples"
          :median-months="medianMonths"
        />
        <StatePanel
          v-else
          variant="pending"
          compact
          title="时序贡献随局部归因一同产出"
          reason="series 字段由 /api/explain/local/{userid} 返回，上一条阻塞未解除。"
          next-step="先解决 A20 的阻塞，本区块自动出现。"
        />
      </section>

    </template>

    <!-- 读图问答 -->
    <section class="page__block" aria-labelledby="qa">
      <header class="page__block-head">
        <span class="page__code">Q</span>
        <h2 id="qa" class="page__block-title">这一页能回答什么、不能回答什么</h2>
      </header>
      <div class="qa">
        <div class="qa__item">
          <p class="qa__q">为什么企业是「匿名企业 u0001」？</p>
          <p class="qa__a">
            信贷面板里只有 userid，没有到统一社会信用代码 / 企业名的映射键（P0-1）。没有映射键，
            任何一个企业名都是编的。映射键到位后 `display_name` 由后端填值，页面结构一个字不改。
          </p>
        </div>
        <div class="qa__item">
          <p class="qa__q">瀑布图上的数字是分数吗？</p>
          <p class="qa__a">
            不是。横轴是 log-odds（违约概率的 logit），从队列平均企业 E[f(x)] 出发逐段累加。
            只有这样才能「加得回去」；用分数直接做瀑布，各段加起来很少正好等于总分。
          </p>
        </div>
        <div class="qa__item">
          <p class="qa__q">为什么雷达图有一半是缺口？</p>
          <p class="qa__a">
            技术要素与成长轨迹两维在面板里没有可归属字段（P0-2 字段字典未补齐）。缺口 = 没有数据；
            画成 0 就是断言「这家企业这两维一无所有」，那是结论，不是事实。
          </p>
        </div>
        <div class="qa__item">
          <p class="qa__q">关键邻居是真实关系吗？</p>
          <p class="qa__a">
            不是。邻居来自六类实体<b>合成图</b>（synthetic_graph），用于验证局部子图交互；
            真实共现关系需要 P0-4（专利）与 P0-5（投资 / 工商变更）。
          </p>
        </div>
        <div class="qa__item">
          <p class="qa__q">这个分数能拿去放贷吗？</p>
          <p class="qa__a">
            不能。行级实测 AUC 约 0.59，置换对照噪声地板 0.477 —— 比地板高 0.1，
            只够做排序参考，不足以支撑单笔信贷决策。这也是本页把「PD 是相对口径」写在仪表盘正下方的原因。
          </p>
        </div>
      </div>
    </section>

    <footer class="page__foot cluster">
      <span>下一步：</span>
      <RouterLink :to="{ name: 'explain' }" class="page__link">P5 可解释性（全局视角）</RouterLink>
      <span class="page__foot-sep">·</span>
      <RouterLink :to="{ name: 'graph' }" class="page__link">P3 时空图谱（全图视角）</RouterLink>
      <span class="page__foot-sep">·</span>
      <RouterLink
        :to="{ name: 'batch', query: { from: enterprise?.userid ?? '' } }"
        class="page__link"
        data-testid="p7-to-batch"
      >
        把这家企业加入批量任务（P8）→
      </RouterLink>
      <span class="page__foot-sep">·</span>
      <span>批量评估按 userid 直取本页同一分数，两页不存在两套口径。</span>
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

.page__verdict {
  padding: var(--space-3) var(--space-4);
  border-left: 3px solid var(--risk-watch);
  border-radius: var(--radius-sm);
  background: var(--risk-watch-bg);
}

.page__verdict-title {
  display: block;
  margin-bottom: 2px;
  font-size: 12px;
  font-weight: 700;
  color: var(--risk-watch);
}

.page__verdict-body {
  font-size: 12.5px;
  line-height: 1.9;
  color: var(--color-text);
}

.is-up {
  color: #b3312c;
}

.is-down {
  color: #2f5d8a;
}

.page__note {
  font-size: 12px;
  line-height: 1.8;
  color: var(--color-text-muted);
}

.page__note-dim {
  color: var(--color-text-subtle);
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
