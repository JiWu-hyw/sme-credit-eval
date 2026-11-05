import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

/**
 * 同一个 SFC 被「静态 import」与「路由懒加载」两处引用时，会被编译成**两份不同的
 * setup 闭包**，但两者共享同一个 `__hmrId`/`__scopeId`。dev 下实测会互相顶掉实例
 * 上下文（页面组件状态正常、模板却停在骨架屏上）。所以页面一律只从路由懒加载取，
 * 冒烟脚本也不另 import 页面组件，需要断言的数据直接走契约文件。
 */

/**
 * 路由表（严格对齐《前端页面与交互原型说明》v1.0 §三）
 *
 * 两个容易踩的点：
 *   1. P1 的路径是 `/`，不是 `/overview`；
 *   2. P7 的 userid 声明为**可选**，导航只能 `{ name: 'enterprise' }` 跳转，
 *      无 userid 时落到占位态而不是 404。
 */

/** 路由 meta（导航组件从 item.meta 取编号 / 标题 / 职责） */
export interface RouteMetaInfo {
  code: string
  title: string
  description: string
}

/** 导航项：items 直接使用 route.meta，避免「导航文案」与「路由 meta」两处维护 */
export interface NavItem {
  name: string
  path: string
  meta: RouteMetaInfo
}

const PLACEHOLDER = () => import('../views/PlaceholderView.vue')

export const ROUTES: RouteRecordRaw[] = [
  { path: '/', name: 'overview', component: () => import('../views/OverviewView.vue'),
    meta: { code: 'P1', title: '概览', description: 'KPI、方法链路、数据缺口状态' } },
  { path: '/data', name: 'data', component: () => import('../views/DataFeaturesView.vue'),
    meta: { code: 'P2', title: '数据与特征', description: '数据接入链路、质量诊断、面板纵深、特征体系' } },
  { path: '/graph', name: 'graph', component: () => import('../views/GraphView.vue'),
    meta: { code: 'P3', title: '时空图谱', description: '六类实体异质图与时间切片' } },
  { path: '/model', name: 'model', component: () => import('../views/ModelCompareView.vue'),
    meta: { code: 'P4', title: '模型与对比', description: '训练曲线、基准对比、ROC/PR/KS' } },
  { path: '/explain', name: 'explain', component: () => import('../views/ExplainView.vue'),
    meta: { code: 'P5', title: '可解释性', description: '全局 SHAP、阶段-因子权重、局部归因' } },
  { path: '/forecast', name: 'forecast', component: () => import('../views/ForecastView.vue'),
    meta: { code: 'P6', title: '前瞻预测', description: '生存力与融资力预测' } },
  // P7 已交付（FE-16）：userid 仍声明为可选，无参时落到匿名队列第一家且不改写 URL
  { path: '/enterprise/:userid?', name: 'enterprise', component: () => import('../views/EnterpriseView.vue'),
    meta: { code: 'P7', title: '单企业深度诊断', description: '档案 · 评分 · 雷达 · 局部归因 · 局部图谱 · 时序贡献' } },
  // P8 批量评估（FE-20）：userid 可空时的深链 target —— 从 P7「加入批量任务」跳过来用 query
  { path: '/batch', name: 'batch', component: () => import('../views/BatchView.vue'),
    meta: { code: 'P8', title: '批量评估', description: '名单导入 · 逐行打分 · 结果筛选 · 导出 · 跨批次对比' } },
  { path: '/:pathMatch(.*)*', name: 'not-found', component: PLACEHOLDER,
    meta: { code: '404', title: '页面不存在', description: '请从顶部导航返回' } },
]

/** 顶部导航只取 P1–P8 八项 */
export const NAV_ROUTES: NavItem[] = ROUTES.filter((r) =>
  /^P\d$/.test(String(r.meta?.code ?? '')),
).map((r) => ({
  name: String(r.name),
  path: r.path,
  meta: r.meta as unknown as RouteMetaInfo,
}))

/**
 * 路由实例工厂。
 *
 * 刻意不在模块顶层 `createWebHistory()`：`createWebHistory` 会读 `window`，
 * 而 SSR 冒烟脚本（scripts/*-smoke.mjs）在 Node 里 `ssrLoadModule` 本文件，
 * 顶层调用会直接 `ReferenceError: window is not defined`。
 * 因此这里只导出纯数据（ROUTES / NAV_ROUTES）与工厂，实例化交给 main.ts。
 */
export function createAppRouter() {
  return createRouter({
    history: createWebHistory(),
    routes: ROUTES,
    scrollBehavior: () => ({ top: 0 }),
  })
}
