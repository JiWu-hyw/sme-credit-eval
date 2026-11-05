<script setup lang="ts">
/**
 * A04 特征体系三级树 + 字段详情浮层（FE-11 区块四）
 *
 * 结构：一级维度（技术要素 / 成长轨迹 / 融合特征）→ 二级子类 → 字段叶子。
 * 交互要求（验收项）：
 *   - **可折叠**：每个分支可展开/收起，任意一级都支持；有「全部展开 / 全部收起」；
 *   - **可点击展开字段清单**：点叶子打开右侧详情浮层（dialog），展示类型、缺失率、
 *     是否缩尾、是否入图、是否进模型、SHAP 排名占位、语义状态。
 *
 * 无障碍：浮层用 role="dialog" + aria-modal，Esc 关闭、遮罩可点、焦点移入关闭按钮、
 * 关闭后焦点还原到触发的叶子——键盘用户不会掉回文档开头。
 *
 * 红线：叶子只渲染代号（UnknownFieldTag 强制），即使 label_ref 指向未来字段字典，
 * 当前也不展示业务名。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import UnknownFieldTag from '@/components/UnknownFieldTag.vue'
import type { FeatureBranch, FeatureLeaf, FeatureNode, FeatureTree } from '@/types/contracts'
import { pct } from '@/utils/format'

const props = defineProps<{ tree: FeatureTree }>()

/* ------------------------------ 折叠状态 ------------------------------ */

/**
 * 默认展开：一级全开（三个维度先让读者看清体系），
 * 二级只展开「含进模型字段」的分支，其余收起 —— 11 个叶子全铺开会把面板撑爆。
 */
function defaultExpanded(nodes: FeatureNode[], level = 1): Set<string> {
  const keys = new Set<string>()
  for (const n of nodes) {
    if (n.kind !== 'branch') continue
    const key = pathKey(n, level)
    if (level === 1 || (n.children ?? []).some((c) => c.kind === 'leaf' && c.used_in_model)) {
      keys.add(key)
    }
  }
  return keys
}

function pathKey(node: FeatureBranch, level: number): string {
  return `${level}:${node.code ?? node.name}`
}

const expanded = ref<Set<string>>(defaultExpanded(props.tree.roots))

function toggle(key: string) {
  const next = new Set(expanded.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  expanded.value = next
}

function expandAll() {
  const all = new Set<string>()
  const walk = (nodes: FeatureNode[], level: number) => {
    for (const n of nodes) {
      if (n.kind !== 'branch') continue
      all.add(pathKey(n, level))
      walk(n.children ?? [], level + 1)
    }
  }
  walk(props.tree.roots, 1)
  expanded.value = all
}

function collapseAll() {
  expanded.value = new Set()
}

/* ------------------------------ 叶子统计 ------------------------------ */

function leafCount(node: FeatureBranch): number {
  return node.children.reduce((s, c) => (c.kind === 'leaf' ? s + 1 : s + leafCount(c)), 0)
}

function usedCount(node: FeatureBranch): number {
  return node.children.reduce(
    (s, c) => (c.kind === 'leaf' ? s + (c.used_in_model ? 1 : 0) : s + usedCount(c)),
    0,
  )
}

const summary = computed(() => props.tree.summary)

/* ------------------------------ 字段详情浮层 ------------------------------ */

const activeLeaf = ref<FeatureLeaf | null>(null)
const activePath = ref('')
const closeBtn = ref<HTMLButtonElement | null>(null)
let restoreFocus: HTMLElement | null = null

function openLeaf(leaf: FeatureLeaf, path: string, evt: MouseEvent) {
  restoreFocus = evt.currentTarget as HTMLElement
  activeLeaf.value = leaf
  activePath.value = path
}

function closeLeaf() {
  activeLeaf.value = null
  restoreFocus?.focus()
  restoreFocus = null
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && activeLeaf.value) closeLeaf()
}

watch(activeLeaf, async (v) => {
  if (typeof window === 'undefined') return
  if (v) {
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', onKeydown)
    await nextTick()
    closeBtn.value?.focus()
  } else {
    document.body.style.overflow = ''
    window.removeEventListener('keydown', onKeydown)
  }
})

onBeforeUnmount(() => {
  if (typeof window !== 'undefined') {
    document.body.style.overflow = ''
    window.removeEventListener('keydown', onKeydown)
  }
})
</script>

<template>
  <div class="ftree">
    <header class="ftree__head cluster">
      <p class="ftree__summary">
        三级体系：<strong>{{ summary?.level1 ?? 3 }}</strong> 个一级维度 ·
        <strong>{{ summary?.level2 ?? '—' }}</strong> 个二级子类 ·
        <strong>{{ summary?.leaves ?? '—' }}</strong> 个字段叶子（进模型
        {{ summary?.used_in_model ?? '—' }} 个）
      </p>
      <div class="ftree__actions">
        <button type="button" class="ftree__btn" @click="expandAll">全部展开</button>
        <button type="button" class="ftree__btn" @click="collapseAll">全部收起</button>
      </div>
    </header>

    <p v-if="summary?.blocked_by?.length" class="ftree__blocked">
      阻塞项：{{ summary.blocked_by.join('；') }}
    </p>

    <ul class="ftree__roots">
      <li v-for="(l1, i1) in tree.roots" :key="i1" class="ftree__node">
        <template v-if="l1.kind === 'branch'">
          <button
            type="button"
            class="ftree__toggle ftree__toggle--l1"
            :aria-expanded="expanded.has(pathKey(l1, 1))"
            @click="toggle(pathKey(l1, 1))"
          >
            <span class="ftree__caret" aria-hidden="true">{{ expanded.has(pathKey(l1, 1)) ? '▾' : '▸' }}</span>
            <span class="ftree__name">{{ l1.name }}</span>
            <span class="ftree__meta">{{ leafCount(l1) }} 字段 · 进模型 {{ usedCount(l1) }}</span>
            <span class="ftree__ready" :data-ready="String(l1.data_ready)">{{ l1.data_ready === true ? '数据就绪' : '数据未就绪' }}</span>
          </button>
          <p v-if="l1.hint" class="ftree__hint">{{ l1.hint }}</p>

          <ul v-show="expanded.has(pathKey(l1, 1))" class="ftree__l2">
            <li v-for="(l2, i2) in l1.children" :key="i2" class="ftree__node">
              <template v-if="l2.kind === 'branch'">
                <button
                  type="button"
                  class="ftree__toggle ftree__toggle--l2"
                  :aria-expanded="expanded.has(pathKey(l2, 2))"
                  @click="toggle(pathKey(l2, 2))"
                >
                  <span class="ftree__caret" aria-hidden="true">{{ expanded.has(pathKey(l2, 2)) ? '▾' : '▸' }}</span>
                  <span class="ftree__name">{{ l2.name }}</span>
                  <span class="ftree__meta">{{ leafCount(l2) }} 字段</span>
                </button>

                <ul v-show="expanded.has(pathKey(l2, 2))" class="ftree__leaves">
                  <li v-if="!l2.children.length" class="ftree__empty">
                    暂无可展示字段（等双维数据齐备后计算，当前为占位节点）
                  </li>
                  <li v-for="(leaf, i3) in l2.children" :key="i3">
                    <button
                      v-if="leaf.kind === 'leaf'"
                      type="button"
                      class="ftree__leaf"
                      :aria-label="`查看字段 ${leaf.field} 详情`"
                      @click="openLeaf(leaf, `${l1.name} / ${l2.name}`, $event)"
                    >
                      <UnknownFieldTag :field="leaf.field" :semantic-known="leaf.semantic_known" />
                      <span class="ftree__leaf-type">{{ leaf.type }}</span>
                      <span class="ftree__leaf-miss" :data-high="leaf.missing_rate >= 0.3">
                        缺失 {{ pct(leaf.missing_rate) }}
                      </span>
                      <span class="ftree__leaf-used" :data-used="leaf.used_in_model">
                        {{ leaf.used_in_model ? '进模型' : '未进模型' }}
                      </span>
                      <span class="ftree__leaf-more" aria-hidden="true">详情 ›</span>
                    </button>
                  </li>
                </ul>
              </template>
            </li>
          </ul>
        </template>
      </li>
    </ul>

    <!-- 字段详情浮层 -->
    <div v-if="activeLeaf" class="leaf-modal" data-testid="leaf-modal" @click.self="closeLeaf">
      <div class="leaf-modal__panel" role="dialog" aria-modal="true" aria-labelledby="leaf-title">
        <header class="leaf-modal__head">
          <div>
            <p class="leaf-modal__crumb">{{ activePath }}</p>
            <h3 id="leaf-title" class="leaf-modal__title">
              <UnknownFieldTag :field="activeLeaf.field" :semantic-known="activeLeaf.semantic_known" />
            </h3>
          </div>
          <button ref="closeBtn" type="button" class="leaf-modal__close" aria-label="关闭字段详情" @click="closeLeaf">
            ×
          </button>
        </header>

        <dl class="leaf-modal__facts">
          <div><dt>数据类型</dt><dd>{{ activeLeaf.detail?.dtype ?? activeLeaf.type }}</dd></div>
          <div><dt>缺失率</dt><dd>{{ pct(activeLeaf.missing_rate) }}</dd></div>
          <div><dt>缺失处理</dt><dd>训练集中位数填充</dd></div>
          <div><dt>缩尾</dt><dd>{{ activeLeaf.detail?.winsorized ? '1% / 99% 缩尾' : '未缩尾（缺失过高或非连续）' }}</dd></div>
          <div><dt>标准化</dt><dd>{{ activeLeaf.detail?.standardized ? 'Z-score（训练集拟合）' : '否' }}</dd></div>
          <div><dt>是否入图</dt><dd>{{ activeLeaf.detail?.in_graph ? '是（参与 GAT 聚合）' : '否（仅作节点特征）' }}</dd></div>
          <div><dt>是否进模型</dt><dd>{{ activeLeaf.used_in_model ? '是' : '否' }}</dd></div>
          <div><dt>SHAP 排名</dt><dd>{{ activeLeaf.detail?.shap_rank ?? '待全局 SHAP 产出（P5）' }}</dd></div>
          <div class="leaf-modal__wide">
            <dt>语义状态</dt>
            <dd>
              <span class="leaf-modal__tag">
                {{ activeLeaf.semantic_known ? '已确认' : '未确认（字段字典未补齐）' }}
              </span>
              {{ activeLeaf.detail?.semantic_note ?? '只显示原始代号，业务名经 label_ref 间接引用' }}
            </dd>
          </div>
          <div v-if="activeLeaf.note" class="leaf-modal__wide"><dt>备注</dt><dd>{{ activeLeaf.note }}</dd></div>
        </dl>

        <footer class="leaf-modal__foot">
          <code>label_ref: {{ activeLeaf.label_ref }}</code>
          <button type="button" class="leaf-modal__btn" @click="closeLeaf">关闭</button>
        </footer>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ftree {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.ftree__summary {
  font-size: 13px;
  color: var(--color-text-muted);
}

.ftree__summary strong {
  font-family: var(--font-mono);
  color: var(--color-text);
}

.ftree__actions {
  display: flex;
  gap: var(--space-2);
  margin-left: auto;
}

.ftree__btn {
  min-height: 30px;
  padding: 0 var(--space-3);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text-muted);
  font-size: 12px;
  cursor: pointer;
}

.ftree__btn:hover {
  border-color: var(--color-accent);
  color: var(--color-accent);
}

.ftree__blocked {
  padding: var(--space-2) var(--space-3);
  border-left: 3px solid var(--risk-high-risk);
  background: var(--risk-high-risk-bg);
  font-size: 12px;
  color: var(--risk-high-risk);
}

.ftree__roots,
.ftree__l2,
.ftree__leaves {
  margin: 0;
  padding: 0;
  list-style: none;
}

.ftree__l2,
.ftree__leaves {
  margin-left: var(--space-5);
  border-left: 1px dashed var(--color-border);
  padding-left: var(--space-3);
}

.ftree__node + .ftree__node {
  margin-top: var(--space-1);
}

.ftree__toggle {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  min-height: 40px;
  padding: var(--space-1) var(--space-3);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text);
  text-align: left;
  cursor: pointer;
  font-size: 13px;
}

.ftree__toggle:hover {
  background: var(--color-surface-muted);
}

.ftree__toggle--l1 {
  font-weight: 600;
  font-size: 14px;
  background: var(--color-surface-muted);
}

.ftree__caret {
  width: 12px;
  color: var(--color-text-subtle);
  font-size: 10px;
}

.ftree__meta {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-subtle);
}

.ftree__ready {
  margin-left: auto;
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text-muted);
  font-size: 11px;
}

.ftree__ready[data-ready='false'] {
  background: var(--risk-high-risk-bg);
  color: var(--risk-high-risk);
}

.ftree__ready[data-ready='partial'] {
  background: var(--risk-watch-bg);
  color: var(--risk-watch);
}

.ftree__hint {
  margin: 0 0 var(--space-1) calc(var(--space-5) + var(--space-2));
  font-size: 12px;
  color: var(--color-text-subtle);
}

.ftree__leaf {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  min-height: 36px;
  padding: 2px var(--space-2);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  text-align: left;
  cursor: pointer;
  font-size: 12px;
  color: var(--color-text-muted);
}

.ftree__leaf:hover,
.ftree__leaf:focus-visible {
  background: var(--color-accent-soft);
}

.ftree__leaf-type {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-subtle);
}

.ftree__leaf-miss[data-high='true'] {
  color: var(--risk-warning);
}

.ftree__leaf-used[data-used='false'] {
  color: var(--color-text-subtle);
}

.ftree__leaf-more {
  margin-left: auto;
  color: var(--color-accent);
  font-size: 11px;
}

.ftree__empty {
  padding: var(--space-2);
  font-size: 12px;
  color: var(--color-text-subtle);
}

/* ---------------- 浮层 ---------------- */
.leaf-modal {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  justify-content: flex-end;
  align-items: flex-start;
  background: color-mix(in srgb, var(--color-text) 32%, transparent);
}

.leaf-modal__panel {
  display: flex;
  flex-direction: column;
  width: min(460px, 100%);
  /* 抽屉必须有高度上限：`height: 100%` 撑满整屏会把 10 行字段信息摊在
     一片空白里（截图为证），底部「label_ref / 关闭」被推到屏幕最下沿。 */
  max-height: 100%;
  height: auto;
  padding: var(--space-5);
  background: var(--color-surface);
  border-left: 1px solid var(--color-border);
  overflow-y: auto;
}

.leaf-modal__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--color-border);
}

.leaf-modal__crumb {
  font-size: 12px;
  color: var(--color-text-subtle);
}

.leaf-modal__title {
  margin-top: var(--space-1);
  font-size: 18px;
}

.leaf-modal__close {
  flex: 0 0 auto;
  width: var(--hit-target-min);
  height: var(--hit-target-min);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  font-size: 20px;
  line-height: 1;
  color: var(--color-text-muted);
  cursor: pointer;
}

.leaf-modal__facts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
  margin-top: var(--space-4);
  font-size: 12px;
}

.leaf-modal__facts dt {
  color: var(--color-text-subtle);
  font-size: 11px;
}

.leaf-modal__facts dd {
  margin: 2px 0 0;
  color: var(--color-text);
  line-height: 1.6;
}

.leaf-modal__wide {
  grid-column: 1 / -1;
}

.leaf-modal__tag {
  display: inline-block;
  margin-right: 6px;
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: var(--risk-watch-bg);
  color: var(--risk-watch);
  font-size: 11px;
}

.leaf-modal__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-top: auto;
  padding-top: var(--space-4);
  border-top: 1px solid var(--color-border);
}

.leaf-modal__foot code {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-subtle);
  word-break: break-all;
}

.leaf-modal__btn {
  min-height: var(--hit-target-min);
  padding: 0 var(--space-4);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-sm);
  background: var(--color-accent-soft);
  color: var(--color-text);
  font-size: 13px;
  cursor: pointer;
}
</style>
