import { useEffect, useState } from 'react'
import { DeveloperSettingsPanel } from '../components/DeveloperSettingsDrawer'

type ThemeMode = 'light' | 'dark'

export default function DeveloperPage() {
  const [themeMode] = useState<ThemeMode>(() => {
    if (typeof window === 'undefined') return 'dark'
    return window.localStorage.getItem('weatherbot-ui-theme') === 'light' ? 'light' : 'dark'
  })

  useEffect(() => {
    document.documentElement.dataset.theme = themeMode
    const background = themeMode === 'dark' ? '#161A22' : '#ffffff'
    document.documentElement.style.backgroundColor = background
    document.body.style.backgroundColor = background
  }, [themeMode])

  return (
    <div className={`${themeMode === 'dark' ? 'polywx-dark bg-[#161A22] text-[#CBD2DC]' : 'polywx-light bg-white text-gray-900'} min-h-screen p-0 sm:p-4`}>
      <div className="mx-auto flex min-h-screen max-w-[980px] flex-col border-neutral-800 sm:min-h-[calc(100vh-32px)] sm:border">
        <DeveloperSettingsPanel themeMode={themeMode} standalone onClose={() => { window.location.href = '/' }} />
      </div>
    </div>
  )
}
