/**
 * ECharts 按需注册（FE-12 全集）
 *
 * 只注册各页真正用到的图表与组件；按需注册把 echarts 包从 ~1MB 压到几百 KB。
 * FE-11 用到柱 / 自定义 / 热力 / 折线 / 散点；FE-12（P3）补上力导向关系图；
 * FE-16（P7）补上仪表盘（gauge）—— 漏注册的表现是「控制台一条 error、图上什么都没有」，
 * 不会白屏，所以必须由冒烟断言「无 ECharts 告警」来兜；
 * FE-13（P4）补 MarkPoint（训练曲线的最佳轮钉点）与 MarkArea（KS 黄金区带）。
 *
 * 类型说明：Option 用 `ECBasicOption` 宽松承接（各 series 的 option 类型在 echarts/core
 * 命名空间里并不导出），业务侧组装 option 时由编译期检查 series 的 `type` 字面量即可。
 */
import * as echarts from 'echarts/core'
import {
  BarChart,
  CustomChart,
  GaugeChart,
  GraphChart,
  HeatmapChart,
  LineChart,
  RadarChart,
  ScatterChart,
} from 'echarts/charts'
import {
  DatasetComponent,
  GraphicComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  RadarComponent,
  TitleComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([
  BarChart,
  CustomChart,
  GaugeChart,
  GraphChart,
  HeatmapChart,
  LineChart,
  RadarChart,
  ScatterChart,
  DatasetComponent,
  GraphicComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  RadarComponent,
  TitleComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer,
])

/** 图表 option：结构由各 series 自身决定，这里只约束顶层 */
export type EChartsOption = Record<string, unknown>

export { echarts }
