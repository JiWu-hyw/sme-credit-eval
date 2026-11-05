<script setup lang="ts">
/**
 * 语义未知字段标签（红线二，FE-03）
 *
 * 契约 §6.5：`semantic_known: false` 的字段（当前 x21–x200 全体）**只显示原始代号**，
 * 不做任何业务化包装。本组件把这条约束做成类型约束：
 *   - 只接收 field 代号，不接收业务名；
 *   - 形如 xNNN 的代号即使被误标 known，也强制按代号渲染。
 */
const props = withDefaults(
  defineProps<{
    field: string
    semanticKnown?: boolean
  }>(),
  { semanticKnown: false },
)

/** 形如 x037 / X_052 的代号：本身就不可能是业务名 */
const looksLikeCode = () => /^x[_-]?\d+$/i.test(props.field.trim())
const shown = () => props.field.trim()
const isUnknown = () => !props.semanticKnown || looksLikeCode()
</script>

<template>
  <code
    v-if="isUnknown()"
    class="unknown-field"
    title="字段语义未确认（字段字典未补齐），仅显示原始代号"
    data-testid="unknown-field"
    >{{ shown() }}</code
  >
  <span v-else class="known-field">{{ shown() }}</span>
</template>

<style scoped>
.unknown-field {
  display: inline-block;
  padding: 0 5px;
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius-sm);
  background: var(--color-surface-muted);
  color: var(--color-text-muted);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 18px;
}

.known-field {
  font-weight: 500;
}
</style>
