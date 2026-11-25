// ECharts 按需引入（ADR-0013）：只注册用到的图表和组件。只有带图表的页面（详情、分析、大盘）会
// import 这个文件，路由懒加载把它拆进单独的包，首页不加载（tests/test_frontend_build.py 守着）。
// 用 canvas 渲染：不需要放开 CSP 的 unsafe-eval。
import { BarChart, CandlestickChart, LineChart, ScatterChart } from 'echarts/charts'
import {
  AxisPointerComponent,
  DataZoomComponent,
  GridComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
} from 'echarts/components'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'

use([
  CanvasRenderer,
  BarChart,
  CandlestickChart,
  LineChart,
  ScatterChart,
  AxisPointerComponent,
  DataZoomComponent,
  GridComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
])
