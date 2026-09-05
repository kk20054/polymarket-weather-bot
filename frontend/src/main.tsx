import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
const DeveloperPage = React.lazy(() => import('./pages/DeveloperPage'))
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Only changing resources opt into polling; settings should not poll by default.
      staleTime: 10000,
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <React.Suspense fallback={<div role="status" className="p-4 text-sm text-neutral-500">加载中...</div>}>
        {window.location.pathname.startsWith('/developer') ? <DeveloperPage /> : <App />}
      </React.Suspense>
    </QueryClientProvider>
  </React.StrictMode>,
)
