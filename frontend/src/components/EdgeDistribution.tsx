import { useMemo } from 'react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { Signal, WeatherSignal } from '../types'

interface Props {
  btcSignals?: Signal[]
  weatherSignals: WeatherSignal[]
}

const BUCKETS = ['0-25%', '25-50%', '50-100%', '100-200%', '200-500%', '500%+']
const EXPLAINER = '天气信号分布：P 是模型胜率，EV 收益 = 模型胜率 / 买入价 - 1。'

function getBucket(edge: number): string {
  const pct = Math.abs(edge) * 100
  if (pct < 25) return '0-25%'
  if (pct < 50) return '25-50%'
  if (pct < 100) return '50-100%'
  if (pct < 200) return '100-200%'
  if (pct < 500) return '200-500%'
  return '500%+'
}

const CustomTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload || !payload.length) return null
  return (
    <div className="border border-neutral-800 bg-neutral-900 px-2 py-1.5">
      <p className="mb-1 text-[10px] text-neutral-400">{label}</p>
      {payload.map((p: any) => (
        <p key={p.name} className="text-[10px] tabular-nums" style={{ color: p.color }}>
          {p.name}: {p.value}
        </p>
      ))}
    </div>
  )
}

export function EdgeDistribution({ weatherSignals }: Props) {
  const data = useMemo(() => {
    const counts: Record<string, number> = {}
    BUCKETS.forEach(bucket => { counts[bucket] = 0 })

    weatherSignals.forEach(signal => {
      counts[getBucket(signal.edge)]++
    })

    return BUCKETS.map(bucket => ({
      bucket,
      Weather: counts[bucket],
    }))
  }, [weatherSignals])

  const total = weatherSignals.length
  const actionable = weatherSignals.filter(signal => signal.actionable).length
  const simulated = weatherSignals.filter(signal => signal.status === 'simulated' || signal.status === 'bought').length
  const paperOpen = weatherSignals.filter(signal => signal.paper_position).length
  const activeBuckets = data.filter(row => row.Weather > 0).length

  if (total === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center px-3 text-center text-[10px] leading-relaxed text-neutral-600">
        <div>暂无可统计信号</div>
        <div className="mt-2 text-[9px]">{EXPLAINER}</div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <div className="grid grid-cols-4 gap-1 px-1 pb-1 text-[9px]">
        <div className="border border-neutral-800 px-1 py-0.5">
          <div className="text-neutral-600">总数</div>
          <div className="tabular-nums text-neutral-200">{total}</div>
        </div>
        <div className="border border-neutral-800 px-1 py-0.5">
          <div className="text-neutral-600">可操作</div>
          <div className="tabular-nums text-green-400">{actionable}</div>
        </div>
        <div className="border border-neutral-800 px-1 py-0.5">
          <div className="text-neutral-600">已标记</div>
          <div className="tabular-nums text-amber-400">{simulated}</div>
        </div>
        <div className="border border-neutral-800 px-1 py-0.5">
          <div className="text-neutral-600">持仓</div>
          <div className="tabular-nums text-cyan-400">{paperOpen}</div>
        </div>
      </div>
      <div className="px-1 pb-1 text-[9px] text-neutral-600">
        {EXPLAINER} 当前信号分布在 {activeBuckets} 个档位。
      </div>
      <div className="min-h-0 flex-1">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 8, left: -10, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1a1a1a" vertical={false} />
            <XAxis
              dataKey="bucket"
              stroke="#525252"
              fontSize={9}
              tickLine={false}
              axisLine={false}
              fontFamily="JetBrains Mono"
            />
            <YAxis
              stroke="#525252"
              fontSize={9}
              tickLine={false}
              axisLine={false}
              allowDecimals={false}
              fontFamily="JetBrains Mono"
            />
            <Tooltip content={<CustomTooltip />} />
            <Bar dataKey="Weather" name="天气" fill="#06b6d4" radius={[2, 2, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
