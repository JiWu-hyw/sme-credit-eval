<script setup lang="ts">
/**
 * ECharts 基座（FE-03）
 *
 * ⚠️ 业务页面勿直接使用，一律走 `ChartPanel`（它强制带数据来源角标，红线一）。
 * 本组件只负责：初始化实例、响应容器尺寸、加载态遮罩、卸载销毁。
 */
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'

import { echarts, type EChartsOption } from './echarts'

const props = withDefaults(
  defineProps<{
    option: EChartsOption
    height?: number | string
    loading?: boolean
    /** 需要一并 replaceMerge 的非 series 组件（radar / polar / geo），见 ChartPanel 注释 */
    mergedComponents?: string[]
  }>(),
  { height: 320, loading: false, mergedComponents: () => [] },
)

const emit = defineEmits<{
  (e: 'chart-click', payload: { dataType?: string; data?: Record<string, unknown> }): void
}>()

const el = ref<HTMLDivElement | null>(null)
// echarts 实例类型取自 core 命名空间，避免深层 import 路径漂移
const chart = shallowRef<ReturnType<typeof echarts.init> | null>(null)
let observer: ResizeObserver | null = null

function render() {
  if (!chart.value) return
  /**
   * 合并模式（replaceMerge: ['series']）而不是 notMerge。
   *
   * 踩坑（FE-12 / P3 力导向图）：notMerge 每次重建整张图，力导向布局会从随机初始位置
   * 重新迭代——用户在图上拖好节点、点一下选中，整张图瞬间重排，画面像"抽风"。
   * 改用 replaceMerge 系列（保留各 series 的内部状态）+ 不重置布局，
   * 其余组件（grid / xAxis…）仍按新 option 覆盖，语义与 notMerge 一致但不会抖动。
   */
  chart.value.setOption(props.option, {
    replaceMerge: ['series', ...(props.mergedComponents ?? [])],
    lazyUpdate: false,
  })
}

onMounted(() => {
  if (!el.value) return
  chart.value = echarts.init(el.value, undefined, { renderer: 'canvas' })
  // 点击透传给上层（P3 力导向图选节点用）。只转发原始参数，不在基座里做业务判断。
  chart.value.on('click', (params) => {
    const p = params as unknown as { dataType?: string; data?: Record<string, unknown> }
    emit('chart-click', { dataType: p.dataType, data: p.data })
  })
  render()
  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(() => chart.value?.resize())
    observer.observe(el.value)
  }
})

watch(() => props.option, render, { deep: true })
watch(
  () => props.loading,
  (v) => (v ? chart.value?.showLoading('default', { text: '加载中' }) : chart.value?.hideLoading()),
)

onBeforeUnmount(() => {
  observer?.disconnect()
  chart.value?.dispose()
  chart.value = null
})
</script>

<template>
  <div
    ref="el"
    class="echart-base"
    :style="{ height: typeof height === 'number' ? `${height}px` : height }"
    role="img"
  />
</template>

<style scoped>
.echart-base {
  width: 100%;
}
</style>
