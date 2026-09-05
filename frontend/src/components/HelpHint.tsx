import { useId } from 'react'
import { HelpCircle } from 'lucide-react'

export function HelpHint({ label, children }: { label: string; children: string }) {
  const id = useId()
  return (
    <span className="group relative inline-flex shrink-0 align-middle">
      <button type="button" aria-label={label} aria-describedby={id}
        onClick={event => { event.preventDefault(); event.stopPropagation() }}
        className="inline-flex h-6 w-6 items-center justify-center text-neutral-500 hover:text-neutral-200 focus-visible:outline focus-visible:outline-1 focus-visible:outline-blue-500">
        <HelpCircle className="h-3.5 w-3.5" />
      </button>
      <span id={id} role="tooltip" className="weatherbot-tooltip pointer-events-none invisible absolute left-0 top-full z-[80] w-60 max-w-[75vw] border p-2 text-left text-[11px] font-normal leading-relaxed opacity-0 shadow-lg group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100">
        {children}
      </span>
    </span>
  )
}
