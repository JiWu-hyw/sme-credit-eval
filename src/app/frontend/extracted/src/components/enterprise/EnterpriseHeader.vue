<script setup lang="ts">
/**
 * P7 区块一 · 企业档案头部（FE-16）
 *
 * 显示七件事：企业标识 / 行业 / 成立年份 / 观测月数 / 生命周期阶段 / 同行口径 / 数据完整性。
 *
 * 这一块最容易犯的错，不是少显示一个字段，而是**把「没有」显示成「不知道」**：
 * 行业为空时写「未披露」，读者会以为这家企业不肯说；实际是映射键（P0-1）没到，
 * 系统里根本没有这个字段。所以空值统一走 `MISSING_TEXT`，并且同屏挂一条
 * 「为什么是空的」说明 —— 这比补一个看似合理的行业名诚实得多。
 *
 * 匿名态（`enterprise.anonymous === true`）时额外挂一颗「匿名」角标，说明本页
 * 不做企业名展示，切换实名态由后端填 `display_name` 即可，页面结构不变（验收项）。
 */
import { computed } from 'vue'

import type { EnterpriseRow } from '@/types/contracts'
import type { DatasetKind } from '@/types'

import SectionBadge from '@/components/model/SectionBadge.vue'

const props = defineProps<{
  enterprise: EnterpriseRow
  /** 全部匿名队列（用于切换器） */
  peers: EnterpriseRow[]
  dataset: DatasetKind
  source: string
  /** 生命周期阶段名（来自 A15 的 stage 口径；缺省用 stage_id） */
  stageLabel?: string
}>()

const emit = defineEmits<{ (e: 'select', userid: string): void }>()

/** 空值的唯一文案：说清「是缺数据」而不是「是零 / 是否」 */
const MISSING_TEXT = '未接入'

const observed = computed(() => {
  const m = props.enterprise.observed_months ?? 0
  return m > 0 ? `${m} 个月` : MISSING_TEXT
})

const depthTone = computed(() => {
  const m = props.enterprise.observed_months ?? 0
  if (m >= 6) return 'ok'
  if (m >= 3) return 'watch'
  return 'thin'
})

const stageText = computed(() => props.stageLabel ?? props.enterprise.stage_id)

const fields = computed(() => [
  {
    key: 'industry',
    label: '行业',
    value: props.enterprise.industry ?? MISSING_TEXT,
    missing: props.enterprise.industry === null,
  },
  {
    key: 'founded',
    label: '成立年份',
    value: props.enterprise.founded_year ? `${props.enterprise.founded_year} 年` : MISSING_TEXT,
    missing: props.enterprise.founded_year === null,
  },
  {
    key: 'stage',
    label: '生命周期阶段',
    value: stageText.value,
    missing: false,
  },
  {
    key: 'months',
    label: '观测月数',
    value: observed.value,
    missing: (props.enterprise.observed_months ?? 0) === 0,
  },
])

function onSelect(e: Event) {
  const v = (e.target as HTMLSelectElement).value
  if (v && v !== props.enterprise.userid) emit('select', v)
}
</script>

<template>
  <section class="ent-head" data-testid="p7-enterprise-head" aria-labelledby="ent-name">
    <div class="ent-head__main">
      <div class="ent-head__title-row">
        <h2 id="ent-name" class="ent-head__name" data-testid="p7-enterprise-name">
          {{ enterprise.display_name }}
        </h2>
        <span v-if="enterprise.anonymous" class="ent-head__anon" data-testid="p7-anon-chip" title="映射键（P0-1）未到位">
          匿名态
        </span>
        <code class="ent-head__id" data-testid="p7-userid">{{ enterprise.userid }}</code>
        <SectionBadge :dataset="dataset" :source="source" endpoint="GET /api/enterprise/{userid}" />
      </div>

      <dl class="ent-head__facts" data-testid="p7-head-facts">
        <div v-for="f in fields" :key="f.key" class="ent-head__fact" :data-missing="f.missing">
          <dt>{{ f.label }}</dt>
          <dd>{{ f.value }}</dd>
        </div>
        <div class="ent-head__fact ent-head__fact--wide">
          <dt>同行口径</dt>
          <dd>
            {{ enterprise.peer_scope.label }} · n={{ enterprise.peer_scope.n }}
            <span class="ent-head__dim">（{{ enterprise.peer_scope.source === 'contract_spec' ? '匿名队列自身分布' : '实测' }}）</span>
          </dd>
        </div>
      </dl>

      <p v-if="enterprise.anonymous" class="ent-head__note" data-testid="p7-anon-note">
        {{ enterprise.name_note }}
      </p>
    </div>

    <div class="ent-head__side">
      <label class="ent-head__picker">
        <span>切换企业（匿名队列）</span>
        <select :value="enterprise.userid" data-testid="p7-enterprise-picker" @change="onSelect">
          <option v-for="p in peers" :key="p.userid" :value="p.userid">
            {{ p.display_name }}（{{ p.credit_score.toFixed(1) }} 分）
          </option>
        </select>
      </label>
      <p class="ent-head__depth" :data-tone="depthTone" data-testid="p7-depth-note">
        <b>观测纵深：{{ observed }}</b>
        <span v-if="depthTone === 'thin'">
          面板纵深不足（企业级中位约 2 个月），时序区块只能给到「月」这一档，不足以支撑季 / 年多尺度建模。
        </span>
        <span v-else>可用于跨月切片观察，但月数仍低于 12，季度粒度只能近似。</span>
      </p>
    </div>
  </section>
</template>

<style scoped>
.ent-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: var(--space-5);
  padding: var(--space-5);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

@media (max-width: 1023px) {
  .ent-head {
    grid-template-columns: minmax(0, 1fr);
  }
}

.ent-head__title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.ent-head__name {
  font-size: 20px;
  font-weight: 700;
}

.ent-head__anon {
  padding: 1px 7px;
  border: 1px dashed var(--risk-watch);
  border-radius: 10px;
  color: var(--risk-watch);
  background: var(--risk-watch-bg);
  font-size: 11px;
}

.ent-head__id {
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  background: var(--color-surface-muted);
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--color-text-muted);
}

.ent-head__facts {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-5);
  margin: var(--space-4) 0 0;
}

.ent-head__fact dt {
  font-size: 11px;
  color: var(--color-text-subtle);
}

.ent-head__fact dd {
  margin: 2px 0 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text);
}

/* 缺数据显示为虚线底 + 弱化色：一眼能分出「有值」与「没接入」 */
.ent-head__fact[data-missing='true'] dd {
  font-weight: 500;
  color: var(--color-text-subtle);
  border-bottom: 1px dashed var(--color-border-strong);
}

.ent-head__dim {
  font-size: 11px;
  color: var(--color-text-subtle);
}

.ent-head__note {
  margin-top: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-left: 2px solid var(--color-border-strong);
  background: var(--color-surface-muted);
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--color-text-muted);
}

.ent-head__side {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-left: var(--space-4);
  border-left: 1px solid var(--color-border);
}

@media (max-width: 1023px) {
  .ent-head__side {
    padding-left: 0;
    border-left: none;
    border-top: 1px solid var(--color-border);
    padding-top: var(--space-4);
  }
}

.ent-head__picker {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  font-size: 11.5px;
  color: var(--color-text-muted);
}

.ent-head__picker select {
  min-height: var(--hit-target-min);
  padding: 0 var(--space-2);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  font-family: inherit;
  font-size: 12.5px;
  color: var(--color-text);
}

.ent-head__depth {
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  background: var(--color-surface-muted);
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--color-text-muted);
}

.ent-head__depth b {
  display: block;
  color: var(--color-text);
}

.ent-head__depth[data-tone='thin'] {
  background: var(--risk-watch-bg);
  color: var(--risk-watch);
}

.ent-head__depth[data-tone='thin'] b {
  color: var(--risk-watch);
}
</style>
