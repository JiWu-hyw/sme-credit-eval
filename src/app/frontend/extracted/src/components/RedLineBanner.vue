<script setup lang="ts">
/**
 * 语义未知统一提示条（红线二，FE-03）
 *
 * 「为什么页面上全是 x037 / x121」这个问题只在这里解释一次，
 * 各图表不再自拟文案，避免七页出现七种说法。
 *
 * FE-14 追加 `affected`：同一个页面里「哪些区块被字段字典卡住」不一样
 * （P5 是 A13 卡业务解读、A15 卡因子命名且契约本身还是 mock），
 * 用一枚小标签列出来，读者不必自己推断影响面。
 */
const props = withDefaults(
  defineProps<{
    /** 受影响字段数量（可选，把话说具体） */
    count?: number
    /** 页面/区块名，写进提示里便于定位 */
    scope?: string
    /** 受影响区块编号（可选，如 ['A13', 'A15']） */
    affected?: string[]
  }>(),
  { count: undefined, scope: '', affected: () => [] },
)
</script>

<template>
  <aside class="redline-banner" role="note" data-testid="redline-banner">
    <span class="redline-banner__icon" aria-hidden="true">!</span>
    <div class="redline-banner__body">
      <p class="redline-banner__title">字段语义未确认<span v-if="props.scope">（{{ props.scope }}）</span></p>
      <p class="redline-banner__text">
        信贷面板 x21–x200<template v-if="props.count"> 共 {{ props.count }} 个字段</template
        >的字段字典尚未补齐，本页一律只显示原始代号（如 <code>x037</code>），不做业务化包装；
        业务名将通过 <code>label_ref</code> 间接引用。
      </p>
      <p v-if="props.affected.length" class="redline-banner__affected">
        受影响区块：
        <span v-for="a in props.affected" :key="a" class="redline-banner__chip">{{ a }}</span>
      </p>
    </div>
  </aside>
</template>

<style scoped>
.redline-banner {
  display: flex;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-border);
  border-left: 3px solid var(--color-accent);
  border-radius: var(--radius-sm);
  background: var(--color-accent-soft);
}

.redline-banner__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: 18px;
  height: 18px;
  margin-top: 2px;
  border-radius: 50%;
  background: var(--color-accent);
  color: var(--color-surface);
  font-size: 12px;
  font-weight: 700;
}

.redline-banner__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text);
}

.redline-banner__text {
  font-size: 12px;
  line-height: 1.7;
  color: var(--color-text-muted);
}

.redline-banner__affected {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 5px;
  font-size: 11.5px;
  color: var(--color-text-subtle);
}

.redline-banner__chip {
  padding: 0 6px;
  border: 1px solid var(--color-border-strong);
  border-radius: 8px;
  background: var(--color-surface);
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--color-text-muted);
}

.redline-banner__text code {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 0 3px;
  border-radius: 2px;
  background: var(--color-surface);
}
</style>
