export function cn(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(' ')
}

export const buttonClass = 'inline-flex min-h-10 cursor-pointer items-center justify-center gap-2 rounded-[10px] border border-line bg-panel px-3.5 py-0 text-[13px] font-semibold leading-none text-secondary transition-[background-color,border-color,color,box-shadow] duration-150 hover:border-line-strong hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-[.38] focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-accent/20'
export const primaryButtonClass = cn(buttonClass, '!border-accent-line !bg-accent-soft font-bold !text-accent-soft-ink hover:!border-accent hover:!bg-accent-soft-hover')
export const dangerButtonClass = cn(buttonClass, '!bg-transparent !text-danger-soft-ink hover:!border-danger/60 hover:!bg-danger/15 hover:!text-danger-soft-ink')
export const iconButtonClass = cn(buttonClass, '!size-9 !min-h-9 !p-0')

export const inputClass = 'h-10 w-full rounded-[10px] border border-line bg-surface px-3.5 py-0 text-[13px] leading-normal text-ink outline-none transition-[background-color,border-color,box-shadow] duration-150 placeholder:text-disabled hover:border-line-strong focus:border-accent focus:ring-3 focus:ring-accent/20 read-only:cursor-default read-only:bg-panel read-only:text-muted disabled:cursor-not-allowed disabled:opacity-50'
export const textareaClass = cn(inputClass, 'h-auto min-h-20 resize-none py-3')

export const pageClass = 'h-full overflow-auto px-[max(36px,calc((100vw-1280px)/2))] pb-20 pt-11 max-[1200px]:px-7 max-sm:px-4 max-sm:pt-6'
export const eyebrowClass = 'text-[11px] font-semibold tracking-[1.2px] text-muted'

export const scrollbarClass = 'app-scrollbars'
