import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Toaster } from '@/components/ui/sonner'
import { loadModalities } from '@/core/registry'
import '@/core/builtin'
import App from '@/core/App'
import { ConfirmProvider } from '@/core/components/ConfirmDialog'
import './index.css'

document.documentElement.classList.add('dark')

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5_000, refetchOnWindowFocus: false, retry: 1 } },
})

loadModalities().then(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <TooltipProvider>
            <ConfirmProvider>
              <App />
            </ConfirmProvider>
            <Toaster theme="dark" position="top-right" richColors closeButton />
          </TooltipProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </StrictMode>,
  )
})
