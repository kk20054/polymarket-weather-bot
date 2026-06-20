import { useMemo } from 'react'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import type { Signal, WeatherSignal } from '../types'

interface Props {
  btcSignals: Signal[]
  weatherSignals: WeatherSignal[]
}

const BUCKETS = ['0-25%', '25-50%', '50-100%', '100-200%', '200-500%', '500%+']

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
    <div className="bg-neutral-900 border border-neutral-800 px-2 py-1.5">
      <p className="text-[10px] text-neutral-400 mb-1">{label}</p>
      {payload.map((p: any) => (
        <p key={p.name} className="text-[10px] tabular-nums" style={{ color: p.color }}>
          {p.name}: {p.value}
        </p>
      ))}
    </div>
  )
}

export function EdgeDistribution({ btcSignals, weatherSignals }: Props) {
  const data = useMemo(() => {
    const counts: Record<string, { btc: number; weather: number }> = {}
    BUCKETS.forEach(b => { counts[b] = { btc: 0, weather: 0 } })

    btcSignals.forEach(s => {
      const bucket = getBucket(s.edge)
      counts[bucket].btc++
    })

    weatherSignals.forEach(s => {
      const bucket = getBucket(s.edge)
      counts[bucket].weather++
    })

    return BUCKETS.map(bucket => ({
      bucket,
      BTC: counts[bucket].btc,
      Weather: counts[bucket].weather,
    }))
  }, [btcSignals, weatherSignals])

  const total = btcSignals.length + weatherSignals.length
  const actionable = weatherSignals.filter(s => s.actionable).length + btcSignals.filter(s => s.actionable).length
  const simulated = weatherSignals.filter(s => s.status === 'simulated' || s.status === 'bought').length
  const paperOpen = weatherSignals.filter(s => s.paper_position).length
  const activeBuckets = data.filter(row => row.BTC + row.Weather > 0).length
  if (total === 0) {
    return (
      <div className="h-full flex items-center justify-center text-neutral-600 text-[10px]">
        暂无可统计信号
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col">
      <div className="grid grid-cols-4 gap-1 px-1 pb-1 text-[9px]">
        <div className="border border-neutral-800 px-1 py-0.5">
          <div className="text-neutral-600">总数</div>
          <div className="text-neutral-200 tabular-nums">{total}</div>
        </div>
        <div className="border border-neutral-800 px-1 py-0.5">
          <div className="text-neutral-600">可操作</div>
          <div className="text-green-400 tabular-nums">{actionable}</div>
        </div>
        <div className="border border-neutral-800 px-1 py-0.5">
          <div className="text-neutral-600">已标记</div>
          <div className="text-amber-400 tabular-nums">{simulated}</div>
        </div>
        <div className="border border-neutral-800 px-1 py-0.5">
          <div className="text-neutral-600">纸面</div>
          <div className="text-cyan-400 tabular-nums">{paperOpen}</div>
        </div>
      </div>
      <div className="px-1 pb-1 text-[9px] text-neutral-600">
        BTC 是比特币信号，天气(WX) 是 weather 信号；P 是模型胜率。
        EV收益 = 模型胜率 / 买入价 - 1；
        当前信号分布在 {activeBuckets} 个档位，若只见一根柱，说明信号集中或前端未刷新。
      </div>
      <div className="flex-1 min-h-0">
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
          <Legend
            iconSize={8}
            wrapperStyle={{ fontSize: '9px', fontFamily: 'JetBrains Mono' }}
          />
          <Bar dataKey="BTC" stackId="a" fill="#d97706" radius={[0, 0, 0, 0]} />
          <Bar dataKey="Weather" name="天气(WX)" stackId="a" fill="#06b6d4" radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
      </div>
    </div>
  )
}
