import { CloudSun, RefreshCw, Unplug } from 'lucide-react'

type DashboardStartupProps = {
  theme: 'light' | 'dark'
  language: 'zh' | 'en'
  failed?: boolean
  retrying?: boolean
  onRetry?: () => void
}

export function DashboardStartup({ theme, language, failed = false, retrying = false, onRetry }: DashboardStartupProps) {
  const zh = language === 'zh'
  const loading = !failed || retrying
  const status = loading
    ? (zh ? '正在载入看板' : 'Loading dashboard')
    : (zh ? '暂时无法连接数据服务' : 'Data service unavailable')

  return (
    <main className={`dashboard-startup polywx-${theme}`}>
      <section className="dashboard-startup-content" aria-labelledby="startup-title">
        <div className="dashboard-startup-brand">
          <CloudSun className="dashboard-startup-weather" size={42} strokeWidth={1.4} aria-hidden="true" />
          <div>
            <h1 id="startup-title">WeatherBot</h1>
            <p>{zh ? '天气市场看板' : 'Weather market dashboard'}</p>
          </div>
        </div>
        <div className="dashboard-startup-track" data-loading={loading} aria-hidden="true">
          {loading && <span />}
        </div>
        <div className="dashboard-startup-status" role="status" aria-live="polite">
          {!loading && <Unplug size={14} aria-hidden="true" />}
          <span>{status}</span>
        </div>
        {failed && (
          <button className="dashboard-startup-retry" type="button" onClick={onRetry} disabled={retrying}>
            <RefreshCw size={14} aria-hidden="true" />
            {retrying ? (zh ? '正在重连' : 'Reconnecting') : (zh ? '重新连接' : 'Reconnect')}
          </button>
        )}
      </section>
    </main>
  )
}
