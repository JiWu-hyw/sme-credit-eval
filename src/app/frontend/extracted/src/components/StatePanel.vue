<script setup lang="ts">
/**
 * 统一空态 / 错误态 / 阻塞态（FE-04）
 *
 * 契约把「空态」和「错误态」都拆成两个必填信息：
 *   ① 说明原因（为什么这里是空的）  ② 下一步（用户能做什么）
 * 所以 `reason` 与 `nextStep` 都是 **必填 prop**——
 * 写不出原因，就说明这个区块其实还没想清楚要展示什么，
 * 让 TypeScript 拦住比让评审在页面上看一句「暂无数据」更好。
 *
 * 三种语气（variant）：
 *   - empty：真的没数据（如未上传面板），中性偏灰
 *   - error：请求/渲染失败，带重试按钮，允许展开技术摘要
 *   - pending：契约未就绪（CONTRACT_PENDING / LABEL_PENDING），说明依赖项而不要求重试
 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    variant?: 'empty' | 'error' | 'pending'
    /** 标题，一句话说清这块是什么 */
    title: string
    /** 必填：为什么空 / 为什么错 */
    reason: string
    /** 必填：下一步做什么 */
    nextStep: string
    /** 技术摘要（code / status），仅 error 时展示 */
    detail?: string
    /** 重试回调；不给则不渲染按钮（pending 态通常不给） */
    retry?: () => void
    /** 重试按钮文案 */
    retryText?: string
    /** 紧凑模式：用于区块内嵌，不占整页高度 */
    compact?: boolean
  }>(),
  { variant: 'empty', detail: undefined, retry: undefined, retryText: '重试', compact: false },
)

const tone = computed(
  () =>
    ({
      empty: 'neutral',
      error: 'danger',
      pending: 'warn',
    })[props.variant],
)
</script>

<template>
  <div
    class="state-panel"
    :class="[`state-panel--${variant}`, { 'state-panel--compact': compact }]"
    :data-tone="tone"
    :role="variant === 'error' ? 'alert' : 'status'"
    data-testid="state-panel"
  >
    <p class="state-panel__title">{{ title }}</p>

    <dl class="state-panel__facts">
      <div class="state-panel__fact">
        <dt>原因</dt>
        <dd>{{ reason }}</dd>
      </div>
      <div class="state-panel__fact">
        <dt>下一步</dt>
        <dd>{{ nextStep }}</dd>
      </div>
    </dl>

    <details v-if="detail" class="state-panel__detail">
      <summary>技术摘要</summary>
      <code>{{ detail }}</code>
    </details>

    <button v-if="retry" type="button" class="state-panel__retry" @click="retry">
      {{ retryText }}
    </button>
  </div>
</template>

<style scoped>
.state-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-6);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text-muted);
}

.state-panel--compact {
  padding: var(--space-4);
  gap: var(--space-2);
}

/* 语气只影响左边框与标题色，不引入新色值 */
.state-panel--error {
  border-style: solid;
  border-left: 3px solid var(--risk-high-risk);
}

.state-panel--pending {
  border-style: solid;
  border-left: 3px solid var(--color-accent);
}

.state-panel__title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text);
}

.state-panel--compact .state-panel__title {
  font-size: 13px;
}

.state-panel__facts {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  font-size: 13px;
  line-height: 1.65;
}

.state-panel__fact {
  display: grid;
  grid-template-columns: 4em 1fr;
  gap: var(--space-3);
}

.state-panel__fact dt {
  color: var(--color-text-subtle);
}

.state-panel__fact dd {
  margin: 0;
  color: var(--color-text-muted);
}

.state-panel__detail {
  font-size: 12px;
  color: var(--color-text-subtle);
}

.state-panel__detail summary {
  cursor: pointer;
  min-height: var(--hit-target-min);
  display: flex;
  align-items: center;
}

.state-panel__detail code {
  display: block;
  margin-top: var(--space-1);
  padding: var(--space-2);
  border-radius: var(--radius-sm);
  background: var(--color-surface-muted);
  font-family: var(--font-mono);
  font-size: 11px;
  word-break: break-all;
}

.state-panel__retry {
  align-self: flex-start;
  min-height: var(--hit-target-min);
  padding: 0 var(--space-4);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-sm);
  background: var(--color-accent-soft);
  color: var(--color-text);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}

.state-panel__retry:hover {
  background: var(--color-accent);
  border-color: var(--color-accent);
}

.state-panel__retry:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
}
</style>
