import type { CalibrationSummary } from '../types'

interface Props {
  calibration: CalibrationSummary
}

export function CalibrationPanel({ calibration }: Props) {
  const settled = calibration.total_with_outcome
  const tracked = calibration.total_signals
  const settlementRate = calibration.settlement_rate ?? (tracked > 0 ? settled / tracked : 0)
  const hasOutcome = settled > 0
  const accuracyPct = hasOutcome ? (calibration.accuracy * 100).toFixed(0) : '--'
  const accuracyColor = !hasOutcome
    ? '#a1a1aa'
    : calibration.accuracy >= 0.55 ? '#22c55e' : calibration.accuracy < 0.50 ? '#dc2626' : '#a1a1aa'

  const brierLabel = !hasOutcome
    ? '等待'
    : calibration.brier_score <= 0.20 ? '好' : calibration.brier_score <= 0.25 ? '一般' : '差'
  const brierColor = !hasOutcome
    ? '#a1a1aa'
    : calibration.brier_score <= 0.20 ? '#22c55e' : calibration.brier_score <= 0.25 ? '#d97706' : '#dc2626'

  const predEdge = (calibration.avg_predicted_edge * 100).toFixed(1)
  const actualEdge = (calibration.avg_actual_edge * 100).toFixed(1)

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-3 gap-1 text-[10px]">
        <div className="border border-neutral-800 px-1 py-1">
          <div className="text-neutral-600">准确率</div>
          <div className="text-lg font-bold tabular-nums" style={{ color: accuracyColor }}>
            {accuracyPct}{hasOutcome ? '%' : ''}
          </div>
          <div className="text-[9px] text-neutral-600 tabular-nums">
            {hasOutcome ? `${Math.round(calibration.accuracy * settled)}/${settled}` : '无结算'}
          </div>
        </div>
        <div className="border border-neutral-800 px-1 py-1">
          <div className="text-neutral-600">结算率</div>
          <div className="text-lg font-bold tabular-nums text-neutral-200">
            {(settlementRate * 100).toFixed(0)}%
          </div>
          <div className="text-[9px] text-neutral-600 tabular-nums">{settled}/{tracked}</div>
        </div>
        <div className="border border-neutral-800 px-1 py-1">
          <div className="text-neutral-600">Brier</div>
          <div className="text-lg font-bold tabular-nums" style={{ color: brierColor }}>
            {hasOutcome ? calibration.brier_score.toFixed(3) : '--'}
          </div>
          <div className="text-[9px] text-neutral-600">{brierLabel}</div>
        </div>
      </div>

      <div className="space-y-1">
        <div className="flex items-center gap-2 text-[10px]">
          <span className="text-neutral-500 w-12 shrink-0">预测EV</span>
          <div className="flex-1 meter-bar">
            <div
              className="meter-fill"
              style={{
                width: `${Math.min(100, Math.abs(calibration.avg_predicted_edge) * 200)}%`,
                backgroundColor: '#d97706'
              }}
            />
          </div>
          <span className="tabular-nums text-amber-500 w-12 text-right">{predEdge}%</span>
        </div>
        <div className="flex items-center gap-2 text-[10px]">
          <span className="text-neutral-500 w-12 shrink-0">实际EV</span>
          <div className="flex-1 meter-bar">
            <div
              className="meter-fill"
              style={{
                width: `${Math.min(100, Math.abs(calibration.avg_actual_edge) * 200)}%`,
                backgroundColor: calibration.avg_actual_edge >= 0 ? '#22c55e' : '#dc2626'
              }}
            />
          </div>
          <span
            className="tabular-nums w-12 text-right"
            style={{ color: calibration.avg_actual_edge >= 0 ? '#22c55e' : '#dc2626' }}
          >
            {actualEdge}%
          </span>
        </div>
      </div>
    </div>
  )
}
