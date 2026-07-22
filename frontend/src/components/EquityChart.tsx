import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { EquityPoint } from '../types'

interface Props {
  data: EquityPoint[]
  initialBankroll: number
  language?: 'zh' | 'en'
}

function formatTime(value: string, language: 'zh' | 'en') {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString(language === 'zh' ? 'zh-CN' : 'en-US', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function EquityTooltip({ active, payload, language }: { active?: boolean; payload?: Array<{ payload?: EquityPoint }>; language: 'zh' | 'en' }) {
  const point = payload?.[0]?.payload
  if (!active || !point) return null
  return (
    <div className="border border-neutral-700 bg-neutral-950 px-2.5 py-2 text-[10px] shadow-xl">
      <div className="text-neutral-500">{formatTime(point.timestamp, language)}</div>
      <div className="mt-1 flex min-w-32 items-center justify-between gap-4">
        <span className="text-neutral-400">{language === 'zh' ? '账户权益' : 'Equity'}</span>
        <span className="font-medium tabular-nums text-neutral-100">${Number(point.bankroll).toFixed(2)}</span>
      </div>
      <div className="mt-0.5 flex items-center justify-between gap-4">
        <span className="text-neutral-400">{language === 'zh' ? '累计盈亏' : 'Total PnL'}</span>
        <span className={`font-medium tabular-nums ${Number(point.pnl) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
          {Number(point.pnl) > 0 ? '+' : ''}${Number(point.pnl).toFixed(2)}
        </span>
      </div>
    </div>
  )
}

export function EquityChart({ data, initialBankroll, language = 'zh' }: Props) {
  if (!data.length) {
    return (
      <div className="flex h-full items-center justify-center text-[10px] text-neutral-600">
        {language === 'zh' ? '暂无可估值的订单轨迹' : 'No mark-to-market history yet'}
      </div>
    )
  }

  const chartData = data.map(point => ({ ...point, label: formatTime(point.timestamp, language) }))
  const latestPnl = Number(chartData[chartData.length - 1]?.pnl ?? 0)
  const values = chartData.map(point => Number(point.bankroll))
  const minimum = Math.min(initialBankroll, ...values)
  const maximum = Math.max(initialBankroll, ...values)
  const padding = Math.max(0.15, (maximum - minimum) * 0.2)
  const color = latestPnl >= 0 ? '#22c55e' : '#ef4444'
  const gradientId = latestPnl >= 0 ? 'paper-equity-positive' : 'paper-equity-negative'

  return (
    <div className="h-full" role="img" aria-label={language === 'zh' ? '模拟账户按最新买一价估值的资金曲线' : 'Paper account equity marked at the latest best bid'}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={chartData} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={color} stopOpacity={0.24} />
              <stop offset="95%" stopColor={color} stopOpacity={0.01} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="currentColor" className="text-neutral-800" vertical={false} />
          <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={36} tick={{ fill: '#737373', fontSize: 9 }} />
          <YAxis
            domain={[minimum - padding, maximum + padding]}
            tickLine={false}
            axisLine={false}
            tickFormatter={value => `$${Number(value).toFixed(0)}`}
            tick={{ fill: '#737373', fontSize: 9 }}
          />
          <Tooltip content={<EquityTooltip language={language} />} />
          <ReferenceLine y={initialBankroll} stroke="#525252" strokeDasharray="4 4" />
          <Area type="stepAfter" dataKey="bankroll" stroke={color} strokeWidth={1.5} fill={`url(#${gradientId})`} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
